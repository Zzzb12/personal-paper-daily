from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zotero_arxiv_daily.pipeline.cache import WorkflowCache
from zotero_arxiv_daily.pipeline.daily_schemas import CacheIdentity


NOW = datetime(2026, 7, 22, tzinfo=UTC)


def _identity(**updates: str) -> CacheIdentity:
    values = {
        "config_hash": "a" * 64,
        "embedding_model": "embedding-model",
        "embedding_version": "embedding-v1",
        "parser_version": "parser-v1",
        "mapper_version": "mapper-v1",
        "prompt_version": "prompt-v1",
        "analysis_schema_version": "analysis-v1",
        "validator_version": "validator-v1",
        "viewer_build_version": "viewer-v1",
        "viewer_template_version": "template-v1",
        "delivery_renderer_version": "delivery-v1",
    }
    values.update(updates)
    return CacheIdentity(**values)


def test_cache_key_is_versioned_and_changes_for_every_identity_component(tmp_path: Path) -> None:
    cache = WorkflowCache(tmp_path)
    original = _identity()

    assert cache.key(original).startswith("personal-paper-daily-stage7-cache-v1-")
    for field in CacheIdentity.model_fields:
        if field in {"cache_version", "pipeline_version", "config_hash"}:
            replacement = "b" * 64 if field == "config_hash" else "changed-v2"
        else:
            replacement = f"changed-{field}"
        changed = original.model_copy(update={field: replacement})
        assert cache.key(changed) != cache.key(original), field


def test_cache_round_trip_and_corrupt_oversized_or_stale_entries_are_misses(
    tmp_path: Path,
) -> None:
    cache = WorkflowCache(tmp_path, max_bytes=1024, ttl=timedelta(hours=1))
    identity = _identity()
    cache.write(identity, {"artifact_hash": "c" * 64}, now=NOW)

    assert cache.read(identity, now=NOW) == {"artifact_hash": "c" * 64}
    assert cache.read(identity, now=NOW + timedelta(hours=2)) is None

    path = cache.path_for(identity)
    path.write_text("{not-json", encoding="utf-8")
    assert cache.read(identity, now=NOW) is None

    path.write_bytes(b"x" * 1025)
    assert cache.read(identity, now=NOW) is None


def test_cache_rejects_identity_and_payload_hash_mismatch(tmp_path: Path) -> None:
    cache = WorkflowCache(tmp_path)
    identity = _identity()
    cache.write(identity, {"artifact_hash": "c" * 64}, now=NOW)
    path = cache.path_for(identity)
    envelope = json.loads(path.read_text(encoding="utf-8"))

    envelope["identity"]["validator_version"] = "tampered"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert cache.read(identity, now=NOW) is None

    cache.write(identity, {"artifact_hash": "c" * 64}, now=NOW)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["payload"]["artifact_hash"] = "d" * 64
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert cache.read(identity, now=NOW) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "sensitive"},
        {"apiKey": "sensitive"},
        {"tenant_access_token": "sensitive"},
        {"metadata": {"value": "credential-sentinel"}},
        {"prompt": "private source"},
        {"zotero": {"items": []}},
        {"full_text": "paper body"},
        {"feedback": {"favorite": True}},
        {"analysis": {"status": "unvalidated"}},
    ],
)
def test_workflow_cache_rejects_private_or_unvalidated_payloads(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    cache = WorkflowCache(tmp_path)

    with pytest.raises(ValueError, match="workflow cache payload is not allowed"):
        cache.write(_identity(), payload, now=NOW)


def test_cache_write_uses_same_directory_atomic_replace_and_cleans_failure(
    tmp_path: Path,
) -> None:
    replacements: list[tuple[Path, Path]] = []

    def failing_replace(source: Path, destination: Path) -> None:
        replacements.append((Path(source), Path(destination)))
        raise OSError("injected replace failure")

    cache = WorkflowCache(tmp_path, replace=failing_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        cache.write(_identity(), {"artifact_hash": "c" * 64}, now=NOW)

    source, destination = replacements[0]
    assert source.parent == destination.parent == tmp_path.resolve()
    assert not source.exists()
    assert not tuple(tmp_path.glob("*.tmp"))
