from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

import zotero_arxiv_daily.viewer.feedback as feedback_module

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackBundle,
    FeedbackCommand,
    FeedbackStore,
    FeedbackStoreFileOps,
    FeedbackStoreSafetyError,
    canonical_feedback_bundle_digest,
)


NOW = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)


def _bundle(*, bundle_number: int = 2, command_number: int = 1) -> FeedbackBundle:
    command = FeedbackCommand(
        command_id=UUID(f"00000000-0000-4000-8000-{command_number:012d}"),
        paper_id="2401.01234",
        action="set_read",
        value=True,
        occurred_at=NOW,
        device_id="device-a",
        sequence=command_number,
    )
    bundle_id = UUID(f"00000000-0000-4000-8000-{bundle_number:012d}")
    return FeedbackBundle(
        bundle_id=bundle_id,
        generated_at=NOW,
        commands=(command,),
        digest=canonical_feedback_bundle_digest((command,), bundle_id=bundle_id, generated_at=NOW),
    )


def _store(tmp_path: Path, **kwargs: object) -> FeedbackStore:
    root = tmp_path / "private-root"
    root.mkdir(exist_ok=True)
    return FeedbackStore(root / "feedback" / "store.json", root=root, **kwargs)


def test_read_missing_store_is_empty_and_import_is_atomic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.read().records == ()

    result = store.import_bundle(_bundle(), dry_run=False)

    assert result.applied_count == 1
    assert result.state.records[0].read is True
    assert store.path.exists()
    assert not list(store.path.parent.glob("*.tmp"))


@pytest.mark.parametrize("contents", ["{", "[]", '{"schema_version":"9.0"}'])
def test_read_corrupt_or_unknown_store_fails_closed_without_leaking_details(
    tmp_path: Path, contents: str
) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text(contents, encoding="utf-8")

    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        store.read()


def test_read_rejects_oversize_or_digest_tampering(tmp_path: Path) -> None:
    oversized = _store(tmp_path, max_bytes=8)
    oversized.path.parent.mkdir()
    oversized.path.write_text("{" + '"x"' * 20 + "}", encoding="utf-8")
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        oversized.read()

    oversized.path.unlink()
    store = _store(tmp_path)
    store.import_bundle(_bundle(), dry_run=False)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["digest"] = "0" * 64
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        store.read()


def test_read_migrates_the_only_supported_v0_fixture_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text(
        json.dumps(
            {
                "schema_version": "0.0",
                "records": [],
                "applied_command_ids": [],
                "applied_bundle_ids": [],
            }
        ),
        encoding="utf-8",
    )

    assert store.read().schema_version == "1.0"
    assert json.loads(store.path.read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_v0_store_dry_run_validates_without_migrating_or_writing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text(
        json.dumps(
            {
                "schema_version": "0.0",
                "records": [],
                "applied_command_ids": [],
                "applied_bundle_ids": [],
            }
        ),
        encoding="utf-8",
    )
    before = store.path.read_bytes()

    result = store.import_bundle(_bundle(), dry_run=True)

    assert result.applied_count == 1
    assert store.path.read_bytes() == before


def test_v0_import_performs_only_the_final_atomic_replacement(tmp_path: Path) -> None:
    replacements: list[tuple[str, str]] = []
    store = _store(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text(
        json.dumps(
            {
                "schema_version": "0.0",
                "records": [],
                "applied_command_ids": [],
                "applied_bundle_ids": [],
            }
        ),
        encoding="utf-8",
    )
    original = FeedbackStoreFileOps.default()

    def replace_file(source: str, target: str) -> None:
        replacements.append((source, target))
        original.replace(source, target)

    store = FeedbackStore(
        store.path, root=store.root, file_ops=replace(original, replace=replace_file)
    )

    store.import_bundle(_bundle(), dry_run=False)

    assert len(replacements) == 1


def test_duplicate_bundle_is_idempotent_and_dry_run_never_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.import_bundle(_bundle(), dry_run=False)
    before = store.path.read_bytes()
    duplicate = store.import_bundle(_bundle(), dry_run=False)
    dry_run = store.import_bundle(_bundle(bundle_number=3, command_number=3), dry_run=True)

    assert first.applied_count == 1
    assert duplicate.bundle_duplicate_count == 1
    assert duplicate.applied_count == 0
    assert dry_run.applied_count == 1
    assert store.path.read_bytes() == before


def test_stale_or_invalid_bundle_never_partially_replaces_existing_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.import_bundle(_bundle(), dry_run=False)
    before = store.path.read_bytes()
    invalid = _bundle(bundle_number=4, command_number=4).model_copy(update={"digest": "0" * 64})

    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        store.import_bundle(invalid, dry_run=False)

    assert store.path.read_bytes() == before


def test_stale_bundle_reports_stale_count_and_preserves_newer_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.import_bundle(_bundle(), dry_run=False)
    stale_command = FeedbackCommand(
        command_id=UUID("00000000-0000-4000-8000-000000000099"),
        paper_id="2401.01234",
        action="set_read",
        value=False,
        occurred_at=NOW - timedelta(seconds=1),
        device_id="device-a",
        sequence=99,
    )
    stale_bundle_id = UUID("00000000-0000-4000-8000-000000000100")
    stale_bundle = FeedbackBundle(
        bundle_id=stale_bundle_id,
        generated_at=NOW,
        commands=(stale_command,),
        digest=canonical_feedback_bundle_digest(
            (stale_command,), bundle_id=stale_bundle_id, generated_at=NOW
        ),
    )

    result = store.import_bundle(stale_bundle, dry_run=False)

    assert result.stale_count == 1
    assert result.state.records[0].read is True


def test_atomic_writer_uses_target_directory_flushes_fsyncs_and_cleans_failed_replace(
    tmp_path: Path,
) -> None:
    events: list[object] = []
    store = _store(tmp_path)
    original = FeedbackStoreFileOps.default()

    def temp(directory: str, prefix: str, suffix: str) -> tuple[int, str]:
        events.append(("temp", Path(directory), prefix, suffix))
        return original.make_temp(directory, prefix, suffix)

    def sync(fd: int) -> None:
        events.append("fsync")
        original.fsync(fd)

    def fail_replace(source: str, target: str) -> None:
        events.append(("replace", Path(source), Path(target)))
        raise OSError("replace source path must remain private")

    store = FeedbackStore(
        store.path,
        root=store.root,
        file_ops=replace(original, make_temp=temp, fsync=sync, replace=fail_replace),
    )

    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        store.import_bundle(_bundle(), dry_run=False)

    assert events[0][0] == "temp"
    assert events[0][1] == store.path.parent
    assert "fsync" in events
    assert events[-1][0] == "replace"
    assert not list(store.path.parent.glob("*.tmp"))


def test_atomic_writer_syncs_the_parent_directory_after_replace(tmp_path: Path) -> None:
    events: list[object] = []
    store = _store(tmp_path)
    original = FeedbackStoreFileOps.default()

    def replace_file(source: str, target: str) -> None:
        events.append("replace")
        original.replace(source, target)

    def sync_directory(directory: str) -> None:
        events.append(("directory-fsync", Path(directory)))

    store = FeedbackStore(
        store.path,
        root=store.root,
        file_ops=replace(original, replace=replace_file, sync_directory=sync_directory),
    )

    store.import_bundle(_bundle(), dry_run=False)

    assert events == ["replace", ("directory-fsync", store.path.parent)]


def test_atomic_writer_uses_injected_parent_creation_and_observes_flush_before_fsync(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    store = _store(tmp_path)
    original = FeedbackStoreFileOps.default()

    class Handle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> "Handle":
            return self

        def __exit__(self, *arguments: object) -> None:
            self.handle.close()  # type: ignore[attr-defined]

        def write(self, value: bytes) -> int:
            return self.handle.write(value)  # type: ignore[attr-defined]

        def flush(self) -> None:
            events.append("flush")
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

    def ensure_parent(directory: str) -> None:
        events.append("mkdir")
        Path(directory).mkdir(parents=True, exist_ok=True)

    def open_fd(descriptor: int, mode: str) -> Handle:
        return Handle(original.open_fd(descriptor, mode))

    def sync(descriptor: int) -> None:
        events.append("fsync")
        original.fsync(descriptor)

    store = FeedbackStore(
        store.path,
        root=store.root,
        file_ops=replace(
            original, ensure_parent=ensure_parent, open_fd=open_fd, fsync=sync
        ),
    )

    store.import_bundle(_bundle(), dry_run=False)

    assert events.index("mkdir") < events.index("flush") < events.index("fsync")


def test_store_rejects_symlink_parent_and_paths_outside_explicit_root(tmp_path: Path) -> None:
    store = _store(tmp_path)
    link = store.root / "linked"
    original = FeedbackStoreFileOps.default()

    def lstat(path: str) -> os.stat_result:
        if Path(path) == link:
            return os.stat_result((stat.S_IFLNK, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        return original.lstat(path)

    linked = FeedbackStore(
        link / "store.json",
        root=store.root,
        file_ops=replace(original, lstat=lstat),
    )

    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        linked.read()
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        FeedbackStore(store.root / ".." / "escape.json", root=store.root).read()
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        FeedbackStore(Path("..") / "escape.json", root=store.root).read()


def test_store_rejects_root_ancestor_reparse_point_and_final_nonregular_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = FeedbackStoreFileOps.default()
    ancestor = store.root.parent

    class ReparseDirectory:
        st_mode = stat.S_IFDIR
        st_file_attributes = 0x0400

    def lstat(path: str) -> object:
        if Path(path) == ancestor:
            return ReparseDirectory()
        return original.lstat(path)

    reparse_store = FeedbackStore(
        store.path, root=store.root, file_ops=replace(original, lstat=lstat)
    )
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        reparse_store.read()

    store.path.parent.mkdir()
    store.path.write_text("not a regular store", encoding="utf-8")
    nonregular = FeedbackStore(
        store.path,
        root=store.root,
        file_ops=replace(
            original,
            lstat=lambda path: os.stat_result((stat.S_IFIFO, 0, 0, 0, 0, 0, 0, 0, 0, 0))
            if Path(path) == store.path
            else original.lstat(path),
        ),
    )
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        nonregular.read()


def test_store_rejects_a_final_symlink_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = FeedbackStoreFileOps.default()
    final_link = os.stat_result((stat.S_IFLNK, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    linked = FeedbackStore(
        store.path,
        root=store.root,
        file_ops=replace(
            original,
            lstat=lambda path: final_link if Path(path) == store.path else original.lstat(path),
        ),
    )

    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        linked.read()


def test_store_rechecks_final_path_after_replace_and_rejects_a_swapped_reparse_point(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = FeedbackStoreFileOps.default()
    swapped = False

    class ReparseFile:
        st_mode = stat.S_IFREG
        st_file_attributes = 0x0400

    def replace_file(source: str, target: str) -> None:
        nonlocal swapped
        original.replace(source, target)
        swapped = True

    def lstat(path: str) -> object:
        if swapped and Path(path) == store.path:
            return ReparseFile()
        return original.lstat(path)

    guarded = FeedbackStore(
        store.path,
        root=store.root,
        file_ops=replace(original, replace=replace_file, lstat=lstat),
    )
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        guarded.import_bundle(_bundle(), dry_run=False)


def test_default_directory_sync_propagates_io_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_module.os, "open", lambda path, flags: 11)
    monkeypatch.setattr(
        feedback_module.os,
        "fsync",
        lambda descriptor: (_ for _ in ()).throw(OSError(5, "io failure")),
    )
    monkeypatch.setattr(feedback_module.os, "close", lambda descriptor: None)

    with pytest.raises(OSError):
        feedback_module._sync_feedback_directory("private-directory")


def test_store_translates_malformed_nul_path_to_the_fixed_safety_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        FeedbackStore("\0store.json", root=store.root).read()


@pytest.mark.parametrize("unsafe", [r"\\server\share\store.json", r"Z:\foreign\store.json"])
def test_store_rejects_unc_and_foreign_windows_drive_paths(tmp_path: Path, unsafe: str) -> None:
    store = _store(tmp_path)
    with pytest.raises(FeedbackStoreSafetyError, match=r"^feedback store rejected$"):
        FeedbackStore(Path(unsafe), root=store.root).read()


def test_private_feedback_patterns_are_gitignored() -> None:
    result = subprocess.run(
        [
            "git",
            "check-ignore",
            "--no-index",
            "data/private-feedback/store.json",
            "feedback-v1-export.json",
            "store.feedback-migration-001.bak",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "data/private-feedback/store.json",
        "feedback-v1-export.json",
        "store.feedback-migration-001.bak",
    ]
