from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel, validate_run_id_value


METRICS_SCHEMA_VERSION = "1.0"
METRIC_VERSION = "stage9-metric-v1"
COLLECTOR_VERSION = "stage9-collector-v1"
TOKEN_ESTIMATOR_VERSION = "stage9-char-estimator-v1"
STAGE_NAMES = ("candidates", "documents", "analysis", "validation", "viewer", "feishu")

StageName = Literal["candidates", "documents", "analysis", "validation", "viewer", "feishu"]
Trigger = Literal["scheduled", "manual", "local"]
PricingStatus = Literal["configured", "not_configured"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_COUNT_FIELDS = (
    "duration_ns",
    "input_count",
    "output_count",
    "byte_count",
    "cache_hit_count",
    "retry_count",
    "partial_failure_count",
)


def _canonical_json(value: StrictModel) -> str:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("value must be a lowercase SHA-256 digest")
    return value


def _reject_boolean(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("count fields must be integers, not booleans")
    return value


def _safe_error_codes(value: tuple[str, ...]) -> tuple[str, ...]:
    if any(not _SAFE_ERROR_CODE_RE.fullmatch(code) for code in value):
        raise ValueError("error_codes must contain only safe error codes")
    if len(value) != len(set(value)):
        raise ValueError("error_codes must be unique")
    return value


class StageMetric(StrictModel):
    metric_version: Literal["stage9-metric-v1"] = METRIC_VERSION
    name: StageName
    duration_ns: int = Field(ge=0)
    input_count: int = Field(default=0, ge=0)
    output_count: int = Field(default=0, ge=0)
    byte_count: int = Field(default=0, ge=0)
    cache_hit_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    partial_failure_count: int = Field(default=0, ge=0)
    error_codes: tuple[str, ...] = ()

    @field_validator(*_COUNT_FIELDS, mode="before")
    @classmethod
    def reject_boolean_counts(cls, value: object) -> object:
        return _reject_boolean(value)

    @field_validator("error_codes")
    @classmethod
    def validate_error_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_error_codes(value)

    @model_validator(mode="after")
    def validate_partial_failure(self) -> Self:
        if self.partial_failure_count and not self.error_codes:
            raise ValueError("partial failure count requires a safe error code")
        return self


class PricingPolicy(StrictModel):
    input_micro_usd_per_million_tokens: int = Field(ge=0)
    output_micro_usd_per_million_tokens: int = Field(ge=0)
    maximum_batch_cost_micro_usd: int = Field(ge=0)

    @field_validator(
        "input_micro_usd_per_million_tokens",
        "output_micro_usd_per_million_tokens",
        "maximum_batch_cost_micro_usd",
        mode="before",
    )
    @classmethod
    def reject_boolean_counts(cls, value: object) -> object:
        return _reject_boolean(value)

    @property
    def policy_hash(self) -> str:
        return hashlib.sha256(_canonical_json(self).encode("utf-8")).hexdigest()


def calculate_cost_micro_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    policy: PricingPolicy,
) -> int:
    input_tokens = _validate_plain_count(input_tokens, name="input_tokens")
    output_tokens = _validate_plain_count(output_tokens, name="output_tokens")
    numerator = (
        input_tokens * policy.input_micro_usd_per_million_tokens
        + output_tokens * policy.output_micro_usd_per_million_tokens
    )
    return (numerator + 999_999) // 1_000_000


def _validate_plain_count(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


class ModelUsageMetric(StrictModel):
    model_identity_hash: str
    paid_call_count: int = Field(default=0, ge=0)
    successful_call_count: int = Field(default=0, ge=0)
    attempt_count: int = Field(default=0, ge=0)
    estimated_input_tokens: int = Field(default=0, ge=0)
    configured_output_token_count: int = Field(default=0, ge=0)
    estimated_output_tokens: int = Field(default=0, ge=0)
    pricing_status: PricingStatus = "not_configured"
    estimated_cost_micro_usd: int | None = Field(default=None, ge=0)
    pricing_policy_hash: str | None = None

    @field_validator(
        "paid_call_count",
        "successful_call_count",
        "attempt_count",
        "estimated_input_tokens",
        "configured_output_token_count",
        "estimated_output_tokens",
        "estimated_cost_micro_usd",
        mode="before",
    )
    @classmethod
    def reject_boolean_counts(cls, value: object) -> object:
        return _reject_boolean(value) if value is not None else value

    @field_validator("model_identity_hash")
    @classmethod
    def validate_model_identity_hash(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("pricing_policy_hash")
    @classmethod
    def validate_pricing_policy_hash(cls, value: str | None) -> str | None:
        return _sha256(value) if value is not None else None

    @model_validator(mode="after")
    def validate_usage(self) -> Self:
        if (
            self.successful_call_count > self.attempt_count
            or self.paid_call_count > self.attempt_count
        ):
            raise ValueError("successful and paid call counts cannot exceed attempts")
        if self.pricing_status == "configured":
            if self.estimated_cost_micro_usd is None or self.pricing_policy_hash is None:
                raise ValueError("configured pricing requires cost and pricing policy hash")
        elif self.estimated_cost_micro_usd is not None or self.pricing_policy_hash is not None:
            raise ValueError("pricing not configured must not expose cost or policy hash")
        return self


class PerformanceBudget(StrictModel):
    candidate_pool_limit: int = Field(default=30, ge=1)
    llm_rerank_limit: int = Field(default=15, ge=1)
    full_analysis_limit: int = Field(default=5, ge=1)
    successful_analysis_call_limit: int = Field(default=5, ge=0)
    analysis_attempt_limit: int = Field(default=15, ge=0)
    configured_output_token_limit: int = Field(default=40_960, ge=0)
    offline_network_call_limit: int = Field(default=0, ge=0)
    offline_paid_call_limit: int = Field(default=0, ge=0)
    median_runtime_limit_ms: int = Field(default=2_000, ge=1)
    p95_runtime_limit_ms: int = Field(default=3_000, ge=1)
    peak_traced_allocation_limit_bytes: int = Field(
        default=256 * 1024 * 1024, ge=1
    )

    @field_validator("*", mode="before")
    @classmethod
    def reject_boolean_counts(cls, value: object) -> object:
        return _reject_boolean(value)

    @model_validator(mode="after")
    def validate_limit_order(self) -> Self:
        if not self.full_analysis_limit <= self.llm_rerank_limit <= self.candidate_pool_limit:
            raise ValueError("selection limits must preserve 30/15/5 ordering")
        if self.p95_runtime_limit_ms < self.median_runtime_limit_ms:
            raise ValueError("p95 runtime limit must not be below the median limit")
        return self


class BudgetEvaluation(StrictModel):
    passed: bool
    candidate_count: int = Field(ge=0)
    selected_for_llm_count: int = Field(ge=0)
    selected_for_analysis_count: int = Field(ge=0)
    successful_analysis_call_count: int = Field(ge=0)
    analysis_attempt_count: int = Field(ge=0)
    configured_output_token_count: int = Field(ge=0)
    offline_network_call_count: int = Field(ge=0)
    offline_paid_call_count: int = Field(ge=0)
    peak_traced_allocation_bytes: int = Field(ge=0)
    estimated_cost_micro_usd: int | None = Field(default=None, ge=0)
    maximum_batch_cost_micro_usd: int | None = Field(default=None, ge=0)

    @field_validator(
        "candidate_count",
        "selected_for_llm_count",
        "selected_for_analysis_count",
        "successful_analysis_call_count",
        "analysis_attempt_count",
        "configured_output_token_count",
        "offline_network_call_count",
        "offline_paid_call_count",
        "peak_traced_allocation_bytes",
        "estimated_cost_micro_usd",
        "maximum_batch_cost_micro_usd",
        mode="before",
    )
    @classmethod
    def reject_boolean_counts(cls, value: object) -> object:
        return _reject_boolean(value) if value is not None else value

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        limits = PerformanceBudget()
        expected = (
            self.candidate_count <= limits.candidate_pool_limit
            and self.selected_for_llm_count <= limits.llm_rerank_limit
            and self.selected_for_analysis_count <= limits.full_analysis_limit
            and self.successful_analysis_call_count
            <= limits.successful_analysis_call_limit
            and self.analysis_attempt_count <= limits.analysis_attempt_limit
            and self.configured_output_token_count
            <= limits.configured_output_token_limit
            and self.offline_network_call_count <= limits.offline_network_call_limit
            and self.offline_paid_call_count <= limits.offline_paid_call_limit
            and self.peak_traced_allocation_bytes
            <= limits.peak_traced_allocation_limit_bytes
            and (
                self.maximum_batch_cost_micro_usd is None
                or (
                    self.estimated_cost_micro_usd is not None
                    and self.estimated_cost_micro_usd
                    <= self.maximum_batch_cost_micro_usd
                )
            )
        )
        if (self.estimated_cost_micro_usd is None) != (
            self.maximum_batch_cost_micro_usd is None
        ):
            raise ValueError("pricing budget fields must be configured together")
        if self.passed != expected:
            raise ValueError(f"passed must be {expected}")
        return self


class RunMetrics(StrictModel):
    schema_version: Literal["1.0"] = METRICS_SCHEMA_VERSION
    collector_version: Literal["stage9-collector-v1"] = COLLECTOR_VERSION
    token_estimator_version: Literal["stage9-char-estimator-v1"] = (
        TOKEN_ESTIMATOR_VERSION
    )
    run_id: str
    trigger: Trigger
    config_hash: str
    artifact_hash: str | None = None
    stages: tuple[StageMetric, ...]
    duration_ns: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    cache_hit_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    partial_failure_count: int = Field(ge=0)
    model_usage: ModelUsageMetric
    peak_traced_allocation_bytes: int = Field(ge=0)
    budget: BudgetEvaluation

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

    @field_validator(
        "duration_ns",
        "byte_count",
        "cache_hit_count",
        "retry_count",
        "partial_failure_count",
        "peak_traced_allocation_bytes",
        mode="before",
    )
    @classmethod
    def reject_boolean_counts(cls, value: object) -> object:
        return _reject_boolean(value)

    @model_validator(mode="after")
    def validate_aggregates(self) -> Self:
        if tuple(stage.name for stage in self.stages) != STAGE_NAMES:
            raise ValueError("stage metrics must appear exactly once in pipeline order")
        aggregates = {
            "duration": (self.duration_ns, sum(stage.duration_ns for stage in self.stages)),
            "byte": (self.byte_count, sum(stage.byte_count for stage in self.stages)),
            "cache": (
                self.cache_hit_count,
                sum(stage.cache_hit_count for stage in self.stages),
            ),
            "retry": (self.retry_count, sum(stage.retry_count for stage in self.stages)),
            "partial": (
                self.partial_failure_count,
                sum(stage.partial_failure_count for stage in self.stages),
            ),
        }
        for label, (observed, expected) in aggregates.items():
            if observed != expected:
                raise ValueError(f"{label} aggregate must match stage totals")
        if (
            self.peak_traced_allocation_bytes
            != self.budget.peak_traced_allocation_bytes
        ):
            raise ValueError("peak allocation must match the budget evaluation")
        return self

    def to_canonical_json(self) -> str:
        return _canonical_json(self)
