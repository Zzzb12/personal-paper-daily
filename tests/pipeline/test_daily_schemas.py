from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.pipeline.daily_schemas import (
    PIPELINE_VERSION,
    CacheIdentity,
    FeishuRunResult,
    MetricsRunResult,
    RunCounts,
    RunManifest,
    StageRunResult,
    StaticSiteResult,
)


NOW = datetime(2026, 7, 22, 1, 2, 3, tzinfo=UTC)
HASH = "a" * 64
STAGE_NAMES = ("candidates", "documents", "analysis", "validation", "viewer", "feishu")


def _stages() -> tuple[StageRunResult, ...]:
    return tuple(
        StageRunResult(
            name=name,
            status="success",
            input_count=1,
            output_count=1,
        )
        for name in STAGE_NAMES
    )


def _manifest(**updates: object) -> RunManifest:
    values: dict[str, object] = {
        "run_id": "20260722T010203Z-local",
        "trigger": "local",
        "config_hash": HASH,
        "dry_run": True,
        "send_requested": False,
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=1),
        "status": "success",
        "stages": _stages(),
        "counts": RunCounts(
            input_count=1,
            candidate_count=1,
            selected_count=1,
            analyzed_count=1,
            validated_count=1,
            published_count=1,
            delivered_count=0,
        ),
        "cache_hit_count": 0,
        "retry_count": 0,
        "partial_failure_count": 0,
        "static_site": StaticSiteResult(
            status="success",
            published_count=1,
            audited_file_count=4,
            audited_byte_count=512,
            artifact_hash=HASH,
        ),
        "feishu": FeishuRunResult(status="preview", delivered_count=0),
        "artifact_hash": HASH,
        "metrics": MetricsRunResult(
            status="success",
            sidecar_hash="b" * 64,
        ),
    }
    values.update(updates)
    return RunManifest(**values)


def test_run_manifest_is_versioned_strict_and_deterministic() -> None:
    manifest = _manifest()

    assert manifest.schema_version == "1.1"
    assert manifest.pipeline_version == PIPELINE_VERSION == "stage9-v1"
    assert manifest.model_dump(mode="json")["trigger"] == "local"
    with pytest.raises(ValidationError, match="Extra inputs"):
        RunManifest(**manifest.model_dump(), prompt="private prompt")


def test_metrics_run_result_has_strict_status_and_hash_contract() -> None:
    success = MetricsRunResult(status="success", sidecar_hash=HASH)
    assert success.sidecar_hash == HASH
    with pytest.raises(ValidationError, match="hash"):
        MetricsRunResult(status="success")
    with pytest.raises(ValidationError, match="hash"):
        MetricsRunResult(status="failed", sidecar_hash=HASH)
    with pytest.raises(ValidationError, match="error"):
        MetricsRunResult(status="failed")
    failed = MetricsRunResult(
        status="failed",
        error_codes=("metrics_persistence_failed",),
    )
    assert failed.sidecar_hash is None
    with pytest.raises(ValidationError, match="Extra inputs"):
        MetricsRunResult(status="skipped", path="private/path")


def test_manifest_has_no_field_for_secrets_prompts_full_text_or_exceptions() -> None:
    forbidden_fragments = (
        "secret",
        "credential",
        "prompt",
        "full_text",
        "abstract",
        "zotero",
        "exception",
        "traceback",
        "api_key",
    )

    field_names = set(RunManifest.model_fields)

    assert not {
        name for name in field_names if any(fragment in name for fragment in forbidden_fragments)
    }


def test_manifest_rejects_dynamic_error_text_and_duplicate_codes() -> None:
    with pytest.raises(ValidationError, match="safe error code"):
        _manifest(error_codes=("https://user:password@example.test/token",))
    with pytest.raises(ValidationError, match="unique"):
        _manifest(error_codes=("viewer_stage_failed", "viewer_stage_failed"))


def test_manifest_rejects_unsafe_send_and_artifact_inconsistency() -> None:
    with pytest.raises(ValidationError, match="dry-run"):
        _manifest(send_requested=True)
    with pytest.raises(ValidationError, match="artifact hash"):
        _manifest(artifact_hash="b" * 64)
    with pytest.raises(ValidationError, match="published count"):
        _manifest(
            counts=RunCounts(
                input_count=1,
                candidate_count=1,
                selected_count=1,
                analyzed_count=1,
                validated_count=1,
                published_count=0,
                delivered_count=0,
            )
        )


def test_manifest_requires_all_stages_once_in_pipeline_order() -> None:
    stages = _stages()
    with pytest.raises(ValidationError, match="pipeline order"):
        _manifest(stages=tuple(reversed(stages)))
    with pytest.raises(ValidationError, match="pipeline order"):
        _manifest(stages=stages[:-1])


def test_stage_result_uses_controlled_counts_and_errors() -> None:
    stage = StageRunResult(
        name="documents",
        status="partial",
        input_count=2,
        output_count=1,
        cache_hit_count=1,
        retry_count=2,
        partial_failure_count=1,
        error_codes=("document_paper_failed",),
    )

    assert stage.partial_failure_count == 1
    with pytest.raises(ValidationError):
        StageRunResult(name="documents", status="failed", input_count=-1, output_count=0)


def test_stage_result_status_must_match_counts_and_controlled_errors() -> None:
    with pytest.raises(ValidationError, match="successful stage"):
        StageRunResult(
            name="documents",
            status="success",
            input_count=2,
            output_count=1,
            partial_failure_count=1,
            error_codes=("document_paper_failed",),
        )
    with pytest.raises(ValidationError, match="failed stage"):
        StageRunResult(
            name="documents",
            status="failed",
            input_count=2,
            output_count=1,
            error_codes=("document_stage_failed",),
        )
    with pytest.raises(ValidationError, match="skipped stage"):
        StageRunResult(name="documents", status="skipped", input_count=1)


def test_manifest_run_status_must_match_stage_outcomes() -> None:
    stages = list(_stages())
    stages[1] = StageRunResult(
        name="documents",
        status="failed",
        input_count=1,
        error_codes=("document_stage_failed",),
    )
    with pytest.raises(ValidationError, match="run status"):
        _manifest(status="success", stages=tuple(stages))

    stages = list(_stages())
    stages[-1] = StageRunResult(
        name="feishu",
        status="failed",
        input_count=1,
        error_codes=("feishu_delivery_failed",),
    )
    with pytest.raises(ValidationError, match="run status"):
        _manifest(status="success", stages=tuple(stages))


def test_cache_identity_covers_every_stage_implementation_identity() -> None:
    identity = CacheIdentity(
        config_hash=HASH,
        embedding_model="jinaai/model",
        embedding_version="sentence-transformers-5.3.0",
        parser_version="docling-2.113.0",
        mapper_version="stage2-v1",
        prompt_version="stage3-v1",
        analysis_schema_version="1.0",
        validator_version="stage4-v1",
        viewer_build_version="stage5-v1",
        viewer_template_version="stage5-v1",
        delivery_renderer_version="stage6-v1",
    )

    payload = identity.model_dump(mode="json")

    assert identity.pipeline_version == PIPELINE_VERSION
    assert {
        "config_hash",
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
    } <= payload.keys()
    with pytest.raises(ValidationError, match="Extra inputs"):
        CacheIdentity(**payload, llm_api_key="must-not-be-stored")
