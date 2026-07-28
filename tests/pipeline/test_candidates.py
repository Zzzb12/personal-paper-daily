import json
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.analysis.schemas import CandidatePaper, InterestPaper
from zotero_arxiv_daily.candidates.ranking import CandidateRanker, EmbeddingIdentity
from zotero_arxiv_daily.viewer.feedback import InterestFeedbackProjection
from zotero_arxiv_daily.interest.base import InterestReadResult
from zotero_arxiv_daily.pipeline.candidates import (
    CandidatePipelineDependencies,
    CandidatePipelineSettings,
    EmptyInterestCorpusError,
    build_candidate_batch,
    build_production_pipeline,
    main,
)
from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivMetadataResult


NOW = datetime(2026, 7, 20, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "stage1_offline.json"


class InterestProvider:
    def __init__(self, papers):
        self.papers = papers

    def read(self):
        return InterestReadResult(
            papers=self.papers, corpus_fingerprint="b" * 64,
            eligible_count=len(self.papers), excluded_count=2, invalid_count=1, issues=(),
        )


class MetadataRetriever:
    def __init__(self, papers):
        self.papers = papers

    def retrieve(self):
        return ArxivMetadataResult(
            candidates=self.papers, retrieved_count=len(self.papers) + 2,
            deduplicated_count=len(self.papers), invalid_count=1,
        )


class DeterministicEmbeddings:
    identity = EmbeddingIdentity.from_settings(
        provider="fixture", implementation_version="1", model="v1", task="retrieval",
        settings={}, dimension=2, dtype="float64",
    )

    def encode(self, texts):
        return np.asarray([[float(index + 1), 1.0] for index, _ in enumerate(texts)])


def interest():
    return InterestPaper(
        paper_id="zotero:zotero-item-key", title="Interest", abstract="Seed",
        collection_paths=("PaperDaily/00-Seeds/Test",), added_at=NOW,
    )


def candidate(index):
    arxiv_id = f"2401.{index:05d}"
    return CandidatePaper(
        paper_id=f"arxiv:{arxiv_id}", arxiv_id=arxiv_id, version=1,
        title=f"Candidate {index}", authors=("Author",), abstract=f"Abstract {index}",
        categories=("cs.CV",), primary_category="cs.CV", published_at=NOW,
        updated_at=NOW, arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


def dependencies(store=None, papers=None, interests=None):
    papers = papers or tuple(candidate(index) for index in range(1, 36))
    interests = (interest(),) if interests is None else interests
    return CandidatePipelineDependencies(
        interest_provider=InterestProvider(interests), arxiv_retriever=MetadataRetriever(papers),
        ranker=CandidateRanker(DeterministicEmbeddings()), store=store or Mock(),
        run_id_factory=lambda _: "20260720T000000Z",
    )


def test_offline_pipeline_enforces_limits_and_privacy():
    batch = build_candidate_batch(
        CandidatePipelineSettings(dry_run=True), dependencies(), lambda: NOW
    )
    assert len(batch.candidates) == 30
    assert len(batch.selected_for_llm) == 15
    assert len(batch.selected_for_full_analysis) == 5
    assert batch.counts.model_dump() == {"retrieved": 37, "deduplicated": 35, "invalid": 1, "excluded": 2}
    serialized = batch.to_deterministic_json()
    assert "zotero-item-key" not in serialized
    assert "PaperDaily/" not in serialized
    assert '"llm_score": null' in serialized


def test_dry_run_does_not_write():
    store = Mock()
    build_candidate_batch(
        CandidatePipelineSettings(dry_run=True), dependencies(store=store), lambda: NOW
    )
    store.write.assert_not_called()


def test_non_dry_run_writes_validated_batch():
    store = Mock()
    batch = build_candidate_batch(
        CandidatePipelineSettings(dry_run=False), dependencies(store=store), lambda: NOW
    )
    store.write.assert_called_once_with(batch)


def test_pipeline_persists_and_honors_smaller_selection_limits():
    batch = build_candidate_batch(
        CandidatePipelineSettings(
            candidate_pool_size=10, llm_rerank_limit=7, full_analysis_limit=3, dry_run=True
        ),
        dependencies(),
        lambda: NOW,
    )
    assert batch.limits.model_dump() == {
        "candidate_pool_size": 10, "llm_rerank_limit": 7, "full_analysis_limit": 3
    }
    assert (len(batch.candidates), len(batch.selected_for_llm), len(batch.selected_for_full_analysis)) == (10, 7, 3)


def test_dry_run_does_not_change_ranking_config_hash():
    dry = build_candidate_batch(CandidatePipelineSettings(dry_run=True), dependencies(), lambda: NOW)
    persistent = build_candidate_batch(
        CandidatePipelineSettings(dry_run=False), dependencies(store=Mock()), lambda: NOW
    )
    assert dry.config_hash == persistent.config_hash


def test_feedback_veto_reaches_candidate_pipeline_before_embedding_or_selection():
    blocked, allowed = candidate(1), candidate(2)

    class RecordingEmbeddings(DeterministicEmbeddings):
        def __init__(self):
            self.calls = []

        def encode(self, texts):
            self.calls.append(tuple(texts))
            return super().encode(texts)

    provider = RecordingEmbeddings()
    deps = dependencies(papers=(blocked, allowed))
    deps.ranker = CandidateRanker(provider)
    deps.feedback = InterestFeedbackProjection(irrelevant_ids=("2401.00001v4",))

    batch = build_candidate_batch(CandidatePipelineSettings(dry_run=True), deps, lambda: NOW)

    assert [paper.paper_id for paper in batch.candidates] == [allowed.paper_id]
    assert all("Candidate 1" not in text for call in provider.calls for text in call)
    assert batch.selected_for_full_analysis == (allowed.paper_id,)


def test_candidate_config_hash_binds_feedback_implementation_and_delta_not_projection_content():
    common = dict(dry_run=True, feedback_favorite_delta=0.05)
    left = CandidatePipelineSettings(**common)
    right = CandidatePipelineSettings(**common)
    changed = CandidatePipelineSettings(dry_run=True, feedback_favorite_delta=0.10)

    assert left.config_hash() == right.config_hash()
    assert left.config_hash() != changed.config_hash()


def test_empty_interest_corpus_fails_before_metadata_or_embeddings():
    deps = dependencies(interests=())
    deps.arxiv_retriever = Mock()
    deps.ranker = Mock()
    with pytest.raises(EmptyInterestCorpusError):
        build_candidate_batch(CandidatePipelineSettings(), deps, lambda: NOW)
    deps.arxiv_retriever.retrieve.assert_not_called()
    deps.ranker.rank.assert_not_called()


def test_offline_fixture_cli_prints_safe_summary_without_network(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("httpx.Client", Mock(side_effect=AssertionError("network forbidden")))
    assert main(["--dry-run", "--offline-fixture", str(FIXTURE)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "schema_version": "1.0",
        "counts": {"retrieved": 2, "deduplicated": 2, "invalid": 0, "excluded": 1},
        "candidate_count": 2,
        "llm_count": 2,
        "full_analysis_count": 2,
    }
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "cache").exists()


def test_production_factory_wires_real_boundaries_from_config_and_environment(monkeypatch):
    calls = {}
    zotero_gateway = Mock()
    arxiv_gateway = Mock()
    embedding_provider = DeterministicEmbeddings()
    embedding_provider.close = Mock()

    def zotero_factory(library_id, api_key, **kwargs):
        calls["zotero"] = (library_id, api_key, kwargs)
        return zotero_gateway

    def arxiv_factory(**kwargs):
        calls["arxiv"] = kwargs
        return arxiv_gateway

    def embedding_factory(**kwargs):
        calls["embedding"] = kwargs
        return embedding_provider

    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.candidates.PyzoteroGateway.from_credentials", zotero_factory
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.candidates.HttpArxivMetadataGateway.from_defaults", arxiv_factory
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.candidates.TransformersMeanPoolingEmbeddingProvider",
        embedding_factory,
    )
    settings, deps = build_production_pipeline(
        Path(__file__).parents[2] / "config",
        environ={"ZOTERO_ID": "synthetic-id", "ZOTERO_KEY": "synthetic-key"},
        dry_run=False,
    )
    assert settings.categories == ("cs.CV", "cs.LG", "cs.AI")
    assert settings.include_paths == (
        "PaperDaily/00-Seeds/**", "PaperDaily/03-Read/**", "PaperDaily/04-Favorite/**"
    )
    assert calls["zotero"][0:2] == ("synthetic-id", "synthetic-key")
    assert calls["embedding"]["model"] == "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
    assert calls["embedding"]["revision"] == "b207367332321f8e44f96e224ef15bc607f4dbf0"
    assert calls["embedding"]["cache_folder"] == Path("models/reranker")
    assert calls["embedding"]["task"] == "retrieval"
    assert calls["embedding"]["trust_remote_code"] is False
    assert calls["embedding"]["prompt_name"] is None
    assert calls["embedding"]["encode_kwargs"] == {"normalize_embeddings": True}
    assert calls["embedding"]["max_sequence_length"] == 512
    assert deps.store.root == Path("data/candidates")
    assert deps.ranker.provider.cache.root == Path("cache/embeddings")
    offset_time = datetime(2026, 7, 20, 8, tzinfo=timezone(timedelta(hours=8)))
    assert deps.run_id_factory(offset_time) == "20260720T000000Z"
    deps.close()
    deps.close()
    embedding_provider.close.assert_called_once()
    zotero_gateway.close.assert_called_once()
    arxiv_gateway.close.assert_called_once()


def test_production_dry_run_does_not_construct_writable_embedding_cache(monkeypatch):
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.candidates.PyzoteroGateway.from_credentials",
        lambda *args, **kwargs: Mock(close=Mock()),
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.candidates.HttpArxivMetadataGateway.from_defaults",
        lambda **kwargs: Mock(close=Mock()),
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.candidates.TransformersMeanPoolingEmbeddingProvider",
        lambda **kwargs: DeterministicEmbeddings(),
    )
    _, deps = build_production_pipeline(
        Path(__file__).parents[2] / "config",
        environ={"ZOTERO_ID": "synthetic-id", "ZOTERO_KEY": "synthetic-key"},
        dry_run=True,
    )
    try:
        assert deps.ranker.provider.identity.provider == "fixture"
        assert not hasattr(deps.ranker.provider, "cache")
    finally:
        deps.close()


def test_configured_corrupt_feedback_store_fails_before_live_clients_and_without_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    project_config = Path(__file__).parents[2] / "config"
    shutil.copy(project_config / "base.yaml", config_dir / "base.yaml")
    (tmp_path / "private-feedback.json").write_text(
        "private feedback content", encoding="utf-8"
    )
    (config_dir / "custom.yaml").write_text(
        "candidate_pipeline:\n  feedback:\n    store_path: private-feedback.json\n    favorite_delta: 0.05\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.candidates.PyzoteroGateway.from_credentials",
        lambda *args, **kwargs: pytest.fail("clients must not be constructed"),
    )

    with pytest.raises(Exception) as error:
        build_production_pipeline(
            config_dir,
            environ={"ZOTERO_ID": "synthetic-id", "ZOTERO_KEY": "synthetic-key"},
            dry_run=False,
        )

    assert str(error.value) == "feedback projection rejected"
    assert "private feedback content" not in str(error.value)


def test_production_cli_mode_runs_without_offline_fixture(monkeypatch, capsys):
    deps = dependencies()
    deps.close = Mock()
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.candidates.build_production_pipeline",
        lambda config_dir, environ, dry_run: (CandidatePipelineSettings(dry_run=True), deps),
    )
    assert main(["--dry-run", "--config-dir", "config"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == "1.0"
    deps.close.assert_called_once()


def test_config_contains_exact_stage_one_defaults():
    root = Path(__file__).parents[2]
    base = OmegaConf.load(root / "config" / "base.yaml")
    custom = OmegaConf.load(root / "config" / "custom.yaml")
    assert list(base.zotero.include_path) == [
        "PaperDaily/00-Seeds/**", "PaperDaily/03-Read/**", "PaperDaily/04-Favorite/**"
    ]
    assert list(base.zotero.ignore_path) == ["PaperDaily/99-Exclude/**"]
    assert list(base.source.arxiv.category) == ["cs.CV", "cs.LG", "cs.AI"]
    assert OmegaConf.to_container(base.candidate_pipeline, resolve=True) == {
        "candidate_pool_size": 30, "llm_rerank_limit": 15, "full_analysis_limit": 5,
        "output_dir": "data/candidates", "embedding_cache_dir": "cache/embeddings",
        "feedback": {"store_path": None, "favorite_delta": 0.05},
        "request_timeout": {"connect": 10, "read": 30, "write": 10, "pool": 10},
        "retry": {"max_attempts": 3, "backoff_seconds": 1, "max_retry_after_seconds": 60},
    }
    assert list(custom.source.arxiv.category) == ["cs.CV", "cs.LG", "cs.AI"]
