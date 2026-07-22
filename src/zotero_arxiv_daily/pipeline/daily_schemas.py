from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel, validate_run_id_value


RUN_MANIFEST_SCHEMA_VERSION = "1.0"
PIPELINE_VERSION = "stage7-v1"
CACHE_VERSION = "stage7-cache-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
STAGE_ORDER = ("candidates", "documents", "analysis", "validation", "viewer", "feishu")

Trigger = Literal["scheduled", "manual", "local"]
RunStatus = Literal["success", "empty", "partial", "failed"]
StageName = Literal["candidates", "documents", "analysis", "validation", "viewer", "feishu"]
StageStatus = Literal["success", "empty", "partial", "failed", "skipped"]


def _non_empty(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("value must be a lowercase SHA-256 digest")
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _safe_error_codes(value: tuple[str, ...]) -> tuple[str, ...]:
    if any(not _SAFE_ERROR_CODE_RE.fullmatch(code) for code in value):
        raise ValueError("error_codes must contain only safe error codes")
    if len(value) != len(set(value)):
        raise ValueError("error_codes must be unique")
    return value


class StageRunResult(StrictModel):
    name: StageName
    status: StageStatus
    input_count: int = Field(default=0, ge=0)
    output_count: int = Field(default=0, ge=0)
    cache_hit_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    partial_failure_count: int = Field(default=0, ge=0)
    error_codes: tuple[str, ...] = ()

    @field_validator("error_codes")
    @classmethod
    def validate_error_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_error_codes(value)


class RunCounts(StrictModel):
    input_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    selected_count: int = Field(default=0, ge=0)
    analyzed_count: int = Field(default=0, ge=0)
    validated_count: int = Field(default=0, ge=0)
    published_count: int = Field(default=0, ge=0)
    delivered_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_monotonic_counts(self) -> Self:
        if self.selected_count > self.candidate_count:
            raise ValueError("selected count cannot exceed candidate count")
        if self.analyzed_count > self.selected_count:
            raise ValueError("analyzed count cannot exceed selected count")
        if self.validated_count > self.analyzed_count:
            raise ValueError("validated count cannot exceed analyzed count")
        if self.published_count > self.validated_count:
            raise ValueError("published count cannot exceed validated count")
        if self.delivered_count > self.validated_count:
            raise ValueError("delivered count cannot exceed validated count")
        return self


class StaticSiteResult(StrictModel):
    status: Literal["success", "failed", "skipped"]
    published_count: int = Field(default=0, ge=0)
    audited_file_count: int = Field(default=0, ge=0)
    audited_byte_count: int = Field(default=0, ge=0)
    artifact_hash: str | None = None
    error_codes: tuple[str, ...] = ()

    @field_validator("artifact_hash")
    @classmethod
    def validate_artifact_hash(cls, value: str | None) -> str | None:
        return _sha256(value) if value is not None else None

    @field_validator("error_codes")
    @classmethod
    def validate_error_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_error_codes(value)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "success" and self.artifact_hash is None:
            raise ValueError("successful static site requires an artifact hash")
        if self.status != "success" and self.artifact_hash is not None:
            raise ValueError("unsuccessful static site must not expose an artifact hash")
        return self


class FeishuRunResult(StrictModel):
    status: Literal["preview", "sent", "duplicate", "failed", "skipped"]
    delivered_count: int = Field(default=0, ge=0, le=5)
    idempotency_key: str | None = None
    error_codes: tuple[str, ...] = ()

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str | None) -> str | None:
        return _sha256(value) if value is not None else None

    @field_validator("error_codes")
    @classmethod
    def validate_error_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_error_codes(value)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status in {"sent", "duplicate"} and self.idempotency_key is None:
            raise ValueError("sent or duplicate Feishu result requires an idempotency key")
        if self.status not in {"sent", "duplicate"} and self.delivered_count:
            raise ValueError("only sent or duplicate Feishu results may count delivery")
        return self


class CacheIdentity(StrictModel):
    cache_version: Literal["stage7-cache-v1"] = CACHE_VERSION
    pipeline_version: Literal["stage7-v1"] = PIPELINE_VERSION
    config_hash: str
    embedding_model: str
    embedding_version: str
    parser_version: str
    mapper_version: str
    prompt_version: str
    analysis_schema_version: str
    validator_version: str
    viewer_build_version: str
    viewer_template_version: str
    delivery_renderer_version: str

    @field_validator("config_hash")
    @classmethod
    def validate_config_hash(cls, value: str) -> str:
        return _sha256(value)

    @field_validator(
        "embedding_model",
        "embedding_version",
        "parser_version",
        "mapper_version",
        "prompt_version",
        "analysis_schema_version",
        "validator_version",
        "viewer_build_version",
        "viewer_template_version",
        "delivery_renderer_version",
    )
    @classmethod
    def normalize_identities(cls, value: str) -> str:
        return _non_empty(value)


WorkflowCacheIdentity = CacheIdentity


class RunManifest(StrictModel):
    schema_version: Literal["1.0"] = RUN_MANIFEST_SCHEMA_VERSION
    pipeline_version: Literal["stage7-v1"] = PIPELINE_VERSION
    run_id: str
    trigger: Trigger
    config_hash: str
    dry_run: bool
    send_requested: bool
    started_at: datetime
    completed_at: datetime
    status: RunStatus
    stages: tuple[StageRunResult, ...]
    counts: RunCounts
    cache_hit_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    partial_failure_count: int = Field(ge=0)
    static_site: StaticSiteResult
    feishu: FeishuRunResult
    artifact_hash: str | None = None
    error_codes: tuple[str, ...] = ()

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return validate_run_id_value(value)

    @field_validator("config_hash")
    @classmethod
    def validate_config_hash(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("artifact_hash")
    @classmethod
    def validate_artifact_hash(cls, value: str | None) -> str | None:
        return _sha256(value) if value is not None else None

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_datetime(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("error_codes")
    @classmethod
    def validate_error_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_error_codes(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.dry_run and self.send_requested:
            raise ValueError("dry-run manifest cannot request sending")
        if tuple(stage.name for stage in self.stages) != STAGE_ORDER:
            raise ValueError("stages must appear exactly once in pipeline order")
        if self.cache_hit_count != sum(stage.cache_hit_count for stage in self.stages):
            raise ValueError("cache hit count must match stage totals")
        if self.retry_count != sum(stage.retry_count for stage in self.stages):
            raise ValueError("retry count must match stage totals")
        if self.partial_failure_count != sum(
            stage.partial_failure_count for stage in self.stages
        ):
            raise ValueError("partial failure count must match stage totals")
        if self.counts.published_count != self.static_site.published_count:
            raise ValueError("published count must match the static site result")
        if self.counts.delivered_count != self.feishu.delivered_count:
            raise ValueError("delivered count must match the Feishu result")
        if self.artifact_hash != self.static_site.artifact_hash:
            raise ValueError("artifact hash must match the static site result")
        if self.feishu.status == "sent" and (self.dry_run or not self.send_requested):
            raise ValueError("sent Feishu result requires an explicit live send request")
        return self
