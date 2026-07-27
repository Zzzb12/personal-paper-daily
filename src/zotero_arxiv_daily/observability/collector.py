from __future__ import annotations

import hashlib
import time
import tracemalloc
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from zotero_arxiv_daily.analysis.client import AnalysisRequest
from zotero_arxiv_daily.observability.metrics import (
    BudgetEvaluation,
    ModelUsageMetric,
    PricingPolicy,
    RunMetrics,
    STAGE_NAMES,
    StageMetric,
    calculate_cost_micro_usd,
)


class MetricsCollectionError(RuntimeError):
    """A fixed-message error raised only at the local metrics boundary."""


class BoundedTokenEstimator:
    def __init__(self, *, max_input_bytes: int = 1024 * 1024) -> None:
        if isinstance(max_input_bytes, bool) or max_input_bytes < 1:
            raise ValueError("max_input_bytes must be a positive integer")
        self._max_input_bytes = max_input_bytes

    def estimate(self, text: str) -> int:
        if not isinstance(text, str):
            raise MetricsCollectionError("token input must be text")
        length = len(text.encode("utf-8"))
        if length > self._max_input_bytes:
            raise MetricsCollectionError("token input is too large")
        return (length + 3) // 4


def default_peak_memory_sampler() -> int:
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    return tracemalloc.get_traced_memory()[1]


@dataclass
class _StageObservation:
    name: str
    _closed: bool = False
    input_count: int = 0
    output_count: int = 0
    byte_count: int = 0
    cache_hit_count: int = 0
    retry_count: int = 0
    partial_failure_count: int = 0
    error_codes: tuple[str, ...] = ()

    def set_counts(self, **updates: object) -> None:
        if self._closed:
            raise MetricsCollectionError("stage observation is closed")
        allowed = {
            "input_count",
            "output_count",
            "byte_count",
            "cache_hit_count",
            "retry_count",
            "partial_failure_count",
            "error_codes",
        }
        if not set(updates).issubset(allowed):
            raise MetricsCollectionError("stage observation field is not allowed")
        for name, value in updates.items():
            if name == "error_codes":
                if not isinstance(value, tuple):
                    raise MetricsCollectionError("stage error codes must be a tuple")
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MetricsCollectionError("stage counts must be non-negative integers")
            setattr(self, name, value)


class MetricsSession:
    def __init__(
        self,
        *,
        run_id: str,
        trigger: str,
        config_hash: str,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
        peak_memory_sampler: Callable[[], int] = default_peak_memory_sampler,
        token_estimator: BoundedTokenEstimator | None = None,
        pricing_policy: PricingPolicy | None = None,
    ) -> None:
        self._identity = {
            "run_id": run_id,
            "trigger": trigger,
            "config_hash": config_hash,
        }
        self._monotonic_ns = monotonic_ns
        self._peak_memory_sampler = peak_memory_sampler
        self._token_estimator = token_estimator or BoundedTokenEstimator()
        self._pricing_policy = pricing_policy
        self._stages: list[StageMetric] = []
        self._finalized = False
        self._model_identity_hash: str | None = None
        self._paid_calls = 0
        self._successful_calls = 0
        self._attempts = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._configured_output_tokens = 0
        self._collection_failed = False

    @property
    def stage_metrics(self) -> tuple[StageMetric, ...]:
        return tuple(self._stages)

    @property
    def collection_failed(self) -> bool:
        return self._collection_failed

    def mark_collection_failed(self) -> None:
        self._collection_failed = True

    def observe(self, name: str, callback: Callable[[], object]) -> object:
        """Run content even when the observational boundary itself fails."""
        if self._collection_failed:
            return callback()
        try:
            expected_index = len(self._stages)
            if expected_index >= len(STAGE_NAMES) or name != STAGE_NAMES[expected_index]:
                raise MetricsCollectionError("stage metrics must follow pipeline order")
            observation = _StageObservation(name=name)
            started = self._safe_monotonic()
        except Exception:
            self._collection_failed = True
            return callback()

        try:
            result = callback()
        except Exception:
            try:
                self._complete_stage(observation, started=started, failed=True)
            except Exception:
                self._collection_failed = True
            raise
        try:
            self._complete_stage(observation, started=started, failed=False)
        except Exception:
            self._collection_failed = True
        return result

    def reconcile_stage_results(
        self,
        results: tuple[object, ...],
        *,
        byte_counts: dict[str, int] | None = None,
    ) -> None:
        if self._finalized or len(results) != len(self._stages):
            raise MetricsCollectionError("stage reconciliation is invalid")
        supplied_bytes = byte_counts or {}
        reconciled: list[StageMetric] = []
        for metric, result in zip(self._stages, results, strict=True):
            if getattr(result, "name", None) != metric.name:
                raise MetricsCollectionError("stage reconciliation order differs")
            reconciled.append(
                StageMetric(
                    name=metric.name,
                    duration_ns=metric.duration_ns,
                    input_count=getattr(result, "input_count"),
                    output_count=getattr(result, "output_count"),
                    byte_count=supplied_bytes.get(metric.name, metric.byte_count),
                    cache_hit_count=getattr(result, "cache_hit_count"),
                    retry_count=getattr(result, "retry_count"),
                    partial_failure_count=getattr(result, "partial_failure_count"),
                    error_codes=getattr(result, "error_codes"),
                )
            )
        self._stages = reconciled

    @contextmanager
    def stage(self, name: str) -> Iterator[_StageObservation]:
        if self._finalized:
            raise MetricsCollectionError("metrics session is finalized")
        expected_index = len(self._stages)
        if expected_index >= len(STAGE_NAMES) or name != STAGE_NAMES[expected_index]:
            raise MetricsCollectionError("stage metrics must follow pipeline order")
        observation = _StageObservation(name=name)
        started = self._safe_monotonic()
        failed = False
        try:
            yield observation
        except Exception:
            failed = True
            raise
        finally:
            self._complete_stage(observation, started=started, failed=failed)

    def _complete_stage(
        self,
        observation: _StageObservation,
        *,
        started: int,
        failed: bool,
    ) -> None:
        completed = self._safe_monotonic()
        observation._closed = True
        error_codes = observation.error_codes
        if failed and not error_codes:
            error_codes = ("stage_callback_failed",)
        self._stages.append(
            StageMetric(
                name=observation.name,
                duration_ns=max(completed - started, 0),
                input_count=observation.input_count,
                output_count=observation.output_count,
                byte_count=observation.byte_count,
                cache_hit_count=observation.cache_hit_count,
                retry_count=observation.retry_count,
                partial_failure_count=observation.partial_failure_count,
                error_codes=error_codes,
            )
        )

    def _safe_monotonic(self) -> int:
        try:
            value = self._monotonic_ns()
        except Exception:
            raise MetricsCollectionError("monotonic clock failed") from None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MetricsCollectionError("monotonic clock returned an invalid value")
        return value

    def record_model_attempt(
        self,
        *,
        model_identity: str,
        input_texts: tuple[str, ...],
        output_text: str | None,
        configured_output_tokens: int,
        succeeded: bool,
        paid: bool,
    ) -> None:
        if self._finalized:
            raise MetricsCollectionError("metrics session is finalized")
        digest = hashlib.sha256(model_identity.encode("utf-8")).hexdigest()
        if self._model_identity_hash is not None and digest != self._model_identity_hash:
            raise MetricsCollectionError("model identity changed during the run")
        if (
            isinstance(configured_output_tokens, bool)
            or configured_output_tokens < 0
        ):
            raise MetricsCollectionError("configured output token count is invalid")
        self._model_identity_hash = digest
        input_tokens = sum(self._token_estimator.estimate(text) for text in input_texts)
        output_tokens = (
            self._token_estimator.estimate(output_text)
            if output_text is not None
            else 0
        )
        self._attempts += 1
        self._paid_calls += int(paid)
        self._successful_calls += int(succeeded)
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        if succeeded:
            self._configured_output_tokens += configured_output_tokens

    def record_cache_hit(self) -> None:
        if self._finalized:
            raise MetricsCollectionError("metrics session is finalized")

    @property
    def model_usage(self) -> ModelUsageMetric:
        identity_hash = self._model_identity_hash or hashlib.sha256(b"none").hexdigest()
        if self._pricing_policy is None:
            pricing_status = "not_configured"
            cost = None
            policy_hash = None
        else:
            pricing_status = "configured"
            cost = calculate_cost_micro_usd(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                policy=self._pricing_policy,
            )
            policy_hash = self._pricing_policy.policy_hash
        return ModelUsageMetric(
            model_identity_hash=identity_hash,
            paid_call_count=self._paid_calls,
            successful_call_count=self._successful_calls,
            attempt_count=self._attempts,
            estimated_input_tokens=self._input_tokens,
            configured_output_token_count=self._configured_output_tokens,
            estimated_output_tokens=self._output_tokens,
            pricing_status=pricing_status,
            estimated_cost_micro_usd=cost,
            pricing_policy_hash=policy_hash,
        )

    def finalize(
        self,
        *,
        artifact_hash: str | None,
        candidate_count: int,
        selected_for_llm_count: int,
        selected_for_analysis_count: int,
        offline_network_call_count: int,
        offline_paid_call_count: int = 0,
    ) -> RunMetrics:
        if self._finalized:
            raise MetricsCollectionError("metrics session is finalized")
        if tuple(stage.name for stage in self._stages) != STAGE_NAMES:
            raise MetricsCollectionError("stage metrics must follow pipeline order")
        try:
            peak = self._peak_memory_sampler()
        except Exception:
            raise MetricsCollectionError("peak memory sampler failed") from None
        if isinstance(peak, bool) or not isinstance(peak, int) or peak < 0:
            raise MetricsCollectionError("peak memory sampler returned an invalid value")
        usage = self.model_usage
        budget_values = {
            "candidate_count": candidate_count,
            "selected_for_llm_count": selected_for_llm_count,
            "selected_for_analysis_count": selected_for_analysis_count,
            "successful_analysis_call_count": usage.successful_call_count,
            "analysis_attempt_count": usage.attempt_count,
            "configured_output_token_count": usage.configured_output_token_count,
            "offline_network_call_count": offline_network_call_count,
            "offline_paid_call_count": offline_paid_call_count,
            "peak_traced_allocation_bytes": peak,
            "estimated_cost_micro_usd": usage.estimated_cost_micro_usd,
            "maximum_batch_cost_micro_usd": (
                self._pricing_policy.maximum_batch_cost_micro_usd
                if self._pricing_policy is not None
                else None
            ),
        }
        passed = (
            candidate_count <= 30
            and selected_for_llm_count <= 15
            and selected_for_analysis_count <= 5
            and usage.successful_call_count <= 5
            and usage.attempt_count <= 15
            and usage.configured_output_token_count <= 40_960
            and offline_network_call_count == 0
            and offline_paid_call_count == 0
            and peak <= 256 * 1024 * 1024
            and (
                self._pricing_policy is None
                or (
                    usage.estimated_cost_micro_usd is not None
                    and usage.estimated_cost_micro_usd
                    <= self._pricing_policy.maximum_batch_cost_micro_usd
                )
            )
        )
        budget = BudgetEvaluation(passed=passed, **budget_values)
        self._finalized = True
        return RunMetrics(
            **self._identity,
            artifact_hash=artifact_hash,
            stages=tuple(self._stages),
            duration_ns=sum(stage.duration_ns for stage in self._stages),
            byte_count=sum(stage.byte_count for stage in self._stages),
            cache_hit_count=sum(stage.cache_hit_count for stage in self._stages),
            retry_count=sum(stage.retry_count for stage in self._stages),
            partial_failure_count=sum(
                stage.partial_failure_count for stage in self._stages
            ),
            model_usage=usage,
            peak_traced_allocation_bytes=peak,
            budget=budget,
        )


class AnalysisMetricsSink:
    def __init__(self, session: MetricsSession) -> None:
        self._session = session

    def record_attempt(
        self,
        request: object,
        response: str | None,
        *,
        model_identity: str,
        succeeded: bool,
        paid: bool,
    ) -> None:
        try:
            if not isinstance(request, AnalysisRequest):
                raise MetricsCollectionError("analysis usage request is invalid")
            self._session.record_model_attempt(
                model_identity=model_identity,
                input_texts=(request.system_prompt, request.user_prompt),
                output_text=response,
                configured_output_tokens=request.max_output_tokens,
                succeeded=succeeded,
                paid=paid,
            )
        except Exception:
            self._session.mark_collection_failed()
