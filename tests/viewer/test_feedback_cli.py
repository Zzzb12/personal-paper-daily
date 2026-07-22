from __future__ import annotations

from io import StringIO
from pathlib import Path
import subprocess
import sys
from datetime import UTC, datetime
from uuid import UUID

import pytest

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackBundle,
    FeedbackCommand,
    FeedbackImportResult,
    FeedbackStoreState,
    canonical_feedback_bundle_digest,
)
from zotero_arxiv_daily.viewer.feedback_cli import main


SUCCESS = (
    "status=ok\n"
    "dry_run=true\n"
    "applied_count=1\n"
    "changed_count=1\n"
    "duplicate_count=0\n"
    "stale_count=0\n"
    "conflict_count=0\n"
    "bundle_duplicate_count=0\n"
)


class _Store:
    def import_bundle(self, bundle: object, *, dry_run: bool) -> FeedbackImportResult:
        assert dry_run is True
        return FeedbackImportResult(
            state=FeedbackStoreState(),
            applied_count=1,
            changed_count=1,
            duplicate_count=0,
            stale_count=0,
            conflict_count=0,
            bundle_duplicate_count=0,
        )


def _write_bundle(path: Path) -> None:
    command = FeedbackCommand(
        command_id=UUID("00000000-0000-4000-8000-000000000001"),
        paper_id="2401.01234",
        action="set_read",
        value=True,
        occurred_at=datetime(2026, 7, 22, 4, 0, tzinfo=UTC),
        device_id="device-a",
        sequence=1,
    )
    bundle_id = UUID("00000000-0000-4000-8000-000000000002")
    bundle = FeedbackBundle(
        bundle_id=bundle_id,
        generated_at=datetime(2026, 7, 22, 4, 0, tzinfo=UTC),
        commands=(command,),
        digest=canonical_feedback_bundle_digest(
            (command,), bundle_id=bundle_id, generated_at=datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
        ),
    )
    path.write_text(bundle.model_dump_json(), encoding="utf-8")


def _run(
    arguments: list[str],
    *,
    private_root: Path,
    bundle_loader: object = lambda path, *, root: object(),
    store_factory: object = lambda path, *, root: _Store(),
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(
        arguments,
        private_root=private_root,
        bundle_loader=bundle_loader,  # type: ignore[arg-type]
        store_factory=store_factory,  # type: ignore[arg-type]
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_cli_keeps_default_store_target_inside_injected_private_root_without_fs_writes(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "data" / "private-feedback"
    private_root.mkdir(parents=True)
    captured: dict[str, Path] = {}

    def loader(path: Path, *, root: Path) -> object:
        captured["bundle_root"] = root
        return object()

    def factory(path: Path, *, root: Path) -> _Store:
        captured["store"] = path
        captured["store_root"] = root
        return _Store()

    code, stdout, stderr = _run(
        ["import", "--bundle", "feedback-v1-export.json", "--store", "daily/store.json", "--dry-run"],
        private_root=private_root,
        bundle_loader=loader,
        store_factory=factory,
    )

    assert code == 0
    assert stdout == SUCCESS
    assert stderr == ""
    assert captured == {
        "bundle_root": private_root.resolve(),
        "store": (private_root / "daily" / "store.json").resolve(),
        "store_root": private_root.resolve(),
    }
    assert not list(private_root.rglob("*"))


def test_module_cli_uses_the_ignored_default_private_root_and_exact_output(tmp_path: Path) -> None:
    private_root = tmp_path / "data" / "private-feedback"
    private_root.mkdir(parents=True)
    _write_bundle(private_root / "feedback-v1-export.json")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "zotero_arxiv_daily.viewer.feedback_cli",
            "import",
            "--bundle",
            "feedback-v1-export.json",
            "--store",
            "store.json",
            "--dry-run",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == SUCCESS
    assert result.stderr == ""
    assert not (private_root / "store.json").exists()


@pytest.mark.parametrize(
    "store",
    ["../escape.json", r"\\server\share\store.json", r"Z:\foreign\store.json"],
)
def test_cli_rejects_store_escape_before_loader_or_factory(tmp_path: Path, store: str) -> None:
    private_root = tmp_path / "data" / "private-feedback"
    private_root.mkdir(parents=True)
    called = False

    def forbidden(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("unsafe input reached a boundary")

    code, stdout, stderr = _run(
        ["import", "--bundle", "feedback-v1-export.json", "--store", store, "--dry-run"],
        private_root=private_root,
        bundle_loader=forbidden,
        store_factory=forbidden,
    )

    assert code == 1
    assert stdout == "status=error\n"
    assert stderr == ""
    assert called is False
    assert not list(private_root.rglob("*"))


@pytest.mark.parametrize(
    "arguments",
    [
        ["import", "--bund", "secret-value", "--store", "store.json"],
        ["import", "--bundle", "secret-value"],
        ["unknown-secret-subcommand"],
    ],
)
def test_cli_parser_errors_are_fixed_and_never_echo_dynamic_arguments(
    tmp_path: Path, arguments: list[str]
) -> None:
    private_root = tmp_path / "data" / "private-feedback"
    private_root.mkdir(parents=True)
    code, stdout, stderr = _run(arguments, private_root=private_root)

    assert code == 1
    assert stdout == "status=error\n"
    assert stderr == ""
    assert "secret-value" not in stdout + stderr
    assert "unknown-secret-subcommand" not in stdout + stderr


def test_cli_maps_boundary_failure_to_the_fixed_output(tmp_path: Path) -> None:
    private_root = tmp_path / "data" / "private-feedback"
    private_root.mkdir(parents=True)

    def rejected(path: Path, *, root: Path) -> object:
        raise ValueError("private path must not leak")

    code, stdout, stderr = _run(
        ["import", "--bundle", "secret-value", "--store", "store.json", "--dry-run"],
        private_root=private_root,
        bundle_loader=rejected,
    )

    assert code == 1
    assert stdout == "status=error\n"
    assert stderr == ""
