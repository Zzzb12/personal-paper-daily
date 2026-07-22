from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackBundle,
    FeedbackCommand,
    canonical_feedback_bundle_digest,
)


NOW = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)


def _write_bundle(path: Path) -> None:
    command = FeedbackCommand(
        command_id=UUID("00000000-0000-4000-8000-000000000001"),
        paper_id="2401.01234",
        action="set_read",
        value=True,
        occurred_at=NOW,
        device_id="device-a",
        sequence=1,
    )
    bundle_id = UUID("00000000-0000-4000-8000-000000000002")
    bundle = FeedbackBundle(
        bundle_id=bundle_id,
        generated_at=NOW,
        commands=(command,),
        digest=canonical_feedback_bundle_digest((command,), bundle_id=bundle_id, generated_at=NOW),
    )
    path.write_text(bundle.model_dump_json(), encoding="utf-8")


def _run(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "zotero_arxiv_daily.viewer.feedback_cli", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_requires_exact_subcommand_and_named_explicit_paths(tmp_path: Path) -> None:
    result = _run("import", "--bundle", "x", "--store", "y", "--dry", cwd=tmp_path)
    missing = _run("import", "--bundle", "x", cwd=tmp_path)

    assert result.returncode == 2
    assert missing.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_cli_dry_run_has_zero_writes_and_allowlisted_count_output(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    store = tmp_path / "store.json"
    _write_bundle(bundle)

    result = _run("import", "--bundle", str(bundle), "--store", str(store), "--dry-run", cwd=tmp_path)

    assert result.returncode == 0
    assert not store.exists()
    assert set(result.stdout.strip().split()) <= {
        "status=ok",
        "dry_run=true",
        "applied_count=1",
        "changed_count=1",
        "duplicate_count=0",
        "stale_count=0",
        "conflict_count=0",
        "bundle_duplicate_count=0",
    }


def test_cli_errors_are_fixed_and_do_not_leak_paths_or_exception_text(tmp_path: Path) -> None:
    missing = tmp_path / "secret-name.json"
    store = tmp_path / "store.json"
    result = _run("import", "--bundle", str(missing), "--store", str(store), cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == "status=error\n"
    assert str(missing) not in result.stdout + result.stderr
    assert "FileNotFoundError" not in result.stdout + result.stderr


def test_cli_repeated_bundle_is_idempotent(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    store = tmp_path / "store.json"
    _write_bundle(bundle)

    first = _run("import", "--bundle", str(bundle), "--store", str(store), cwd=tmp_path)
    second = _run("import", "--bundle", str(bundle), "--store", str(store), cwd=tmp_path)

    assert first.returncode == second.returncode == 0
    assert "applied_count=1" in first.stdout
    assert "bundle_duplicate_count=1" in second.stdout
