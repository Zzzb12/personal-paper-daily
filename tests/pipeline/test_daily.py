from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from time import sleep

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBatchResult,
    DocumentIssue,
    PaperDocumentResult,
)
from zotero_arxiv_daily.analysis.client import AnalysisRequest
from zotero_arxiv_daily.analysis.paper_schemas import (
    AnalysisBatchResult,
    AnalysisIssue,
    PaperAnalysisResult,
)
from zotero_arxiv_daily.analysis.validation_schemas import ValidationBatchResult
from zotero_arxiv_daily.delivery.feishu import DigestPolicy
from zotero_arxiv_daily.delivery.ledger import DeliveryLedger
from zotero_arxiv_daily.documents.evidence import EvidenceBuildSettings
from zotero_arxiv_daily.observability.collector import (
    AnalysisMetricsSink,
    BoundedTokenEstimator,
    MetricsSession,
)
from zotero_arxiv_daily.observability.metrics import RunMetrics
from zotero_arxiv_daily.observability.store import MetricsWriter
from zotero_arxiv_daily.pipeline.artifacts import ArtifactAuditor, ManifestStore
from zotero_arxiv_daily.pipeline.daily import (
    AnalysisStageOutput,
    DailyDependencies,
    DailySettings,
    PreparedDelivery,
    _build_validation_packets,
    run_daily,
)
from zotero_arxiv_daily.pipeline.candidates import CandidatePipelineError
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
    validation_batch: ValidationBatchResult | None = None,
    allow_partial_viewer: bool = False,
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
        if validation_batch is not None:
            return validation_batch
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
        return StaticViewerBuilder(ViewerSettings(
            output_root=run_root / "viewer", allow_partial=allow_partial_viewer
        )).build(
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
        metrics_session=MetricsSession(
            run_id="run-stage7",
            trigger="local",
            config_hash="a" * 64,
            token_estimator=BoundedTokenEstimator(),
        ),
        metrics_writer=MetricsWriter(run_root),
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
    assert manifest.metrics.status == "success"
    assert manifest.metrics.sidecar_hash is not None
    metrics_path = tmp_path / "run" / "run-metrics.json"
    assert metrics_path.is_file()
    metrics = RunMetrics.model_validate_json(metrics_path.read_bytes())
    assert tuple(stage.name for stage in metrics.stages) == (
        "candidates",
        "documents",
        "analysis",
        "validation",
        "viewer",
        "feishu",
    )
    assert metrics.duration_ns == sum(stage.duration_ns for stage in metrics.stages)
    assert not (tmp_path / "run" / "viewer" / "run-metrics.json").exists()
    serialized = dependencies.manifest_store.output.read_text(encoding="utf-8")
    assert "secret" not in serialized
    assert "prompt" not in serialized


def test_candidate_cleanup_runs_before_document_stage(tmp_path: Path) -> None:
    order: list[str] = []
    dependencies = _dependencies(tmp_path, _inputs(1), order=order)
    original_document_runner = dependencies.document_runner
    dependencies.candidate_cleanup = lambda: order.append("candidate_cleanup")

    def document_runner(batch):
        assert order == ["candidates", "candidate_cleanup"]
        return original_document_runner(batch)

    dependencies.document_runner = document_runner

    manifest = run_daily(_settings(tmp_path), dependencies)

    assert manifest.status == "success"
    assert order[:3] == ["candidates", "candidate_cleanup", "documents"]


def test_candidate_boundary_failure_writes_only_fixed_manifest_code(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies(tmp_path, _inputs(1))

    def fail_candidates():
        try:
            raise RuntimeError("PRIVATE ZOTERO RESPONSE")
        except RuntimeError as error:
            raise CandidatePipelineError("candidate_interest_timeout") from error

    dependencies.candidate_runner = fail_candidates

    manifest = run_daily(_settings(tmp_path), dependencies)

    assert manifest.status == "failed"
    assert manifest.error_codes == ("candidate_interest_timeout",)
    assert manifest.stages[0].error_codes == ("candidate_interest_timeout",)
    serialized = dependencies.manifest_store.output.read_text(encoding="utf-8")
    assert "PRIVATE ZOTERO RESPONSE" not in serialized


def test_validation_packet_rebuild_skips_failed_analyses_without_calling_builder() -> None:
    items = _inputs(2)
    candidates = _candidate_batch(items)
    documents = _document_batch(items)
    analyses = _analysis_batch(items)
    failed_results = tuple(
        result.model_copy(
            update={
                "status": "failed",
                "analysis": None,
                "issues": (
                    AnalysisIssue(
                        code="analysis_evidence_build_failed",
                        severity="error",
                        message="analysis could not build its evidence packet",
                    ),
                ),
            }
        )
        for result in analyses.results
    )
    analyses = analyses.model_copy(update={"results": failed_results})
    calls: list[str] = []

    packets = _build_validation_packets(
        candidates,
        documents,
        analyses,
        EvidenceBuildSettings(),
        builder=lambda paper_id, *_args: calls.append(paper_id),
    )

    assert packets == ()
    assert calls == []


def test_validation_packet_rebuild_isolates_one_paper_failure() -> None:
    items = _inputs(2)
    candidates = _candidate_batch(items)
    documents = _document_batch(items)
    analyses = _analysis_batch(items)
    packet_by_id = {item.packet.paper_id: item.packet for item in items}
    first_id, second_id = candidates.selected_for_full_analysis[:2]

    def builder(paper_id, *_args):
        if paper_id == first_id:
            raise RuntimeError("PRIVATE DOCUMENT CONTENT")
        return packet_by_id[paper_id]

    packets = _build_validation_packets(
        candidates,
        documents,
        analyses,
        EvidenceBuildSettings(),
        builder=builder,
    )

    assert tuple(packet.paper_id for packet in packets) == (second_id,)


def test_analysis_stage_exposes_only_allowlisted_fixed_diagnostics(
    tmp_path: Path,
) -> None:
    items = _inputs(1)
    analyses = _analysis_batch(items)
    failed = analyses.results[0].model_copy(
        update={
            "status": "failed",
            "analysis": None,
            "issues": (
                AnalysisIssue(
                    code="analysis_evidence_build_failed",
                    severity="error",
                    message="analysis could not build its evidence packet",
                ),
                AnalysisIssue(
                    code="https://private.example.test/dynamic-error",
                    severity="error",
                    message="dynamic dependency exception",
                ),
            ),
        }
    )
    analyses = analyses.model_copy(update={"results": (failed,)})

    manifest = run_daily(
        _settings(tmp_path),
        _dependencies(tmp_path, items, analysis_batch=analyses),
    )

    assert manifest.stages[2].error_codes == (
        "analysis_paper_failed",
        "analysis_evidence_build_failed",
    )
    serialized = (tmp_path / "run" / "run-manifest.json").read_text(
        encoding="utf-8"
    )
    assert "private.example.test" not in serialized


def test_metrics_persistence_failure_does_not_change_content_success(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies(tmp_path, _inputs(1))

    class BrokenMetricsWriter:
        def write(self, metrics):
            raise OSError("PRIVATE METRICS PATH AND CONTENT")

    dependencies.metrics_writer = BrokenMetricsWriter()
    manifest = run_daily(_settings(tmp_path), dependencies)

    assert manifest.status == "success"
    assert manifest.static_site.status == "success"
    assert manifest.metrics.status == "failed"
    assert manifest.metrics.error_codes == ("metrics_persistence_failed",)
    assert "PRIVATE" not in dependencies.manifest_store.output.read_text(encoding="utf-8")


def test_metrics_collection_failure_does_not_skip_or_fail_content(
    tmp_path: Path,
) -> None:
    order: list[str] = []
    dependencies = _dependencies(tmp_path, _inputs(1), order=order)
    dependencies.metrics_session = MetricsSession(
        run_id="run-stage7",
        trigger="local",
        config_hash="a" * 64,
        monotonic_ns=lambda: (_ for _ in ()).throw(
            RuntimeError("PRIVATE CLOCK DETAIL")
        ),
    )

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
    assert manifest.static_site.status == "success"
    assert manifest.feishu.status == "preview"
    assert manifest.metrics.status == "failed"
    assert manifest.metrics.error_codes == ("metrics_collection_failed",)
    serialized = dependencies.manifest_store.output.read_text(encoding="utf-8")
    assert "PRIVATE" not in serialized


def test_metrics_peak_sampler_failure_does_not_change_content_success(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies(tmp_path, _inputs(1))
    dependencies.metrics_session = MetricsSession(
        run_id="run-stage7",
        trigger="local",
        config_hash="a" * 64,
        peak_memory_sampler=lambda: (_ for _ in ()).throw(
            RuntimeError("PRIVATE SAMPLER DETAIL")
        ),
    )

    manifest = run_daily(_settings(tmp_path), dependencies)

    assert manifest.status == "success"
    assert manifest.static_site.status == "success"
    assert manifest.metrics.status == "failed"
    assert manifest.metrics.error_codes == ("metrics_collection_failed",)


def test_metrics_time_complete_viewer_and_feishu_boundaries_and_record_bytes(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies(tmp_path, _inputs(1))
    elapsed = [0]
    original_viewer = dependencies.viewer_runner
    original_auditor = dependencies.artifact_auditor
    original_prepare = dependencies.prepare_delivery
    original_send = dependencies.send_delivery

    def viewer(batch):
        elapsed[0] += 10
        return original_viewer(batch)

    class TimedAuditor:
        def audit(self, root):
            elapsed[0] += 20
            return original_auditor.audit(root)

    def prepare(batch):
        elapsed[0] += 30
        return original_prepare(batch)

    class TimedLedger:
        @contextmanager
        def claim(self, key):
            elapsed[0] += 40
            yield False

    def send(delivery):
        elapsed[0] += 50
        original_send(delivery)

    dependencies.viewer_runner = viewer
    dependencies.artifact_auditor = TimedAuditor()
    dependencies.prepare_delivery = prepare
    dependencies.delivery_ledger = TimedLedger()
    dependencies.send_delivery = send
    dependencies.metrics_session = MetricsSession(
        run_id="run-stage7",
        trigger="local",
        config_hash="a" * 64,
        monotonic_ns=lambda: elapsed[0],
        peak_memory_sampler=lambda: 0,
    )

    manifest = run_daily(_settings(tmp_path, send=True), dependencies)
    metrics = RunMetrics.model_validate_json(
        (tmp_path / "run" / "run-metrics.json").read_bytes()
    )
    by_name = {stage.name: stage for stage in metrics.stages}

    assert manifest.status == "success"
    assert by_name["viewer"].duration_ns == 30
    assert by_name["viewer"].byte_count == manifest.static_site.audited_byte_count
    assert by_name["viewer"].byte_count > 0
    assert by_name["feishu"].duration_ns == 120


def test_analysis_attempts_beyond_result_count_are_reported_as_retries(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies(tmp_path, _inputs(1))
    original_analysis = dependencies.analysis_runner

    def analysis(candidates, documents):
        assert dependencies.metrics_session is not None
        for index, succeeded in enumerate((False, True)):
            dependencies.metrics_session.record_model_attempt(
                model_identity="offline-fake",
                input_texts=(),
                output_text=None,
                configured_output_tokens=0,
                succeeded=succeeded,
                paid=False,
                retry=index > 0,
            )
        return original_analysis(candidates, documents)

    dependencies.analysis_runner = analysis
    manifest = run_daily(_settings(tmp_path), dependencies)
    metrics = RunMetrics.model_validate_json(
        (tmp_path / "run" / "run-metrics.json").read_bytes()
    )

    assert manifest.stages[2].retry_count == 1
    assert manifest.retry_count == 1
    assert metrics.stages[2].retry_count == 1
    assert metrics.retry_count == 1


def test_analysis_retry_count_ignores_skipped_and_cached_results(
    tmp_path: Path,
) -> None:
    items = _inputs(2)
    base = _analysis_batch(items)
    mixed = base.model_copy(
        update={
            "results": (
                base.results[0],
                PaperAnalysisResult(
                    paper_id=base.results[1].paper_id,
                    status="skipped",
                    analysis=None,
                    issues=(),
                    processing_seconds=0,
                    cache_hit=False,
                ),
            )
        }
    )
    dependencies = _dependencies(tmp_path, items, analysis_batch=mixed)
    original_analysis = dependencies.analysis_runner

    def analysis(candidates, documents):
        assert dependencies.metrics_session is not None
        for index, succeeded in enumerate((False, True)):
            dependencies.metrics_session.record_model_attempt(
                model_identity="offline-fake",
                input_texts=(),
                output_text=None,
                configured_output_tokens=0,
                succeeded=succeeded,
                paid=False,
                retry=index > 0,
            )
        return original_analysis(candidates, documents)

    dependencies.analysis_runner = analysis
    manifest = run_daily(_settings(tmp_path), dependencies)

    assert manifest.stages[2].retry_count == 1


def test_analysis_usage_collection_failure_marks_metrics_without_failing_content(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies(tmp_path, _inputs(1))
    dependencies.metrics_session = MetricsSession(
        run_id="run-stage7",
        trigger="local",
        config_hash="a" * 64,
        token_estimator=BoundedTokenEstimator(max_input_bytes=1),
    )
    original_analysis = dependencies.analysis_runner

    def analysis(candidates, documents):
        assert dependencies.metrics_session is not None
        AnalysisMetricsSink(dependencies.metrics_session).record_attempt(
            AnalysisRequest(
                prompt_version="stage3-v1",
                system_prompt="oversize private prompt",
                user_prompt="oversize private full text",
                response_schema={"type": "object"},
                max_output_tokens=1,
            ),
            "oversize private response",
            model_identity="offline-fake",
            succeeded=True,
            paid=False,
        )
        return original_analysis(candidates, documents)

    dependencies.analysis_runner = analysis
    manifest = run_daily(_settings(tmp_path), dependencies)

    assert manifest.status == "success"
    assert manifest.metrics.status == "failed"
    assert manifest.metrics.error_codes == ("metrics_collection_failed",)


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


def test_feedback_vetoed_candidates_never_reach_document_analysis_validation_viewer_or_feishu(
    tmp_path: Path,
) -> None:
    order: list[str] = []
    dependencies = _dependencies(tmp_path, _inputs(1), order=order)
    vetoed = _candidate_batch(_inputs(1)).model_copy(
        update={
            "candidates": (),
            "rankings": (),
            "selected_for_llm": (),
            "selected_for_full_analysis": (),
        }
    )
    dependencies.candidate_runner = lambda: (order.append("candidates") or vetoed)

    manifest = run_daily(_settings(tmp_path), dependencies)

    assert order == ["candidates"]
    assert manifest.status == "empty"
    serialized = dependencies.manifest_store.output.read_text(encoding="utf-8")
    assert "feedback" not in serialized
    assert "2401." not in serialized


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


def test_stage4_partial_cannot_reach_viewer_even_when_stage5_partial_is_enabled(
    tmp_path: Path,
) -> None:
    from tests.viewer.test_publication import _result

    item = _inputs(1)[0]
    partial = _result("partial", eligible=False)
    validation = ValidationBatchResult(
        run_id=_candidate_batch((item,)).run_id,
        created_at=NOW,
        results=(partial,),
    )

    manifest = run_daily(
        _settings(tmp_path),
        _dependencies(
            tmp_path,
            (item,),
            validation_batch=validation,
            allow_partial_viewer=True,
        ),
    )

    assert manifest.counts.validated_count == 0
    assert manifest.counts.published_count == 0
    assert not tuple((_settings(tmp_path).viewer_output / "papers").glob("*.html"))


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


def test_delivery_ledger_serializes_same_key_across_concurrent_workers(
    tmp_path: Path,
) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery-ledger.json")
    key = "d" * 64
    sends: list[str] = []

    def worker() -> None:
        with ledger.claim(key) as duplicate:
            if duplicate:
                return
            sends.append(key)
            sleep(0.05)

    workers = [Thread(target=worker) for _ in range(4)]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join(timeout=5)

    assert not any(worker_thread.is_alive() for worker_thread in workers)
    assert sends == [key]
