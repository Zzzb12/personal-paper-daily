from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBatchResult,
    DocumentIssue,
    PaperDocumentResult,
)
from zotero_arxiv_daily.analysis.paper_schemas import AnalysisBatchResult
from zotero_arxiv_daily.delivery.feishu import DigestPolicy
from zotero_arxiv_daily.delivery.ledger import DeliveryLedger
from zotero_arxiv_daily.pipeline.artifacts import ArtifactAuditor, ManifestStore
from zotero_arxiv_daily.pipeline.daily import (
    AnalysisStageOutput,
    DailyDependencies,
    DailySettings,
    PreparedDelivery,
    run_daily,
)
from zotero_arxiv_daily.pipeline.validation import (
    ValidationSettings,
    build_validation_batch,
)
from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
from zotero_arxiv_daily.viewer.schemas import ViewerSettings
from tests.analysis.stage4_factories import GoldenInputs
from tests.pipeline.test_validation import (
    _analysis_batch,
    _candidate_batch,
    _deps as validation_dependencies,
    _document_batch,
    _inputs,
)


NOW = datetime(2026, 7, 22, 2, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def _settings(tmp_path: Path, *, run_id: str = "run-stage7", send: bool = False) -> DailySettings:
    return DailySettings(
        run_id=run_id,
        trigger="local",
        config_hash="a" * 64,
        dry_run=not send,
        send_feishu=send,
        viewer_output=tmp_path / "run" / "viewer",
    )


def _dependencies(
    tmp_path: Path,
    items: tuple[GoldenInputs, ...],
    *,
    document_batch: DocumentBatchResult | None = None,
    analysis_batch: AnalysisBatchResult | None = None,
    fail_viewer: bool = False,
    fail_send: bool = False,
    send_calls: list[str] | None = None,
    ledger: DeliveryLedger | None = None,
    order: list[str] | None = None,
) -> DailyDependencies:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    candidates = _candidate_batch(items)
    documents = document_batch or _document_batch(items)
    analyses = analysis_batch or _analysis_batch(items)
    packets = tuple(item.packet for item in items)
    calls = order if order is not None else []

    def candidate_runner():
        calls.append("candidates")
        return candidates

    def document_runner(received):
        calls.append("documents")
        assert received == candidates
        return documents

    def analysis_runner(received_candidates, received_documents):
        calls.append("analysis")
        assert received_candidates == candidates
        assert received_documents == documents
        return AnalysisStageOutput(batch=analyses, packets=packets)

    def validation_runner(received_candidates, received_documents, received_analysis):
        calls.append("validation")
        return build_validation_batch(
            received_candidates,
            received_documents,
            received_analysis.packets,
            received_analysis.batch,
            ValidationSettings(),
            validation_dependencies(tmp_path),
        )

    def viewer_runner(batch):
        calls.append("viewer")
        if fail_viewer:
            raise RuntimeError("dynamic path and secret must not reach manifest")
        return StaticViewerBuilder(ViewerSettings(output_root=run_root / "viewer")).build(
            batch.results,
            batch_label="stage7-test",
        )

    def prepare_delivery(batch):
        calls.append("prepare_feishu")
        request = DigestPolicy.build(
            batch,
            chat_id="oc_00000000000000000000000000000000",
            site_url="https://papers.example.test/daily/",
        )
        return PreparedDelivery(
            idempotency_key=request.idempotency_key,
            paper_count=len(request.payload.papers),
        )

    def send_delivery(delivery: PreparedDelivery) -> None:
        calls.append("send_feishu")
        if send_calls is not None:
            send_calls.append(delivery.idempotency_key)
        if fail_send:
            raise RuntimeError("https://user:secret@example.test/private response")

    return DailyDependencies(
        candidate_runner=candidate_runner,
        document_runner=document_runner,
        analysis_runner=analysis_runner,
        validation_runner=validation_runner,
        viewer_runner=viewer_runner,
        prepare_delivery=prepare_delivery,
        send_delivery=send_delivery,
        manifest_store=ManifestStore(run_root, run_root / "run-manifest.json"),
        artifact_auditor=ArtifactAuditor(run_root),
        delivery_ledger=ledger or DeliveryLedger(run_root / "delivery-ledger.json"),
        clock=Clock(),
        sleep=lambda _: None,
    )


def test_complete_success_composes_all_stages_and_writes_safe_manifest(tmp_path: Path) -> None:
    items = _inputs(1)
    order: list[str] = []
    dependencies = _dependencies(tmp_path, items, order=order)

    manifest = run_daily(_settings(tmp_path), dependencies)

    assert order == [
        "candidates",
        "documents",
        "analysis",
        "validation",
        "viewer",
        "prepare_feishu",
    ]
    assert manifest.status == "success"
    assert manifest.counts.candidate_count == 1
    assert manifest.counts.validated_count == 1
    assert manifest.counts.published_count == 1
    assert manifest.static_site.status == "success"
    assert manifest.feishu.status == "preview"
    assert manifest.artifact_hash is not None
    assert dependencies.manifest_store.output.is_file()
    serialized = dependencies.manifest_store.output.read_text(encoding="utf-8")
    assert "secret" not in serialized
    assert "prompt" not in serialized


def test_empty_candidates_stop_without_calling_expensive_or_delivery_stages(tmp_path: Path) -> None:
    order: list[str] = []
    dependencies = _dependencies(tmp_path, (), order=order)

    manifest = run_daily(_settings(tmp_path), dependencies)

    assert order == ["candidates"]
    assert manifest.status == "empty"
    assert tuple(stage.status for stage in manifest.stages) == (
        "empty",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
    )
    assert manifest.static_site.status == "skipped"
    assert manifest.feishu.status == "skipped"


def test_one_paper_failure_keeps_successful_paper_and_marks_partial(tmp_path: Path) -> None:
    items = _inputs(2)
    original_documents = _document_batch(items)
    issue = DocumentIssue(
        code="document_fixture_failure",
        severity="error",
        message="fixture document failed",
    )
    failed = PaperDocumentResult(
        paper_id=items[1].candidate.paper_id,
        status="failed",
        document=None,
        issues=(issue,),
        processing_seconds=0,
    )
    documents = original_documents.model_copy(
        update={"results": (original_documents.results[0], failed)}
    )
    analyses = _analysis_batch(items).model_copy(
        update={"results": (_analysis_batch(items).results[0],)}
    )

    manifest = run_daily(
        _settings(tmp_path),
        _dependencies(
            tmp_path,
            items,
            document_batch=documents,
            analysis_batch=analyses,
        ),
    )

    assert manifest.status == "partial"
    assert manifest.counts.candidate_count == 2
    assert manifest.counts.analyzed_count == 1
    assert manifest.counts.validated_count == 1
    assert manifest.counts.published_count == 1
    assert manifest.partial_failure_count >= 1


def test_stage4_invalid_paper_is_not_published_or_delivered(tmp_path: Path) -> None:
    item = _inputs(1)[0]
    analysis = item.analysis_result.analysis.model_copy(update={"english_title": "Tampered"})
    invalid_item = item._replace(
        analysis_result=item.analysis_result.model_copy(update={"analysis": analysis})
    )

    manifest = run_daily(
        _settings(tmp_path),
        _dependencies(tmp_path, (invalid_item,)),
    )

    assert manifest.status == "partial"
    assert manifest.counts.validated_count == 0
    assert manifest.counts.published_count == 0
    assert manifest.counts.delivered_count == 0
    assert manifest.feishu.status == "preview"


def test_static_site_survives_feishu_failure_and_records_targets_separately(tmp_path: Path) -> None:
    manifest = run_daily(
        _settings(tmp_path, send=True),
        _dependencies(tmp_path, _inputs(1), fail_send=True),
    )

    assert manifest.status == "partial"
    assert manifest.static_site.status == "success"
    assert manifest.artifact_hash is not None
    assert (_settings(tmp_path, send=True).viewer_output / "index.html").is_file()
    assert manifest.feishu.status == "failed"
    assert manifest.feishu.error_codes == ("feishu_delivery_failed",)


def test_static_site_failure_skips_feishu_and_never_marks_it_successful(tmp_path: Path) -> None:
    order: list[str] = []
    manifest = run_daily(
        _settings(tmp_path, send=True),
        _dependencies(tmp_path, _inputs(1), fail_viewer=True, order=order),
    )

    assert manifest.status == "failed"
    assert manifest.static_site.status == "failed"
    assert manifest.feishu.status == "skipped"
    assert manifest.artifact_hash is None
    assert "prepare_feishu" not in order
    assert "send_feishu" not in order


def test_persistent_idempotency_key_prevents_duplicate_send_across_runs(tmp_path: Path) -> None:
    ledger = DeliveryLedger(tmp_path / "run" / "delivery-ledger.json")
    send_calls: list[str] = []
    first = run_daily(
        _settings(tmp_path, run_id="run-stage7-first", send=True),
        _dependencies(tmp_path, _inputs(1), send_calls=send_calls, ledger=ledger),
    )
    second = run_daily(
        _settings(tmp_path, run_id="run-stage7-second", send=True),
        _dependencies(tmp_path, _inputs(1), send_calls=send_calls, ledger=ledger),
    )

    assert first.feishu.status == "sent"
    assert second.feishu.status == "duplicate"
    assert len(send_calls) == 1
    assert first.feishu.idempotency_key == second.feishu.idempotency_key
    assert second.counts.delivered_count == 0
