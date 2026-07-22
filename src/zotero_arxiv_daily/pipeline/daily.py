from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import field_validator, model_validator

from zotero_arxiv_daily.analysis.document_schemas import DocumentBatchResult
from zotero_arxiv_daily.analysis.paper_schemas import AnalysisBatchResult, EvidencePacket
from zotero_arxiv_daily.analysis.schemas import CandidateBatch, StrictModel, validate_run_id_value
from zotero_arxiv_daily.analysis.validation_schemas import ValidationBatchResult
from zotero_arxiv_daily.delivery.ledger import DeliveryLedger
from zotero_arxiv_daily.pipeline.artifacts import ArtifactAudit, ArtifactAuditor, ManifestStore
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
    def contains(self, idempotency_key: str) -> bool: ...

    def record(self, idempotency_key: str) -> None: ...


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


def run_daily(settings: DailySettings, dependencies: DailyDependencies) -> RunManifest:
    started_at = dependencies.clock()
    stages: list[StageRunResult] = []
    static_site = StaticSiteResult(status="skipped")
    feishu = FeishuRunResult(status="skipped")
    counts = RunCounts()

    try:
        candidates = dependencies.candidate_runner()
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
            status="success",
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

    try:
        build = dependencies.viewer_runner(validation)
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
        delivery = dependencies.prepare_delivery(validation)
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
        elif dependencies.delivery_ledger.contains(delivery.idempotency_key):
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
            dependencies.delivery_ledger.record(delivery.idempotency_key)
            feishu = FeishuRunResult(
                status="sent",
                delivered_count=delivery.paper_count,
                idempotency_key=delivery.idempotency_key,
            )
            counts = counts.model_copy(update={"delivered_count": delivery.paper_count})
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

    status = (
        "partial"
        if feishu.status == "failed"
        or any(stage.status == "partial" for stage in stages)
        else "success"
    )
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
    failures = sum(status != "success" for status in statuses)
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
    non_valid = sum(result.status != "validated" for result in validation.results)
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


__all__ = [
    "AnalysisStageOutput",
    "ArtifactAuditor",
    "DailyDependencies",
    "DailySettings",
    "DeliveryLedger",
    "ManifestStore",
    "PreparedDelivery",
    "run_daily",
]
