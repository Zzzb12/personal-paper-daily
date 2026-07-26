from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from zotero_arxiv_daily.observability.metrics import (
    BudgetEvaluation,
    ModelUsageMetric,
    RunMetrics,
    StageMetric,
)
from zotero_arxiv_daily.observability.store import MetricsStoreError, MetricsWriter


HASH = "a" * 64
STAGES = ("candidates", "documents", "analysis", "validation", "viewer", "feishu")


def _metrics() -> RunMetrics:
    stages = tuple(StageMetric(name=name, duration_ns=1) for name in STAGES)
    return RunMetrics(
        run_id="20260726T010203Z-local",
        trigger="local",
        config_hash=HASH,
        stages=stages,
        duration_ns=6,
        byte_count=0,
        cache_hit_count=0,
        retry_count=0,
        partial_failure_count=0,
        model_usage=ModelUsageMetric(model_identity_hash=HASH),
        peak_traced_allocation_bytes=0,
        budget=BudgetEvaluation(
            passed=True,
            candidate_count=0,
            selected_for_llm_count=0,
            selected_for_analysis_count=0,
            successful_analysis_call_count=0,
            analysis_attempt_count=0,
            configured_output_token_count=0,
            offline_network_call_count=0,
            offline_paid_call_count=0,
            peak_traced_allocation_bytes=0,
        ),
    )


def test_writer_uses_exact_fixed_path_and_canonical_hash(tmp_path: Path) -> None:
    writer = MetricsWriter(tmp_path)
    result = writer.write(_metrics())
    payload = (tmp_path / "run-metrics.json").read_bytes()

    assert result.output == (tmp_path / "run-metrics.json").resolve()
    assert result.output.name == "run-metrics.json"
    assert result.sha256 == __import__("hashlib").sha256(payload).hexdigest()
    assert payload == _metrics().to_canonical_json().encode("utf-8")
    assert RunMetrics.model_validate_json(payload) == _metrics()


def test_writer_rejects_oversize_and_invalid_existing_destination(tmp_path: Path) -> None:
    with pytest.raises(MetricsStoreError, match="size"):
        MetricsWriter(tmp_path, max_bytes=10).write(_metrics())

    destination = tmp_path / "run-metrics.json"
    destination.write_text("{private corruption", encoding="utf-8")
    with pytest.raises(MetricsStoreError, match="existing"):
        MetricsWriter(tmp_path).write(_metrics())
    assert destination.read_text(encoding="utf-8") == "{private corruption"


def test_writer_rejects_existing_identity_mismatch(tmp_path: Path) -> None:
    destination = tmp_path / "run-metrics.json"
    payload = _metrics().model_copy(update={"run_id": "another-run"}).to_canonical_json()
    destination.write_text(payload, encoding="utf-8")

    with pytest.raises(MetricsStoreError, match="identity"):
        MetricsWriter(tmp_path).write(_metrics())


def test_writer_uses_atomic_replace_and_cleans_failed_temporary(
    tmp_path: Path,
) -> None:
    replace = Mock(side_effect=OSError("private failure"))
    writer = MetricsWriter(tmp_path, replace=replace)

    with pytest.raises(MetricsStoreError, match="write failed"):
        writer.write(_metrics())

    source, destination = replace.call_args.args
    assert Path(source).parent == tmp_path.resolve()
    assert Path(destination) == (tmp_path / "run-metrics.json").resolve()
    assert not Path(source).exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_writer_rejects_symlinked_root_or_destination(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(MetricsStoreError, match="link"):
        MetricsWriter(linked)

    destination_target = target / "elsewhere.json"
    destination_target.write_text(_metrics().to_canonical_json(), encoding="utf-8")
    destination = target / "run-metrics.json"
    destination.symlink_to(destination_target)
    with pytest.raises(MetricsStoreError, match="link"):
        MetricsWriter(target).write(_metrics())


@pytest.mark.parametrize("unsafe", [r"\\server\share", r"Z:\foreign"])
def test_writer_rejects_unc_and_foreign_windows_roots(
    unsafe: str, tmp_path: Path
) -> None:
    with pytest.raises(MetricsStoreError):
        MetricsWriter(Path(unsafe))


def test_writer_rejects_unknown_existing_version_without_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "run-metrics.json"
    payload = json.loads(_metrics().to_canonical_json())
    payload["schema_version"] = "99.0"
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MetricsStoreError, match="existing"):
        MetricsWriter(tmp_path).write(_metrics())
    assert json.loads(destination.read_text(encoding="utf-8"))["schema_version"] == "99.0"
