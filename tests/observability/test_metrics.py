from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.observability.metrics import (
    METRICS_SCHEMA_VERSION,
    BudgetEvaluation,
    ModelUsageMetric,
    PerformanceBudget,
    PricingPolicy,
    RunMetrics,
    StageMetric,
    calculate_cost_micro_usd,
)


HASH = "a" * 64
OTHER_HASH = "b" * 64
STAGE_NAMES = ("candidates", "documents", "analysis", "validation", "viewer", "feishu")


def _stage(name: str, **updates: object) -> StageMetric:
    values: dict[str, object] = {
        "name": name,
        "duration_ns": 10,
        "input_count": 2,
        "output_count": 1,
        "byte_count": 3,
        "cache_hit_count": 0,
        "retry_count": 0,
        "partial_failure_count": 0,
        "error_codes": (),
    }
    values.update(updates)
    return StageMetric(**values)


def _usage(**updates: object) -> ModelUsageMetric:
    values: dict[str, object] = {
        "model_identity_hash": HASH,
        "paid_call_count": 0,
        "attempt_count": 0,
        "estimated_input_tokens": 0,
        "configured_output_token_count": 0,
        "estimated_output_tokens": 0,
        "pricing_status": "not_configured",
        "estimated_cost_micro_usd": None,
        "pricing_policy_hash": None,
    }
    values.update(updates)
    return ModelUsageMetric(**values)


def _budget_evaluation(**updates: object) -> BudgetEvaluation:
    values: dict[str, object] = {
        "passed": True,
        "candidate_count": 30,
        "selected_for_llm_count": 15,
        "selected_for_analysis_count": 5,
        "successful_analysis_call_count": 0,
        "analysis_attempt_count": 0,
        "configured_output_token_count": 0,
        "offline_network_call_count": 0,
        "offline_paid_call_count": 0,
        "peak_traced_allocation_bytes": 1024,
    }
    values.update(updates)
    return BudgetEvaluation(**values)


def _metrics(**updates: object) -> RunMetrics:
    stages = tuple(_stage(name) for name in STAGE_NAMES)
    values: dict[str, object] = {
        "run_id": "20260726T010203Z-local",
        "trigger": "local",
        "config_hash": HASH,
        "artifact_hash": OTHER_HASH,
        "stages": stages,
        "duration_ns": 60,
        "byte_count": 18,
        "cache_hit_count": 0,
        "retry_count": 0,
        "partial_failure_count": 0,
        "model_usage": _usage(),
        "peak_traced_allocation_bytes": 1024,
        "budget": _budget_evaluation(),
    }
    values.update(updates)
    return RunMetrics(**values)


def test_metric_contracts_are_strict_frozen_versioned_and_canonical() -> None:
    metrics = _metrics()

    assert metrics.schema_version == METRICS_SCHEMA_VERSION == "1.0"
    assert metrics.collector_version == "stage9-collector-v1"
    assert metrics.token_estimator_version == "stage9-char-estimator-v1"
    assert json.loads(metrics.to_canonical_json()) == metrics.model_dump(mode="json")
    assert metrics.to_canonical_json() == _metrics().to_canonical_json()

    with pytest.raises(ValidationError, match="Extra inputs"):
        StageMetric(**_stage("analysis").model_dump(), prompt="private")
    with pytest.raises(ValidationError, match="frozen"):
        metrics.duration_ns = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration_ns", -1),
        ("duration_ns", True),
        ("input_count", -1),
        ("byte_count", True),
        ("retry_count", -1),
    ],
)
def test_stage_metric_rejects_negative_and_boolean_integer_fields(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _stage("analysis", **{field: value})


def test_stage_metric_rejects_unsafe_duplicate_and_inconsistent_errors() -> None:
    with pytest.raises(ValidationError, match="safe error"):
        _stage("analysis", error_codes=("https://private.example/token",))
    with pytest.raises(ValidationError, match="unique"):
        _stage("analysis", error_codes=("analysis_failed", "analysis_failed"))
    with pytest.raises(ValidationError, match="partial"):
        _stage("analysis", partial_failure_count=1)

    partial = _stage(
        "analysis",
        partial_failure_count=1,
        error_codes=("analysis_paper_failed",),
    )
    assert partial.partial_failure_count == 1


def test_pricing_policy_hash_and_integer_cost_are_deterministic() -> None:
    policy = PricingPolicy(
        input_micro_usd_per_million_tokens=2_000_000,
        output_micro_usd_per_million_tokens=8_000_000,
        maximum_batch_cost_micro_usd=50_000,
    )

    assert len(policy.policy_hash) == 64
    assert policy.policy_hash == PricingPolicy(**policy.model_dump()).policy_hash
    assert calculate_cost_micro_usd(
        input_tokens=1_000,
        output_tokens=500,
        policy=policy,
    ) == 6_000
    assert calculate_cost_micro_usd(
        input_tokens=1,
        output_tokens=0,
        policy=PricingPolicy(
            input_micro_usd_per_million_tokens=1,
            output_micro_usd_per_million_tokens=0,
            maximum_batch_cost_micro_usd=1,
        ),
    ) == 1


def test_model_usage_requires_consistent_pricing_and_attempt_counts() -> None:
    policy = PricingPolicy(
        input_micro_usd_per_million_tokens=1,
        output_micro_usd_per_million_tokens=1,
        maximum_batch_cost_micro_usd=5,
    )
    configured = _usage(
        paid_call_count=1,
        attempt_count=2,
        estimated_input_tokens=3,
        configured_output_token_count=8_192,
        estimated_output_tokens=4,
        pricing_status="configured",
        estimated_cost_micro_usd=2,
        pricing_policy_hash=policy.policy_hash,
    )
    assert configured.attempt_count == 2

    with pytest.raises(ValidationError, match="pricing"):
        _usage(pricing_status="configured")
    with pytest.raises(ValidationError, match="not configured"):
        _usage(estimated_cost_micro_usd=0)
    with pytest.raises(ValidationError, match="attempt"):
        _usage(paid_call_count=2, attempt_count=1)
    with pytest.raises(ValidationError, match="SHA-256"):
        _usage(model_identity_hash="model-name")


def test_performance_budget_has_exact_stage9_defaults_and_is_strict() -> None:
    budget = PerformanceBudget()

    assert budget.candidate_pool_limit == 30
    assert budget.llm_rerank_limit == 15
    assert budget.full_analysis_limit == 5
    assert budget.successful_analysis_call_limit == 5
    assert budget.analysis_attempt_limit == 15
    assert budget.configured_output_token_limit == 40_960
    assert budget.offline_network_call_limit == 0
    assert budget.offline_paid_call_limit == 0
    assert budget.median_runtime_limit_ms == 2_000
    assert budget.p95_runtime_limit_ms == 3_000
    assert budget.peak_traced_allocation_limit_bytes == 256 * 1024 * 1024
    with pytest.raises(ValidationError):
        PerformanceBudget(candidate_pool_limit=True)


def test_run_metrics_recomputes_ordered_aggregates() -> None:
    stages = tuple(_stage(name) for name in STAGE_NAMES)

    with pytest.raises(ValidationError, match="order"):
        _metrics(stages=tuple(reversed(stages)))
    with pytest.raises(ValidationError, match="duration"):
        _metrics(duration_ns=61)
    with pytest.raises(ValidationError, match="byte"):
        _metrics(byte_count=19)
    with pytest.raises(ValidationError, match="cache"):
        _metrics(cache_hit_count=1)
    with pytest.raises(ValidationError, match="retry"):
        _metrics(retry_count=1)
    with pytest.raises(ValidationError, match="partial"):
        _metrics(partial_failure_count=1)
    with pytest.raises(ValidationError, match="SHA-256"):
        _metrics(config_hash="secret")


def test_budget_evaluation_must_match_fixed_limits() -> None:
    assert _budget_evaluation().passed is True
    with pytest.raises(ValidationError, match="passed"):
        _budget_evaluation(passed=False)
    with pytest.raises(ValidationError, match="passed"):
        _budget_evaluation(analysis_attempt_count=16)
    failed = _budget_evaluation(analysis_attempt_count=16, passed=False)
    assert failed.passed is False


def test_serialized_metrics_have_no_private_or_free_form_surface() -> None:
    payload = _metrics().model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True).casefold()
    forbidden = (
        "api_key",
        "credential",
        "secret",
        "prompt",
        "response",
        "paper_id",
        "title",
        "author",
        "abstract",
        "full_text",
        "zotero",
        "chat_id",
        "app_secret",
        "http://",
        "https://",
        "\\users\\",
        "/home/",
        "traceback",
        "exception",
        "hostname",
        "username",
    )

    assert all(marker not in serialized for marker in forbidden)
    allowed_string_fields = {
            "schema_version",
            "metric_version",
            "collector_version",
        "token_estimator_version",
        "run_id",
        "trigger",
        "config_hash",
        "artifact_hash",
        "name",
        "model_identity_hash",
        "pricing_status",
        "pricing_policy_hash",
    }

    def walk(value: object, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, child_key)
        elif isinstance(value, list):
            for child_value in value:
                walk(child_value, key)
        elif isinstance(value, str):
            assert key in allowed_string_fields or key == "error_codes"

    walk(payload)
