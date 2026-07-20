import json
from datetime import UTC, datetime

import pytest

from zotero_arxiv_daily.analysis.analyzer import AnalysisDependencies
from zotero_arxiv_daily.analysis.cache import AnalysisCache
from zotero_arxiv_daily.analysis.client import AnalysisClientError
from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBatchResult,
    DocumentIssue,
    PaperDocumentResult,
)
from zotero_arxiv_daily.analysis.paper_schemas import PaperLinks
from zotero_arxiv_daily.pipeline.analysis import (
    build_analysis_batch,
    build_production_analysis_pipeline,
)
from tests.analysis.test_analyzer import analyzer_settings, valid_draft
from tests.documents.test_evidence import document_graph
from tests.documents.test_selection import batch


NOW = datetime(2026, 7, 20, tzinfo=UTC)


class DynamicFakeClient:
    model_identity = "fake:stage3-pipeline"

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        payload = json.loads(request.user_prompt)
        paper = payload["paper"]
        evidence = payload["evidence_packet"]["evidence_candidates"]
        text_id = next(
            item["evidence_id"]
            for item in evidence
            if item["kind"] == "text" and not item["abstract_only"]
        )
        visual_id = next(
            item["evidence_id"] for item in evidence if item["kind"] != "text"
        )
        draft = valid_draft().model_copy(
            update={
                "paper_id": paper["paper_id"],
                "english_title": paper["english_title"],
                "links": PaperLinks.model_validate(paper["links"]),
            }
        )
        response = draft.model_dump_json()
        source_evidence = json.loads(valid_draft().model_dump_json())
        source_text = source_evidence["chinese_title"]["evidence_ids"][0]
        source_visual = source_evidence["supporting_visuals"][0]["evidence_id"]
        return response.replace(source_text, text_id).replace(source_visual, visual_id)


class RetryOnceClient(DynamicFakeClient):
    def generate(self, request):
        if self.calls == 0:
            self.calls += 1
            raise AnalysisClientError("analysis_timeout", retryable=True)
        return super().generate(request)


def dependencies(tmp_path):
    return AnalysisDependencies(
        client=DynamicFakeClient(),
        cache=AnalysisCache(tmp_path / "cache"),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )


def document_result(paper_id, *, status="success", document=None):
    if status in {"success", "partial"}:
        if document is None:
            graph = document_graph()
            expected_url = f"https://arxiv.org/pdf/{paper_id.removeprefix('arxiv:')}"
            graph = graph.model_copy(
                update={"pdf": graph.pdf.model_copy(update={"source_url": expected_url})}
            )
        else:
            graph = document
        return PaperDocumentResult(
            paper_id=paper_id,
            status=status,
            document=graph,
            processing_seconds=0,
        )
    return PaperDocumentResult(
        paper_id=paper_id,
        status=status,
        document=None,
        issues=(
            DocumentIssue(code="synthetic_failure", severity="error", message="fixture"),
        ),
        processing_seconds=0,
    )


def document_batch(candidate_batch, results):
    return DocumentBatchResult(
        run_id=candidate_batch.run_id,
        created_at=NOW,
        results=tuple(results),
    )


def test_pipeline_analyzes_only_selected_documents_in_order_and_at_most_five(tmp_path):
    candidates = batch(count=8, selected=5)
    documents = document_batch(
        candidates,
        (
            document_result(paper.paper_id)
            for paper in candidates.candidates
        ),
    )
    deps = dependencies(tmp_path)
    result = build_analysis_batch(candidates, documents, analyzer_settings(), deps)
    assert [item.paper_id for item in result.results] == list(
        candidates.selected_for_full_analysis[:5]
    )
    assert deps.client.calls == result.expensive_call_count == 5


def test_pipeline_isolates_missing_failed_and_mismatched_documents(tmp_path):
    candidates = batch(count=4, selected=4)
    mismatch = document_graph().model_copy(
        update={
            "pdf": document_graph().pdf.model_copy(
                update={"source_url": "https://arxiv.org/pdf/9999.99999"}
            )
        }
    )
    documents = document_batch(
        candidates,
        (
            document_result(candidates.candidates[0].paper_id),
            document_result(candidates.candidates[2].paper_id, status="failed"),
            document_result(candidates.candidates[3].paper_id, document=mismatch),
        ),
    )
    result = build_analysis_batch(
        candidates, documents, analyzer_settings(), dependencies(tmp_path)
    )
    assert [item.status for item in result.results] == [
        "success",
        "skipped",
        "failed",
        "failed",
    ]
    assert [item.issues[0].code for item in result.results[1:]] == [
        "analysis_document_missing",
        "analysis_document_failed",
        "analysis_document_mismatch",
    ]


def test_pipeline_rejects_a_different_document_batch_run(tmp_path):
    candidates = batch(count=1, selected=1)
    documents = DocumentBatchResult(
        run_id="20260721T000000Z",
        created_at=NOW,
        results=(document_result(candidates.candidates[0].paper_id),),
    )
    with pytest.raises(ValueError, match="run_id"):
        build_analysis_batch(candidates, documents, analyzer_settings(), dependencies(tmp_path))


def test_pipeline_counts_an_expensive_paper_once_even_when_client_retries(tmp_path):
    candidates = batch(count=1, selected=1)
    documents = document_batch(
        candidates,
        (document_result(candidates.candidates[0].paper_id),),
    )
    deps = dependencies(tmp_path)
    deps.client = RetryOnceClient()
    result = build_analysis_batch(candidates, documents, analyzer_settings(), deps)
    assert result.results[0].status == "success"
    assert deps.client.calls == 2
    assert result.expensive_call_count == 1


def test_pipeline_rejects_duplicate_stage_two_paper_results(tmp_path):
    candidates = batch(count=1, selected=1)
    item = document_result(candidates.candidates[0].paper_id)
    documents = document_batch(candidates, (item, item))
    with pytest.raises(ValueError, match="duplicate"):
        build_analysis_batch(candidates, documents, analyzer_settings(), dependencies(tmp_path))


def test_production_factory_uses_environment_only_on_explicit_construction(tmp_path):
    captured = {}

    def client_factory(**kwargs):
        captured.update(kwargs)
        return DynamicFakeClient()

    config_dir = __import__("pathlib").Path(__file__).parents[2] / "config"
    settings, deps = build_production_analysis_pipeline(
        config_dir,
        environ={
            "LLM_API_KEY": "fake-key-for-composition-only",
            "LLM_BASE_URL": "https://llm.example.test/v1",
            "LLM_MODEL": "fake-model",
        },
        client_factory=client_factory,
        cache_root=tmp_path / "analysis-cache",
    )
    assert settings.evidence.max_visuals == 3
    assert settings.evidence.max_candidates == 48
    assert captured["api_key"] == "fake-key-for-composition-only"
    assert captured["read_timeout"] == 60
    assert captured["response_max_bytes"] == 1048576
    assert deps.cache.root == tmp_path / "analysis-cache"
    assert not deps.cache.root.exists()


def test_production_factory_reports_only_missing_environment_variable_names(tmp_path):
    config_dir = __import__("pathlib").Path(__file__).parents[2] / "config"
    with pytest.raises(RuntimeError) as captured:
        build_production_analysis_pipeline(
            config_dir,
            environ={"LLM_API_KEY": "fake-present-value"},
            cache_root=tmp_path / "cache",
        )
    message = str(captured.value)
    assert "LLM_BASE_URL" in message
    assert "LLM_MODEL" in message
    assert "fake-present-value" not in message
