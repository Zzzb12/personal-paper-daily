from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Any, Protocol

from pydantic import field_validator, model_validator

from zotero_arxiv_daily.analysis.document_schemas import DocumentBatchResult
from zotero_arxiv_daily.analysis.paper_schemas import AnalysisBatchResult, EvidencePacket
from zotero_arxiv_daily.analysis.schemas import CandidateBatch, StrictModel, validate_run_id_value
from zotero_arxiv_daily.analysis.validation_schemas import ValidationBatchResult
from zotero_arxiv_daily.candidates.feedback import (
    FEEDBACK_PROJECTION_IMPLEMENTATION_VERSION,
    FeedbackProjectionError,
)
from zotero_arxiv_daily.delivery.ledger import DeliveryLedger
from zotero_arxiv_daily.pipeline.artifacts import (
    ArtifactAudit,
    ArtifactAuditor,
    ManifestStore,
    resolve_within,
)
from zotero_arxiv_daily.pipeline.daily_schemas import (
    FeishuRunResult,
    RunCounts,
    RunManifest,
    STAGE_ORDER,
    StageRunResult,
    StaticSiteResult,
    Trigger,
)
from zotero_arxiv_daily.viewer.schemas import BuildManifest


class DailySettings(StrictModel):
    run_id: str
    trigger: Trigger = "local"
    config_hash: str
    dry_run: bool = True
    send_feishu: bool = False
    viewer_output: Path

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return validate_run_id_value(value)

    @field_validator("config_hash")
    @classmethod
    def validate_config_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("config_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_send_gate(self):
        if self.dry_run and self.send_feishu:
            raise ValueError("dry-run cannot enable Feishu sending")
        return self


@dataclass(frozen=True)
class AnalysisStageOutput:
    batch: AnalysisBatchResult
    packets: tuple[EvidencePacket, ...]


class PreparedDelivery(StrictModel):
    idempotency_key: str
    paper_count: int

    @field_validator("idempotency_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("idempotency_key must be a lowercase SHA-256 digest")
        return value

    @field_validator("paper_count")
    @classmethod
    def validate_paper_count(cls, value: int) -> int:
        if isinstance(value, bool) or not 0 <= value <= 5:
            raise ValueError("paper_count must be between zero and five")
        return value


class ManifestWriter(Protocol):
    output: Path

    def write(self, manifest: RunManifest) -> Path: ...


class Auditor(Protocol):
    def audit(self, viewer_root: Path) -> ArtifactAudit: ...


class Ledger(Protocol):
    def claim(self, idempotency_key: str) -> AbstractContextManager[bool]: ...


@dataclass
class DailyDependencies:
    candidate_runner: Callable[[], CandidateBatch]
    document_runner: Callable[[CandidateBatch], DocumentBatchResult]
    analysis_runner: Callable[[CandidateBatch, DocumentBatchResult], AnalysisStageOutput]
    validation_runner: Callable[
        [CandidateBatch, DocumentBatchResult, AnalysisStageOutput], ValidationBatchResult
    ]
    viewer_runner: Callable[[ValidationBatchResult], BuildManifest]
    prepare_delivery: Callable[[ValidationBatchResult], PreparedDelivery]
    send_delivery: Callable[[PreparedDelivery], None]
    manifest_store: ManifestWriter
    artifact_auditor: Auditor
    delivery_ledger: Ledger
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]
    close_callbacks: tuple[Callable[[], None], ...] = ()

    def close(self) -> None:
        for callback in reversed(self.close_callbacks):
            callback()


@dataclass(frozen=True)
class DailyFactoryContext:
    settings: DailySettings
    config_dir: Path
    run_root: Path
    manifest_output: Path
    offline_fixture: Path | None
    environment: Mapping[str, str]


def run_daily(settings: DailySettings, dependencies: DailyDependencies) -> RunManifest:
    started_at = dependencies.clock()
    stages: list[StageRunResult] = []
    static_site = StaticSiteResult(status="skipped")
    feishu = FeishuRunResult(status="skipped")
    counts = RunCounts()

    try:
        candidates = dependencies.candidate_runner()
    except FeedbackProjectionError:
        stages.append(_failed_stage("candidates", "feedback_projection_rejected"))
        stages.extend(_skipped_stages(start_at=1))
        return _finish(
            settings,
            dependencies,
            started_at,
            stages,
            counts,
            static_site,
            feishu,
            status="failed",
        )
    except Exception:
        stages.append(_failed_stage("candidates", "candidate_stage_failed"))
        stages.extend(_skipped_stages(start_at=1))
        return _finish(
            settings,
            dependencies,
            started_at,
            stages,
            counts,
            static_site,
            feishu,
            status="failed",
        )

    candidate_count = len(candidates.candidates)
    counts = counts.model_copy(
        update={
            "input_count": candidates.counts.retrieved,
            "candidate_count": candidate_count,
            "selected_count": len(candidates.selected_for_full_analysis),
        }
    )
    if candidate_count == 0:
        stages.append(
            StageRunResult(
                name="candidates",
                status="empty",
                input_count=candidates.counts.retrieved,
                output_count=0,
            )
        )
        stages.extend(_skipped_stages(start_at=1))
        return _finish(
            settings,
            dependencies,
            started_at,
            stages,
            counts,
            static_site,
            feishu,
            status="empty",
        )
    stages.append(
        StageRunResult(
            name="candidates",
            status="partial" if candidates.counts.invalid else "success",
            input_count=candidates.counts.retrieved,
            output_count=candidate_count,
            partial_failure_count=candidates.counts.invalid,
            error_codes=("candidate_item_failed",) if candidates.counts.invalid else (),
        )
    )

    try:
        documents = dependencies.document_runner(candidates)
    except Exception:
        stages.append(_failed_stage("documents", "document_stage_failed", counts.selected_count))
        stages.extend(_skipped_stages(start_at=2))
        return _finish(
            settings,
            dependencies,
            started_at,
            stages,
            counts,
            static_site,
            feishu,
            status="failed",
        )
    document_stage = _paper_stage(
        "documents",
        counts.selected_count,
        tuple(result.status for result in documents.results),
        cache_hits=sum(result.cache_hit for result in documents.results),
        error_code="document_paper_failed",
    )
    stages.append(document_stage)

    try:
        analysis_output = dependencies.analysis_runner(candidates, documents)
    except Exception:
        stages.append(_failed_stage("analysis", "analysis_stage_failed", counts.selected_count))
        stages.extend(_skipped_stages(start_at=3))
        return _finish(
            settings,
            dependencies,
            started_at,
            stages,
            counts,
            static_site,
            feishu,
            status="failed",
        )
    analyses = analysis_output.batch
    analyzed_count = sum(
        result.status in {"success", "partial"} for result in analyses.results
    )
    counts = counts.model_copy(update={"analyzed_count": analyzed_count})
    analysis_stage = _paper_stage(
        "analysis",
        counts.selected_count,
        tuple(result.status for result in analyses.results),
        cache_hits=analyses.cache_hit_count,
        error_code="analysis_paper_failed",
    )
    stages.append(analysis_stage)

    try:
        validation = dependencies.validation_runner(
            candidates, documents, analysis_output
        )
    except Exception:
        stages.append(
            _failed_stage("validation", "validation_stage_failed", analyzed_count)
        )
        stages.extend(_skipped_stages(start_at=4))
        return _finish(
            settings,
            dependencies,
            started_at,
            stages,
            counts,
            static_site,
            feishu,
            status="failed",
        )
    eligible_count = sum(_is_eligible(result) for result in validation.results)
    counts = counts.model_copy(update={"validated_count": eligible_count})
    validation_stage = _validation_stage(
        input_count=counts.selected_count,
        validation=validation,
        eligible_count=eligible_count,
    )
    stages.append(validation_stage)
    publishable_validation = _eligible_validation_batch(validation)

    try:
        build = dependencies.viewer_runner(publishable_validation)
        if build.published_count != eligible_count:
            raise ValueError("viewer publication count differs from Stage 4 eligibility")
        audit = dependencies.artifact_auditor.audit(settings.viewer_output)
        if audit.build_manifest != build:
            raise ValueError("viewer build and audited manifest differ")
        static_site = StaticSiteResult(
            status="success",
            published_count=build.published_count,
            audited_file_count=audit.file_count,
            audited_byte_count=audit.byte_count,
            artifact_hash=audit.artifact_hash,
        )
        counts = counts.model_copy(update={"published_count": build.published_count})
        stages.append(
            StageRunResult(
                name="viewer",
                status="success" if build.published_count else "empty",
                input_count=len(validation.results),
                output_count=build.published_count,
            )
        )
    except Exception:
        static_site = StaticSiteResult(
            status="failed", error_codes=("viewer_stage_failed",)
        )
        stages.append(
            _failed_stage("viewer", "viewer_stage_failed", len(validation.results))
        )
        feishu = FeishuRunResult(
            status="skipped", error_codes=("feishu_skipped_viewer_failed",)
        )
        stages.append(
            StageRunResult(
                name="feishu",
                status="skipped",
                input_count=eligible_count,
                error_codes=("feishu_skipped_viewer_failed",),
            )
        )
        return _finish(
            settings,
            dependencies,
            started_at,
            stages,
            counts,
            static_site,
            feishu,
            status="failed",
        )

    try:
        delivery = dependencies.prepare_delivery(publishable_validation)
        if not settings.send_feishu:
            feishu = FeishuRunResult(status="preview")
            stages.append(
                StageRunResult(
                    name="feishu",
                    status="success" if delivery.paper_count else "empty",
                    input_count=eligible_count,
                    output_count=delivery.paper_count,
                )
            )
        elif delivery.paper_count == 0:
            feishu = FeishuRunResult(status="skipped")
            stages.append(
                StageRunResult(
                    name="feishu",
                    status="empty",
                    input_count=eligible_count,
                )
            )
        else:
            with dependencies.delivery_ledger.claim(delivery.idempotency_key) as duplicate:
                if duplicate:
                    feishu = FeishuRunResult(
                        status="duplicate", idempotency_key=delivery.idempotency_key
                    )
                    stages.append(
                        StageRunResult(
                            name="feishu",
                            status="success",
                            input_count=eligible_count,
                        )
                    )
                else:
                    dependencies.send_delivery(delivery)
                    feishu = FeishuRunResult(
                        status="sent",
                        delivered_count=delivery.paper_count,
                        idempotency_key=delivery.idempotency_key,
                    )
                    counts = counts.model_copy(
                        update={"delivered_count": delivery.paper_count}
                    )
                    stages.append(
                        StageRunResult(
                            name="feishu",
                            status="success",
                            input_count=eligible_count,
                            output_count=delivery.paper_count,
                        )
                    )
    except Exception:
        feishu = FeishuRunResult(
            status="failed", error_codes=("feishu_delivery_failed",)
        )
        stages.append(
            _failed_stage("feishu", "feishu_delivery_failed", eligible_count)
        )

    status = _derive_run_status(stages)
    return _finish(
        settings,
        dependencies,
        started_at,
        stages,
        counts,
        static_site,
        feishu,
        status=status,
    )


def _paper_stage(
    name: str,
    input_count: int,
    statuses: tuple[str, ...],
    *,
    cache_hits: int,
    error_code: str,
) -> StageRunResult:
    output_count = sum(status in {"success", "partial"} for status in statuses)
    failures = sum(status != "success" for status in statuses) + max(
        input_count - len(statuses), 0
    )
    if not statuses or output_count == 0:
        status = "failed"
    elif failures:
        status = "partial"
    else:
        status = "success"
    return StageRunResult(
        name=name,
        status=status,
        input_count=input_count,
        output_count=output_count,
        cache_hit_count=cache_hits,
        partial_failure_count=failures,
        error_codes=(error_code,) if failures else (),
    )


def _validation_stage(
    *,
    input_count: int,
    validation: ValidationBatchResult,
    eligible_count: int,
) -> StageRunResult:
    non_valid = sum(
        result.status != "validated" for result in validation.results
    ) + max(input_count - len(validation.results), 0)
    if not validation.results:
        status = "failed"
    elif non_valid:
        status = "partial"
    else:
        status = "success"
    return StageRunResult(
        name="validation",
        status=status,
        input_count=input_count,
        output_count=eligible_count,
        cache_hit_count=validation.cache_hit_count,
        partial_failure_count=non_valid,
        error_codes=("validation_paper_blocked",) if non_valid else (),
    )


def _eligible_validation_batch(
    validation: ValidationBatchResult,
) -> ValidationBatchResult:
    eligible = tuple(result for result in validation.results if _is_eligible(result))
    return validation.model_copy(
        update={
            "results": eligible,
            "cache_hit_count": sum(result.cache_hit for result in eligible),
        }
    )


def _is_eligible(result: Any) -> bool:
    return bool(
        result.status == "validated"
        and result.report is not None
        and result.report.status == "valid"
        and result.report.publication_eligibility == "eligible"
        and result.validated is not None
    )


def _failed_stage(name: str, error_code: str, input_count: int = 0) -> StageRunResult:
    return StageRunResult(
        name=name,
        status="failed",
        input_count=input_count,
        error_codes=(error_code,),
    )


def _skipped_stages(*, start_at: int) -> list[StageRunResult]:
    return [
        StageRunResult(name=name, status="skipped")
        for name in STAGE_ORDER[start_at:]
    ]


def _derive_run_status(stages: Sequence[StageRunResult]) -> str:
    if stages and stages[0].status == "empty":
        return "empty"
    if any(stage.status == "failed" and stage.name != "feishu" for stage in stages):
        return "failed"
    if any(
        stage.status == "partial" or stage.partial_failure_count
        for stage in stages
    ) or any(stage.name == "feishu" and stage.status == "failed" for stage in stages):
        return "partial"
    return "success"


def _finish(
    settings: DailySettings,
    dependencies: DailyDependencies,
    started_at: datetime,
    stages: list[StageRunResult],
    counts: RunCounts,
    static_site: StaticSiteResult,
    feishu: FeishuRunResult,
    *,
    status: str,
) -> RunManifest:
    errors: list[str] = []
    for stage in stages:
        for code in stage.error_codes:
            if code not in errors:
                errors.append(code)
    manifest = RunManifest(
        run_id=settings.run_id,
        trigger=settings.trigger,
        config_hash=settings.config_hash,
        dry_run=settings.dry_run,
        send_requested=settings.send_feishu,
        started_at=started_at,
        completed_at=dependencies.clock(),
        status=status,
        stages=tuple(stages),
        counts=counts,
        cache_hit_count=sum(stage.cache_hit_count for stage in stages),
        retry_count=sum(stage.retry_count for stage in stages),
        partial_failure_count=sum(stage.partial_failure_count for stage in stages),
        static_site=static_site,
        feishu=feishu,
        artifact_hash=static_site.artifact_hash,
        error_codes=tuple(errors),
    )
    dependencies.manifest_store.write(manifest)
    return manifest


_LIVE_REQUIRED_ENVIRONMENT = (
    "ZOTERO_ID",
    "ZOTERO_KEY",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "PAPER_DAILY_SITE_URL",
)
_SEND_REQUIRED_ENVIRONMENT = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_CHAT_ID",
)
_OFFLINE_CHAT_ID = "oc_00000000000000000000000000000000"
_OFFLINE_SITE_URL = "https://papers.example.test/stage7-offline/"
_REQUIRED_DOCLING_MODEL_DIRECTORIES = (
    "docling-project--docling-layout-heron",
    "docling-project--docling-models",
)


def _configuration_hash(
    config_dir: Path,
    *,
    mode: str,
    fixture: Path | None,
    environment: Mapping[str, str],
) -> str:
    from omegaconf import OmegaConf

    digest = hashlib.sha256()
    digest.update(b"stage7-v1\0")
    digest.update(FEEDBACK_PROJECTION_IMPLEMENTATION_VERSION.encode("utf-8"))
    digest.update(b"\0")
    for name in ("base.yaml", "custom.yaml"):
        path = Path(config_dir) / name
        if path.exists():
            configuration = OmegaConf.to_container(OmegaConf.load(path), resolve=False)
            if not isinstance(configuration, dict):
                raise ValueError("daily configuration must be a mapping")
            candidate_pipeline = configuration.get("candidate_pipeline")
            if isinstance(candidate_pipeline, dict):
                feedback = candidate_pipeline.get("feedback")
                if isinstance(feedback, dict):
                    feedback.pop("store_path", None)
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(
                json.dumps(
                    configuration,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(b"\0")
    digest.update(mode.encode("ascii"))
    digest.update(b"\0")
    if fixture is not None:
        digest.update(hashlib.sha256(fixture.read_bytes()).digest())
    else:
        for name in ("LLM_BASE_URL", "LLM_MODEL", "PAPER_DAILY_SITE_URL"):
            digest.update(name.encode("ascii"))
            digest.update(b"\0")
            digest.update(environment.get(name, "").strip().encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def _missing_environment(
    environment: Mapping[str, str], *, send_feishu: bool
) -> tuple[str, ...]:
    required = (
        *_LIVE_REQUIRED_ENVIRONMENT,
        *(_SEND_REQUIRED_ENVIRONMENT if send_feishu else ()),
    )
    return tuple(name for name in required if not environment.get(name, "").strip())


def _safe_run_root(path: Path) -> Path:
    supplied = Path(path)
    if supplied.is_symlink() or (
        hasattr(supplied, "is_junction") and supplied.is_junction()
    ):
        raise ValueError("run root must not be a symbolic link or junction")
    windows = PureWindowsPath(str(path))
    if windows.drive.startswith("\\\\"):
        raise ValueError("UNC run roots are not allowed")
    resolved = Path(path).resolve()
    if resolved.exists() and (
        resolved.is_symlink()
        or (hasattr(resolved, "is_junction") and resolved.is_junction())
    ):
        raise ValueError("run root must not be a symbolic link or junction")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _require_docling_artifacts(path: Path) -> Path:
    supplied = Path(path)
    if supplied.is_symlink() or (
        hasattr(supplied, "is_junction") and supplied.is_junction()
    ):
        raise ValueError("Docling model artifacts must be a regular local directory")
    root = supplied.resolve()
    if not root.is_dir():
        raise ValueError("Docling model artifacts are unavailable")
    for name in _REQUIRED_DOCLING_MODEL_DIRECTORIES:
        model_root = root / name
        if not model_root.is_dir() or not any(
            candidate.is_file() for candidate in model_root.rglob("*")
        ):
            raise ValueError("Docling model artifacts are incomplete")
    return root


class _NoWriteValidationCache:
    def read(self, identity: Any) -> None:
        return None

    def write(self, identity: Any, validated: Any) -> None:
        return None


def _feedback_rejected_daily_dependencies(context: DailyFactoryContext) -> DailyDependencies:
    """Return a local-only shell that records a configured feedback-store rejection."""

    def reject_candidates() -> CandidateBatch:
        raise FeedbackProjectionError()

    def unreachable(*_: Any) -> Any:
        raise AssertionError("feedback-rejected run must stop after candidates")

    return DailyDependencies(
        candidate_runner=reject_candidates,
        document_runner=unreachable,
        analysis_runner=unreachable,
        validation_runner=unreachable,
        viewer_runner=unreachable,
        prepare_delivery=unreachable,
        send_delivery=unreachable,
        manifest_store=ManifestStore(context.run_root, context.manifest_output),
        artifact_auditor=ArtifactAuditor(context.run_root),
        delivery_ledger=DeliveryLedger(context.run_root / "delivery-ledger.json"),
        clock=lambda: datetime.now(UTC),
        sleep=lambda _: None,
    )


def build_offline_daily_dependencies(context: DailyFactoryContext) -> DailyDependencies:
    if context.offline_fixture is None or not context.settings.dry_run:
        raise ValueError("offline daily dependencies require a dry-run fixture")
    fixture_path = context.offline_fixture
    if fixture_path.is_symlink() or not fixture_path.is_file():
        raise ValueError("offline fixture must be a regular file")
    if fixture_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("offline fixture exceeds the byte limit")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    from zotero_arxiv_daily.analysis.document_schemas import (
        DocumentBatchResult,
        PaperDocumentResult,
    )
    from zotero_arxiv_daily.analysis.paper_schemas import AnalysisBatchResult
    from zotero_arxiv_daily.delivery.feishu import DigestPolicy, FeishuRenderer
    from zotero_arxiv_daily.pipeline.validation import (
        ValidationDependencies,
        ValidationSettings,
        _fixture_time,
        _offline_candidate_batch,
        _offline_inputs,
        build_validation_batch,
    )
    from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
    from zotero_arxiv_daily.viewer.schemas import ViewerSettings

    candidate, document, packet, analysis_result = _offline_inputs(fixture)
    fixture_time = _fixture_time(fixture)
    candidates = _offline_candidate_batch(fixture, candidate, fixture_time)
    documents = DocumentBatchResult(
        run_id=candidates.run_id,
        created_at=fixture_time,
        results=(
            PaperDocumentResult(
                paper_id=candidate.paper_id,
                status="success",
                document=document,
                processing_seconds=0,
            ),
        ),
    )
    analyses = AnalysisBatchResult(
        run_id=candidates.run_id,
        created_at=fixture_time,
        results=(analysis_result,),
        expensive_call_count=0,
        cache_hit_count=0,
    )
    analysis_output = AnalysisStageOutput(batch=analyses, packets=(packet,))
    prepared_requests: dict[str, str] = {}
    clock_ticks = iter(fixture_time + timedelta(seconds=index) for index in range(32))

    def validation_runner(
        candidate_batch: CandidateBatch,
        document_batch: DocumentBatchResult,
        output: AnalysisStageOutput,
    ) -> ValidationBatchResult:
        return build_validation_batch(
            candidate_batch,
            document_batch,
            output.packets,
            output.batch,
            ValidationSettings(),
            ValidationDependencies(
                cache=_NoWriteValidationCache(), clock=lambda: fixture_time
            ),
        )

    def viewer_runner(batch: ValidationBatchResult) -> BuildManifest:
        return StaticViewerBuilder(
            ViewerSettings(output_root=context.settings.viewer_output)
        ).build(batch.results, batch_label=context.settings.run_id)

    def prepare_delivery(batch: ValidationBatchResult) -> PreparedDelivery:
        request = DigestPolicy.build(
            batch, chat_id=_OFFLINE_CHAT_ID, site_url=_OFFLINE_SITE_URL
        )
        prepared_requests[request.idempotency_key] = FeishuRenderer.render(request.payload)
        return PreparedDelivery(
            idempotency_key=request.idempotency_key,
            paper_count=len(request.payload.papers),
        )

    def reject_send(delivery: PreparedDelivery) -> None:
        del delivery
        raise RuntimeError("offline fixture cannot send")

    return DailyDependencies(
        candidate_runner=lambda: candidates,
        document_runner=lambda received: documents,
        analysis_runner=lambda received_candidates, received_documents: analysis_output,
        validation_runner=validation_runner,
        viewer_runner=viewer_runner,
        prepare_delivery=prepare_delivery,
        send_delivery=reject_send,
        manifest_store=ManifestStore(context.run_root, context.manifest_output),
        artifact_auditor=ArtifactAuditor(context.run_root),
        delivery_ledger=DeliveryLedger(context.run_root / "delivery-ledger.json"),
        clock=lambda: next(clock_ticks),
        sleep=lambda _: None,
    )


def build_production_daily_dependencies(context: DailyFactoryContext) -> DailyDependencies:
    if context.settings.dry_run or context.offline_fixture is not None:
        raise ValueError("production daily dependencies require live mode")

    from omegaconf import OmegaConf

    from zotero_arxiv_daily.analysis.validation_cache import ValidationCache
    from zotero_arxiv_daily.delivery.feishu import (
        DigestPolicy,
        FeishuClient,
        FeishuRenderer,
        HttpxTransport,
    )
    from zotero_arxiv_daily.delivery.schemas import FeishuSettings
    from zotero_arxiv_daily.documents.evidence import build_evidence_packet
    from zotero_arxiv_daily.pipeline.analysis import (
        build_analysis_batch,
        build_production_analysis_pipeline,
    )
    from zotero_arxiv_daily.pipeline.candidates import (
        build_candidate_batch,
        build_production_pipeline,
    )
    from zotero_arxiv_daily.pipeline.documents import (
        DocumentPipelineSettings,
        build_document_batch,
        build_document_dependencies,
    )
    from zotero_arxiv_daily.pipeline.validation import (
        ValidationDependencies,
        ValidationSettings,
        build_validation_batch,
    )
    from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
    from zotero_arxiv_daily.viewer.schemas import ViewerSettings

    callbacks: list[Callable[[], None]] = []
    try:
        config = OmegaConf.load(context.config_dir / "base.yaml")
        custom_path = context.config_dir / "custom.yaml"
        if custom_path.exists():
            config = OmegaConf.merge(config, OmegaConf.load(custom_path))
        candidate_settings, candidate_dependencies = build_production_pipeline(
            context.config_dir,
            environ=dict(context.environment),
            dry_run=False,
        )
        callbacks.append(candidate_dependencies.close)

        document_config = config.document_pipeline
        docling_artifacts_path = _require_docling_artifacts(
            Path(str(document_config.docling_artifacts_path))
        )

        document_timeout = OmegaConf.to_container(
            document_config.request_timeout, resolve=True
        )
        document_retry = OmegaConf.to_container(document_config.retry, resolve=True)
        if not isinstance(document_timeout, dict) or not isinstance(document_retry, dict):
            raise ValueError("document timeout and retry configuration must be mappings")
        document_settings = DocumentPipelineSettings(
            cache_root=Path(str(document_config.cache_root)),
            docling_artifacts_path=docling_artifacts_path,
            max_download_bytes=int(document_config.max_download_bytes),
            max_pages=int(document_config.max_pages),
            document_timeout_seconds=float(document_config.document_timeout_seconds),
            connect_timeout=float(document_timeout["connect"]),
            read_timeout=float(document_timeout["read"]),
            write_timeout=float(document_timeout["write"]),
            pool_timeout=float(document_timeout["pool"]),
            max_attempts=int(document_retry["max_attempts"]),
            backoff_seconds=float(document_retry["backoff_seconds"]),
            max_retry_after_seconds=float(document_retry["max_retry_after_seconds"]),
            config_version=str(document_config.config_version),
        )
        document_dependencies = build_document_dependencies(document_settings)
        callbacks.append(document_dependencies.close)

        analysis_settings, analysis_dependencies = build_production_analysis_pipeline(
            context.config_dir, environ=context.environment
        )
        client_close = getattr(analysis_dependencies.client, "close", None)
        if callable(client_close):
            callbacks.append(client_close)

        validation_config = config.validation_pipeline
        validation_settings = ValidationSettings(
            schema_version=str(validation_config.schema_version),
            validator_version=str(validation_config.validator_version),
            max_papers=int(validation_config.max_papers),
        )
        validation_dependencies = ValidationDependencies(
            cache=ValidationCache(
                Path(str(validation_config.cache_root)),
                max_cache_bytes=int(validation_config.max_cache_bytes),
            ),
            clock=lambda: datetime.now(UTC),
        )

        viewer_config = config.viewer_pipeline
        viewer_settings = ViewerSettings(
            output_root=context.settings.viewer_output,
            evidence_roots=tuple(Path(str(value)) for value in viewer_config.evidence_roots),
            site_title=str(viewer_config.site_title),
            allow_partial=False,
            max_papers=int(viewer_config.max_papers),
            max_assets_per_paper=int(viewer_config.max_assets_per_paper),
            max_image_bytes=int(viewer_config.max_image_bytes),
            build_version=str(viewer_config.build_version),
            template_version=str(viewer_config.template_version),
        )
    except FeedbackProjectionError:
        for callback in reversed(callbacks):
            callback()
        return _feedback_rejected_daily_dependencies(context)
    except Exception:
        for callback in reversed(callbacks):
            callback()
        raise

    prepared_requests: dict[str, tuple[Any, str]] = {}

    def candidate_runner() -> CandidateBatch:
        return build_candidate_batch(
            candidate_settings,
            candidate_dependencies,
            lambda: datetime.now(UTC),
        )

    def document_runner(batch: CandidateBatch) -> DocumentBatchResult:
        return build_document_batch(batch, document_settings, document_dependencies)

    def analysis_runner(
        candidates: CandidateBatch, documents: DocumentBatchResult
    ) -> AnalysisStageOutput:
        batch = build_analysis_batch(
            candidates, documents, analysis_settings, analysis_dependencies
        )
        document_by_id = {
            result.paper_id: result.document
            for result in documents.results
            if result.document is not None
        }
        packets = tuple(
            build_evidence_packet(
                paper_id,
                document_by_id[paper_id],
                analysis_settings.evidence,
            )
            for paper_id in candidates.selected_for_full_analysis[:5]
            if paper_id in document_by_id
        )
        return AnalysisStageOutput(batch=batch, packets=packets)

    def validation_runner(
        candidates: CandidateBatch,
        documents: DocumentBatchResult,
        output: AnalysisStageOutput,
    ) -> ValidationBatchResult:
        return build_validation_batch(
            candidates,
            documents,
            output.packets,
            output.batch,
            validation_settings,
            validation_dependencies,
        )

    def viewer_runner(batch: ValidationBatchResult) -> BuildManifest:
        return StaticViewerBuilder(viewer_settings).build(
            batch.results, batch_label=context.settings.run_id
        )

    def prepare_delivery(batch: ValidationBatchResult) -> PreparedDelivery:
        settings = (
            FeishuSettings.from_environment(context.environment)
            if context.settings.send_feishu
            else None
        )
        request = DigestPolicy.build(
            batch,
            chat_id=settings.chat_id if settings is not None else _OFFLINE_CHAT_ID,
            site_url=context.environment["PAPER_DAILY_SITE_URL"],
        )
        rendered = FeishuRenderer.render(request.payload)
        prepared_requests[request.idempotency_key] = (request, rendered)
        return PreparedDelivery(
            idempotency_key=request.idempotency_key,
            paper_count=len(request.payload.papers),
        )

    def send_delivery(delivery: PreparedDelivery) -> None:
        request, rendered = prepared_requests[delivery.idempotency_key]
        settings = FeishuSettings.from_environment(context.environment)
        transport = HttpxTransport()
        try:
            FeishuClient(
                settings,
                transport,
                environment=context.environment,
                sleep=time.sleep,
            ).send(request, rendered)
        finally:
            transport.close()

    return DailyDependencies(
        candidate_runner=candidate_runner,
        document_runner=document_runner,
        analysis_runner=analysis_runner,
        validation_runner=validation_runner,
        viewer_runner=viewer_runner,
        prepare_delivery=prepare_delivery,
        send_delivery=send_delivery,
        manifest_store=ManifestStore(context.run_root, context.manifest_output),
        artifact_auditor=ArtifactAuditor(context.run_root),
        delivery_ledger=DeliveryLedger(Path("cache/workflow/delivery-ledger.json")),
        clock=lambda: datetime.now(UTC),
        sleep=time.sleep,
        close_callbacks=tuple(callbacks),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] | None = None,
    offline_factory: Callable[
        [DailyFactoryContext], DailyDependencies
    ] = build_offline_daily_dependencies,
    production_factory: Callable[
        [DailyFactoryContext], DailyDependencies
    ] = build_production_daily_dependencies,
) -> int:
    parser = argparse.ArgumentParser(
        description="Run the versioned Personal Paper Daily pipeline",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--trigger", choices=("scheduled", "manual", "local"), default="local"
    )
    parser.add_argument("--mode", choices=("dry-run", "live"), default="dry-run")
    parser.add_argument("--offline-fixture", type=Path)
    parser.add_argument("--send-feishu", action="store_true")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--run-root", type=Path, default=Path("outputs/daily"))
    parser.add_argument("--viewer-output", type=Path, default=Path("viewer"))
    parser.add_argument(
        "--manifest-output", type=Path, default=Path("run-manifest.json")
    )
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)

    environment = os.environ if environ is None else environ
    dry_run = args.mode == "dry-run"
    if dry_run and args.offline_fixture is None:
        parser.error("dry-run mode requires --offline-fixture")
    if not dry_run and args.offline_fixture is not None:
        parser.error("--offline-fixture is only valid in dry-run mode")
    if dry_run and args.send_feishu:
        parser.error("--send-feishu requires --mode live")
    if not dry_run:
        missing = _missing_environment(
            environment, send_feishu=bool(args.send_feishu)
        )
        if missing:
            parser.error("missing required environment variables: " + ", ".join(missing))

    try:
        run_root = _safe_run_root(args.run_root)
        viewer_output = resolve_within(run_root, args.viewer_output)
        manifest_output = resolve_within(run_root, args.manifest_output)
        fixture = (
            args.offline_fixture.resolve()
            if args.offline_fixture is not None
            else None
        )
        config_dir = args.config_dir.resolve()
        config_hash = _configuration_hash(
            config_dir,
            mode=args.mode,
            fixture=fixture,
            environment=environment,
        )
    except (OSError, ValueError):
        parser.error("daily pipeline paths or configuration are invalid")

    now_factory = clock or (lambda: datetime.now(UTC))
    now = now_factory()
    run_id = args.run_id or now.astimezone(UTC).strftime(
        f"%Y%m%dT%H%M%SZ-{args.trigger}"
    )
    try:
        settings = DailySettings(
            run_id=run_id,
            trigger=args.trigger,
            config_hash=config_hash,
            dry_run=dry_run,
            send_feishu=bool(args.send_feishu),
            viewer_output=viewer_output,
        )
        context = DailyFactoryContext(
            settings=settings,
            config_dir=config_dir,
            run_root=run_root,
            manifest_output=manifest_output,
            offline_fixture=fixture,
            environment=environment,
        )
        dependencies = (
            offline_factory(context) if dry_run else production_factory(context)
        )
    except Exception:
        parser.error("daily pipeline dependencies could not be constructed")

    try:
        manifest = run_daily(settings, dependencies)
    finally:
        dependencies.close()
    print(
        f"run_id={manifest.run_id} status={manifest.status} "
        f"published={manifest.counts.published_count} "
        f"delivered={manifest.counts.delivered_count} "
        f"artifact_hash={manifest.artifact_hash or 'none'}"
    )
    return 1 if "feedback_projection_rejected" in manifest.error_codes else 0


__all__ = [
    "AnalysisStageOutput",
    "ArtifactAuditor",
    "DailyFactoryContext",
    "DailyDependencies",
    "DailySettings",
    "DeliveryLedger",
    "ManifestStore",
    "PreparedDelivery",
    "build_offline_daily_dependencies",
    "build_production_daily_dependencies",
    "main",
    "run_daily",
]


if __name__ == "__main__":
    raise SystemExit(main())
