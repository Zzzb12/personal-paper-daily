from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBatchResult,
    PaperDocumentResult,
)
from zotero_arxiv_daily.analysis.paper_schemas import (
    AnalysisBatchResult,
    PaperAnalysisResult,
    PaperLinks,
)
from zotero_arxiv_daily.analysis.schemas import (
    CandidateBatch,
    CandidateCounts,
    CandidateSelectionLimits,
    RankingModelVersions,
    RankingRecord,
)
from zotero_arxiv_daily.analysis.validation_cache import ValidationCache
from zotero_arxiv_daily.pipeline.validation import (
    ValidationDependencies,
    ValidationSettings,
    build_validation_batch,
)
from tests.analysis.stage4_factories import GoldenInputs, golden_inputs


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def _clone(inputs: GoldenInputs, index: int) -> GoldenInputs:
    suffix = f"{index:05d}"
    paper_id = f"arxiv:2401.{suffix}"
    title = f"Synthetic Paper {index}"
    pdf_url = f"https://arxiv.org/pdf/2401.{suffix}"
    arxiv_url = f"https://arxiv.org/abs/2401.{suffix}"
    paper = inputs.candidate.model_copy(
        update={
            "paper_id": paper_id,
            "arxiv_id": f"2401.{suffix}",
            "title": title,
            "pdf_url": pdf_url,
            "arxiv_url": arxiv_url,
        }
    )
    document = inputs.document.model_copy(
        update={
            "content_fingerprint": f"{index:x}"[-1] * 64,
            "pdf": inputs.document.pdf.model_copy(
                update={
                    "source_url": pdf_url,
                    "sha256": f"{index + 5:x}"[-1] * 64,
                }
            ),
        }
    )
    packet_candidates = tuple(
        item.model_copy(update={"paper_id": paper_id})
        for item in inputs.packet.candidates
    )
    packet = inputs.packet.model_copy(
        update={
            "paper_id": paper_id,
            "document_fingerprint": document.content_fingerprint,
            "packet_fingerprint": f"{index + 8:x}"[-1] * 64,
            "candidates": packet_candidates,
        }
    )
    analysis = inputs.analysis_result.analysis.model_copy(
        update={
            "paper_id": paper_id,
            "english_title": title,
            "links": PaperLinks(pdf_url=pdf_url, arxiv_url=arxiv_url, code_url=None),
            "evidence_candidates": packet_candidates,
        }
    )
    analysis_result = inputs.analysis_result.model_copy(
        update={"paper_id": paper_id, "analysis": analysis}
    )
    return GoldenInputs(paper, document, packet, analysis_result)


def _inputs(count: int = 3) -> tuple[GoldenInputs, ...]:
    base = golden_inputs()
    return tuple(_clone(base, index + 1) for index in range(count))


def _candidate_batch(items: tuple[GoldenInputs, ...]) -> CandidateBatch:
    versions = RankingModelVersions(
        provider="fixture",
        model="fixture-v1",
        task="retrieval",
        scorer="cosine",
        embedding_identity_hash="c" * 64,
    )
    return CandidateBatch(
        run_id="run-stage4",
        created_at=NOW,
        retrieved_at=NOW,
        categories=("cs.CV",),
        config_hash="a" * 64,
        interest_corpus_fingerprint="b" * 64,
        candidates=tuple(item.candidate for item in items),
        rankings=tuple(
            RankingRecord(
                paper_id=item.candidate.paper_id,
                embedding_score=1 - index / 10,
                final_score=1 - index / 10,
                rank=index + 1,
                reason="artificial fixture",
                model_versions=versions,
            )
            for index, item in enumerate(items)
        ),
        selected_for_llm=tuple(item.candidate.paper_id for item in items[:15]),
        selected_for_full_analysis=tuple(item.candidate.paper_id for item in items[:5]),
        counts=CandidateCounts(
            retrieved=len(items), deduplicated=len(items), invalid=0, excluded=0
        ),
        limits=CandidateSelectionLimits(),
    )


def _document_batch(items: tuple[GoldenInputs, ...]) -> DocumentBatchResult:
    return DocumentBatchResult(
        run_id="run-stage4",
        created_at=NOW,
        results=tuple(
            PaperDocumentResult(
                paper_id=item.candidate.paper_id,
                status="success",
                document=item.document,
                processing_seconds=0,
            )
            for item in items
        ),
    )


def _analysis_batch(items: tuple[GoldenInputs, ...]) -> AnalysisBatchResult:
    return AnalysisBatchResult(
        run_id="run-stage4",
        created_at=NOW,
        results=tuple(item.analysis_result for item in items),
        expensive_call_count=0,
        cache_hit_count=0,
    )


def _deps(tmp_path: Path) -> ValidationDependencies:
    return ValidationDependencies(
        cache=ValidationCache(tmp_path / "validation"),
        clock=lambda: NOW,
    )


def test_batch_isolates_invalid_middle_paper_and_keeps_order(tmp_path: Path) -> None:
    items = list(_inputs())
    bad = items[1]
    bad_analysis = bad.analysis_result.analysis.model_copy(
        update={"english_title": "Tampered middle title"}
    )
    items[1] = bad._replace(
        analysis_result=bad.analysis_result.model_copy(update={"analysis": bad_analysis})
    )
    values = tuple(items)

    result = build_validation_batch(
        _candidate_batch(values),
        _document_batch(values),
        tuple(item.packet for item in values),
        _analysis_batch(values),
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert [item.paper_id for item in result.results] == [
        item.candidate.paper_id for item in values
    ]
    assert [item.status for item in result.results] == [
        "validated",
        "invalid",
        "validated",
    ]


def test_batch_processes_at_most_five_selected_papers(tmp_path: Path) -> None:
    items = _inputs(6)
    result = build_validation_batch(
        _candidate_batch(items),
        _document_batch(items),
        tuple(item.packet for item in items),
        _analysis_batch(items[:5]),
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert len(result.results) == 5


def test_duplicate_document_fails_only_that_paper(tmp_path: Path) -> None:
    items = _inputs(2)
    documents = _document_batch(items)
    duplicated = documents.model_copy(
        update={"results": (documents.results[0], documents.results[0], documents.results[1])}
    )
    result = build_validation_batch(
        _candidate_batch(items),
        duplicated,
        tuple(item.packet for item in items),
        _analysis_batch(items),
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert [item.status for item in result.results] == ["failed", "validated"]


def test_missing_packet_skips_only_that_paper(tmp_path: Path) -> None:
    items = _inputs(2)
    result = build_validation_batch(
        _candidate_batch(items),
        _document_batch(items),
        (items[1].packet,),
        _analysis_batch(items),
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert [item.status for item in result.results] == ["skipped", "validated"]


def test_batch_cache_hit_and_validator_version_identity(tmp_path: Path) -> None:
    items = _inputs(1)
    args = (
        _candidate_batch(items),
        _document_batch(items),
        tuple(item.packet for item in items),
        _analysis_batch(items),
    )
    dependencies = _deps(tmp_path)
    first = build_validation_batch(*args, ValidationSettings(), dependencies)
    second = build_validation_batch(*args, ValidationSettings(), dependencies)
    changed = build_validation_batch(
        *args,
        ValidationSettings(validator_version="stage4-v2"),
        dependencies,
    )

    assert first.cache_hit_count == 0
    assert second.cache_hit_count == 1
    assert changed.cache_hit_count == 0


def test_run_id_mismatch_is_rejected_before_processing(tmp_path: Path) -> None:
    items = _inputs(1)
    analyses = _analysis_batch(items).model_copy(update={"run_id": "other-run"})
    with pytest.raises(ValueError, match="run_id"):
        build_validation_batch(
            _candidate_batch(items),
            _document_batch(items),
            tuple(item.packet for item in items),
            analyses,
            ValidationSettings(),
            _deps(tmp_path),
        )


def test_validation_config_is_non_secret_and_capped() -> None:
    config = OmegaConf.load(Path("config/base.yaml"))
    assert config.validation_pipeline.validator_version == "stage4-v1"
    assert int(config.validation_pipeline.max_papers) == 5
    assert str(config.validation_pipeline.cache_root) == "cache/validation"
