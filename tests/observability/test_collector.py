from __future__ import annotations

from collections.abc import Iterator

import pytest

from zotero_arxiv_daily.observability.collector import (
    BoundedTokenEstimator,
    MetricsCollectionError,
    MetricsSession,
)
from zotero_arxiv_daily.observability.metrics import PricingPolicy


HASH = "a" * 64
STAGES = ("candidates", "documents", "analysis", "validation", "viewer", "feishu")


def _clock(values: tuple[int, ...]) -> callable:
    iterator: Iterator[int] = iter(values)
    return lambda: next(iterator)


def _session(*, clock=None, peak=lambda: 123, pricing=None) -> MetricsSession:
    return MetricsSession(
        run_id="20260726T010203Z-local",
        trigger="local",
        config_hash=HASH,
        monotonic_ns=clock or _clock(tuple(range(0, 13))),
        peak_memory_sampler=peak,
        token_estimator=BoundedTokenEstimator(),
        pricing_policy=pricing,
    )


def _record_all_stages(session: MetricsSession) -> None:
    for name in STAGES:
        with session.stage(name) as observation:
            observation.set_counts(input_count=1, output_count=1, byte_count=2)


def test_session_records_injected_stage_timing_and_aggregates() -> None:
    session = _session(clock=_clock((10, 20, 20, 40, 40, 70, 70, 110, 110, 160, 160, 220)))

    _record_all_stages(session)
    metrics = session.finalize(
        artifact_hash=HASH,
        candidate_count=30,
        selected_for_llm_count=15,
        selected_for_analysis_count=5,
        offline_network_call_count=0,
    )

    assert [stage.duration_ns for stage in metrics.stages] == [10, 20, 30, 40, 50, 60]
    assert metrics.duration_ns == 210
    assert metrics.byte_count == 12
    assert metrics.peak_traced_allocation_bytes == 123


def test_stage_observation_records_safe_failure_and_freezes_after_exit() -> None:
    session = _session()
    observation = None
    with pytest.raises(RuntimeError, match="private dynamic message"):
        with session.stage("candidates") as observation:
            observation.set_counts(input_count=2)
            raise RuntimeError("private dynamic message")

    assert observation is not None
    with pytest.raises(MetricsCollectionError, match="closed"):
        observation.set_counts(output_count=1)
    assert session.stage_metrics[0].error_codes == ("stage_callback_failed",)
    assert "private dynamic message" not in session.stage_metrics[0].model_dump_json()


def test_model_attempts_are_counted_without_retaining_text() -> None:
    session = _session()
    private_prompt = "PRIVATE PROMPT Zotero title and abstract"
    private_response = "PRIVATE MODEL RESPONSE"

    session.record_model_attempt(
        model_identity="provider:model",
        input_texts=(private_prompt,),
        output_text=None,
        configured_output_tokens=8192,
        succeeded=False,
        paid=True,
    )
    session.record_model_attempt(
        model_identity="provider:model",
        input_texts=(private_prompt,),
        output_text=private_response,
        configured_output_tokens=8192,
        succeeded=True,
        paid=True,
    )
    usage = session.model_usage

    assert usage.attempt_count == 2
    assert usage.paid_call_count == 2
    assert usage.successful_call_count == 1
    assert usage.configured_output_token_count == 8_192
    assert usage.estimated_input_tokens > 0
    assert usage.estimated_output_tokens > 0
    assert private_prompt not in usage.model_dump_json()
    assert private_response not in usage.model_dump_json()


def test_cache_hit_records_no_attempt_or_paid_call() -> None:
    session = _session()
    session.record_cache_hit()

    assert session.model_usage.attempt_count == 0
    assert session.model_usage.paid_call_count == 0
    assert session.model_usage.successful_call_count == 0


def test_offline_fake_success_is_successful_but_never_paid() -> None:
    session = _session()
    session.record_model_attempt(
        model_identity="offline-fake",
        input_texts=("synthetic",),
        output_text="synthetic",
        configured_output_tokens=8192,
        succeeded=True,
        paid=False,
    )

    assert session.model_usage.successful_call_count == 1
    assert session.model_usage.paid_call_count == 0
    assert session.model_usage.attempt_count == 1


def test_pricing_is_integer_bounded_and_unconfigured_is_not_zero_cost() -> None:
    policy = PricingPolicy(
        input_micro_usd_per_million_tokens=2_000_000,
        output_micro_usd_per_million_tokens=8_000_000,
        maximum_batch_cost_micro_usd=100_000,
    )
    configured = _session(pricing=policy)
    configured.record_model_attempt(
        model_identity="provider:model",
        input_texts=("abcd",),
        output_text="abcd",
        configured_output_tokens=10,
        succeeded=True,
        paid=True,
    )
    assert configured.model_usage.pricing_status == "configured"
    assert configured.model_usage.estimated_cost_micro_usd == 10
    assert configured.model_usage.pricing_policy_hash == policy.policy_hash

    unconfigured = _session()
    assert unconfigured.model_usage.pricing_status == "not_configured"
    assert unconfigured.model_usage.estimated_cost_micro_usd is None


def test_estimator_is_deterministic_bounded_and_rejects_oversize_input() -> None:
    estimator = BoundedTokenEstimator(max_input_bytes=8)

    assert estimator.estimate("") == 0
    assert estimator.estimate("abcd") == 1
    assert estimator.estimate("abcde") == 2
    assert estimator.estimate("论文") == 2
    with pytest.raises(MetricsCollectionError, match="too large"):
        estimator.estimate("123456789")


def test_finalize_requires_exact_stage_order_and_only_runs_once() -> None:
    session = _session()
    with pytest.raises(MetricsCollectionError, match="pipeline order"):
        with session.stage("documents"):
            pass

    incomplete = _session()
    with incomplete.stage("candidates"):
        pass
    with pytest.raises(MetricsCollectionError, match="pipeline order"):
        incomplete.finalize(
            artifact_hash=None,
            candidate_count=0,
            selected_for_llm_count=0,
            selected_for_analysis_count=0,
            offline_network_call_count=0,
        )

    complete = _session()
    _record_all_stages(complete)
    complete.finalize(
        artifact_hash=None,
        candidate_count=30,
        selected_for_llm_count=15,
        selected_for_analysis_count=5,
        offline_network_call_count=0,
    )
    with pytest.raises(MetricsCollectionError, match="finalized"):
        complete.finalize(
            artifact_hash=None,
            candidate_count=30,
            selected_for_llm_count=15,
            selected_for_analysis_count=5,
            offline_network_call_count=0,
        )


def test_collection_boundary_rejects_mixed_model_identity_and_sampler_failure() -> None:
    session = _session()
    session.record_model_attempt(
        model_identity="one",
        input_texts=(),
        output_text=None,
        configured_output_tokens=1,
        succeeded=False,
        paid=False,
    )
    with pytest.raises(MetricsCollectionError, match="model identity"):
        session.record_model_attempt(
            model_identity="two",
            input_texts=(),
            output_text=None,
            configured_output_tokens=1,
            succeeded=False,
            paid=False,
        )

    broken = _session(peak=lambda: (_ for _ in ()).throw(RuntimeError("private")))
    _record_all_stages(broken)
    with pytest.raises(MetricsCollectionError, match="peak memory"):
        broken.finalize(
            artifact_hash=None,
            candidate_count=0,
            selected_for_llm_count=0,
            selected_for_analysis_count=0,
            offline_network_call_count=0,
        )
