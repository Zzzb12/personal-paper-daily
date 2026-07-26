from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
DAILY = ROOT / ".github" / "workflows" / "personal-paper-daily.yml"


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_stage9_ci_gate_is_offline_bounded_and_has_an_explicit_timeout() -> None:
    workflow = _load(CI)
    steps = workflow["jobs"]["pytest"]["steps"]
    stage9 = next(step for step in steps if step["name"] == "Validate Stage 9 offline budgets")

    assert int(stage9["timeout-minutes"]) <= 10
    assert stage9["run"] == (
        "uv run pytest tests/benchmarks tests/observability "
        "tests/workflows/test_stage9_ci.py -q"
    )
    raw = stage9["run"].casefold()
    assert "zotero" not in raw
    assert "llm" not in raw
    assert "feishu" not in raw


def test_metrics_are_private_run_records_and_pages_remains_viewer_only() -> None:
    workflow = _load(DAILY)
    steps = workflow["jobs"]["daily"]["steps"]
    run_upload = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    pages_upload = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/upload-pages-artifact@")
    )
    run_paths = tuple(
        line.strip()
        for line in run_upload["with"]["path"].splitlines()
        if line.strip()
    )

    assert run_paths == (
        "outputs/daily/run-manifest.json",
        "outputs/daily/run-metrics.json",
    )
    assert pages_upload["with"]["path"] == "outputs/daily/viewer"
    assert all(not path.endswith("/") for path in run_paths)
    assert all("viewer" not in path for path in run_paths)
    raw = DAILY.read_text(encoding="utf-8")
    assert "hashlib.sha256(metrics_payload).hexdigest()" in raw
    assert "manifest.metrics.sidecar_hash" in raw
    assert "cache/workflow/run-metrics" not in raw
