from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
DAILY = WORKFLOWS / "personal-paper-daily.yml"
CI = WORKFLOWS / "ci.yml"
FULL_SHA_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


def _load(path: Path) -> dict[str, object]:
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_daily_workflow_has_schedule_dispatch_and_one_shared_versioned_cli() -> None:
    workflow = _load(DAILY)
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers

    raw = DAILY.read_text(encoding="utf-8")
    command = "python -m zotero_arxiv_daily.pipeline.daily"
    assert raw.count(command) == 1
    assert "--trigger" in raw
    assert "--mode" in raw
    assert "--run-root outputs/daily" in raw
    assert "--viewer-output viewer" in raw
    assert "--manifest-output run-manifest.json" in raw


def test_workflows_use_least_permissions_concurrency_and_timeouts() -> None:
    for path in (DAILY, CI):
        workflow = _load(path)
        assert workflow["permissions"] == {"contents": "read"}
        concurrency = workflow["concurrency"]
        assert isinstance(concurrency, dict)
        assert "github.ref" in str(concurrency["group"])
        assert concurrency["cancel-in-progress"] == "true"
        jobs = workflow["jobs"]
        assert isinstance(jobs, dict)
        for job in jobs.values():
            assert isinstance(job, dict)
            assert int(job["timeout-minutes"]) > 0
            for step in job.get("steps", []):
                if "run" in step:
                    assert int(step["timeout-minutes"]) > 0

    daily_jobs = _load(DAILY)["jobs"]
    assert daily_jobs["daily"]["permissions"] == {"contents": "read"}
    assert daily_jobs["deploy"]["permissions"] == {
        "pages": "write",
        "id-token": "write",
    }


def test_every_external_action_is_pinned_and_checkout_cannot_persist_credentials() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        workflow = _load(path)
        for job in workflow.get("jobs", {}).values():
            for step in job.get("steps", []):
                action = step.get("uses")
                if action is None:
                    continue
                assert FULL_SHA_ACTION.fullmatch(action), f"floating action in {path}: {action}"
                if action.startswith("actions/checkout@"):
                    assert step.get("with", {}).get("persist-credentials") == "false"


def test_workflow_cache_and_uploaded_artifacts_use_explicit_safe_allowlists() -> None:
    workflow = _load(DAILY)
    steps = workflow["jobs"]["daily"]["steps"]
    cache_step = next(step for step in steps if str(step.get("uses", "")).startswith("actions/cache@"))
    cache_paths = tuple(line.strip() for line in cache_step["with"]["path"].splitlines() if line.strip())
    assert cache_paths == ("cache/embeddings", "cache/documents", "cache/workflow")
    assert "stage7-v1" in cache_step["with"]["key"]
    assert "hashFiles" in cache_step["with"]["key"]
    assert "LLM_MODEL" in cache_step["with"]["key"]

    pages = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-pages-artifact@")
    )
    manifest = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert pages["with"]["path"] == "outputs/daily/viewer"
    assert manifest["with"]["path"] == "outputs/daily/run-manifest.json"
    assert "Verify reviewed viewer artifact" in tuple(step["name"] for step in steps)

    raw = DAILY.read_text(encoding="utf-8").lower()
    for forbidden in (
        "cache/analysis",
        "cache/validation",
        "data/zotero",
        "viewer/feedback",
        ".env\n",
    ):
        assert forbidden not in raw


def test_live_send_and_pages_require_exact_explicit_acknowledgements() -> None:
    raw = DAILY.read_text(encoding="utf-8")
    assert "I_UNDERSTAND_LIVE_NETWORK" in raw
    assert "I_UNDERSTAND_FEISHU_SEND" in raw
    assert "I_UNDERSTAND_PUBLIC_ARTIFACT" in raw
    assert "--send-feishu" in raw
    assert "PAPER_DAILY_SCHEDULE_LIVE" in raw
    assert "PAPER_DAILY_SCHEDULE_SEND" in raw
    assert "PAPER_DAILY_ENABLE_PAGES_DEPLOY" in raw
    deploy = _load(DAILY)["jobs"]["deploy"]
    assert "I_UNDERSTAND_PUBLIC_ARTIFACT" in deploy["if"]


def test_workflows_never_echo_environment_write_git_or_change_visibility() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml"))
    lowered = combined.lower()
    for forbidden in (
        "set -x",
        "printenv",
        "cat config/",
        "get-childitem env:",
        "git push",
        "git commit",
        "contents: write",
        "gh repo edit",
        "--visibility",
        "repository: ${{",
    ):
        assert forbidden not in lowered


def test_ci_preserves_full_upstream_coverage_and_adds_stage7_static_tests() -> None:
    raw = CI.read_text(encoding="utf-8")
    assert "uv sync --frozen" in raw
    assert 'pytest -m "" --cov=src/zotero_arxiv_daily --cov-report=term-missing' in raw
    assert "pytest tests/workflows tests/pipeline/test_daily" in raw


def test_unsafe_legacy_scheduled_or_write_workflows_are_removed() -> None:
    assert not (WORKFLOWS / "main.yml").exists()
    assert not (WORKFLOWS / "test.yml").exists()
    assert not (WORKFLOWS / "keep-alive.yml").exists()
