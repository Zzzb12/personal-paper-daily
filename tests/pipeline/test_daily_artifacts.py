from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zotero_arxiv_daily.pipeline.artifacts import ArtifactAuditor, ManifestStore
from zotero_arxiv_daily.pipeline.daily_schemas import (
    FeishuRunResult,
    RunCounts,
    RunManifest,
    STAGE_ORDER,
    StageRunResult,
    StaticSiteResult,
)
from zotero_arxiv_daily.viewer.schemas import BuildManifest


NOW = datetime(2026, 7, 22, tzinfo=UTC)
HASH = "a" * 64


def _run_manifest() -> RunManifest:
    stages = tuple(
        StageRunResult(name=name, status="success", input_count=1, output_count=1)
        for name in STAGE_ORDER
    )
    return RunManifest(
        run_id="20260722T000000Z-local",
        trigger="local",
        config_hash=HASH,
        dry_run=True,
        send_requested=False,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        status="success",
        stages=stages,
        counts=RunCounts(
            input_count=1,
            candidate_count=1,
            selected_count=1,
            analyzed_count=1,
            validated_count=1,
            published_count=1,
        ),
        cache_hit_count=0,
        retry_count=0,
        partial_failure_count=0,
        static_site=StaticSiteResult(
            status="success",
            published_count=1,
            audited_file_count=3,
            audited_byte_count=10,
            artifact_hash=HASH,
        ),
        feishu=FeishuRunResult(status="preview"),
        artifact_hash=HASH,
    )


def _valid_viewer(root: Path) -> Path:
    viewer = root / "viewer"
    (viewer / "assets").mkdir(parents=True)
    (viewer / "index.html").write_text("<!doctype html><title>Safe</title>", encoding="utf-8")
    (viewer / "assets" / "site.css").write_text("body{}", encoding="utf-8")
    manifest = BuildManifest(
        build_version="stage5-v1",
        template_version="stage5-v1",
        published_count=0,
        partial_count=0,
        written_paths=("assets/site.css", "build-manifest.json", "index.html"),
    )
    (viewer / "build-manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return viewer


def test_artifact_audit_accepts_only_a_complete_viewer_and_hashes_contents(tmp_path: Path) -> None:
    viewer = _valid_viewer(tmp_path)

    first = ArtifactAuditor(tmp_path).audit(viewer)
    second = ArtifactAuditor(tmp_path).audit(viewer)

    assert first.artifact_hash == second.artifact_hash
    assert first.file_count == 3
    assert first.byte_count > 0
    assert first.build_manifest.published_count == 0


def test_artifact_audit_allows_only_the_exact_reviewed_feedback_script(tmp_path: Path) -> None:
    viewer = _valid_viewer(tmp_path)
    script = viewer / "assets" / "feedback.js"
    script.write_text("export {};", encoding="utf-8")
    manifest = BuildManifest.model_validate_json(
        (viewer / "build-manifest.json").read_text(encoding="utf-8")
    ).model_copy(
        update={
            "written_paths": (
                "assets/feedback.js",
                "assets/site.css",
                "build-manifest.json",
                "index.html",
            )
        }
    )
    (viewer / "build-manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )

    audit = ArtifactAuditor(tmp_path).audit(viewer)

    assert audit.file_count == 4


def _write_declared_viewer_file(viewer: Path, relative_path: str) -> None:
    target = viewer / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unsafe", encoding="utf-8")
    manifest = BuildManifest.model_validate_json(
        (viewer / "build-manifest.json").read_text(encoding="utf-8")
    ).model_copy(
        update={
            "written_paths": tuple(
                sorted(
                    {
                        *BuildManifest.model_validate_json(
                            (viewer / "build-manifest.json").read_text(encoding="utf-8")
                        ).written_paths,
                        relative_path,
                    }
                )
            )
        }
    )
    (viewer / "build-manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize(
    "relative_path",
    (
        "assets/feedback-store.json",
        "assets/feedback.bundle.json",
        "assets/feedback_export.json",
        "assets/Feedback-Export.json",
        "assets/nested/reader-state.snapshot.json",
        "assets/nested/FAVORITES-backup.json",
        "assets/browser/localStorage-snapshot.json",
        "assets/browser/STATE/cache.json",
    ),
)
def test_artifact_audit_rejects_declared_feedback_state_bundle_and_snapshot_variants(
    tmp_path: Path, relative_path: str
) -> None:
    viewer = _valid_viewer(tmp_path)
    _write_declared_viewer_file(viewer, relative_path)

    with pytest.raises(ValueError, match="artifact contains a forbidden path"):
        ArtifactAuditor(tmp_path).audit(viewer)


@pytest.mark.parametrize(
    "relative_path",
    (
        ".env",
        "assets/cache/site.css",
        "assets/PRIVATE/site.css",
        "assets/Zotero/site.css",
        "assets/archive/site.css",
        "assets/migration/backup.css",
        "assets/feedback-store/site.css",
        "assets/nested/BUNDLE/site.css",
        "assets/browser-state/site.css",
    ),
)
def test_artifact_audit_rejects_private_seed_basenames_and_path_segments(
    tmp_path: Path, relative_path: str
) -> None:
    viewer = _valid_viewer(tmp_path)
    _write_declared_viewer_file(viewer, relative_path)

    with pytest.raises(ValueError, match="artifact contains a forbidden path"):
        ArtifactAuditor(tmp_path).audit(viewer)


@pytest.mark.parametrize(
    "relative_path",
    (
        "assets/zotero-export.css",
        "assets/private-export.css",
        "assets/cache-copy.css",
        "assets/ZOTERO_EXPORT.svg",
        "assets/feedbackStore.css",
        "assets/feedbackstore.css",
        "assets/readerState.css",
        "assets/READERSTATE.css",
        "assets/localStorage.css",
        "assets/BROWSERSTATE.css",
        "assets/favorites.css",
    ),
)
def test_artifact_audit_rejects_private_tokens_and_camel_case_compounds(
    tmp_path: Path, relative_path: str
) -> None:
    viewer = _valid_viewer(tmp_path)
    _write_declared_viewer_file(viewer, relative_path)

    with pytest.raises(ValueError, match="artifact contains a forbidden path"):
        ArtifactAuditor(tmp_path).audit(viewer)


@pytest.mark.parametrize("relative_path", ("assets/restore.svg", "assets/stateless.css"))
def test_artifact_audit_allows_unrelated_words_containing_sensitive_substrings(
    tmp_path: Path, relative_path: str
) -> None:
    viewer = _valid_viewer(tmp_path)
    _write_declared_viewer_file(viewer, relative_path)

    audit = ArtifactAuditor(tmp_path).audit(viewer)

    assert relative_path in audit.build_manifest.written_paths


@pytest.mark.parametrize(
    "relative_path",
    (
        "assets/reader-bundle.json",
        "assets/Reader-Bundle.JSON",
        "assets/export.snapshot.json",
        "assets/nested/STORE.json",
    ),
)
def test_artifact_audit_rejects_every_declared_json_except_root_build_manifest(
    tmp_path: Path, relative_path: str
) -> None:
    viewer = _valid_viewer(tmp_path)
    _write_declared_viewer_file(viewer, relative_path)

    with pytest.raises(ValueError, match="artifact contains a forbidden path"):
        ArtifactAuditor(tmp_path).audit(viewer)


@pytest.mark.parametrize(
    "relative_path",
    (
        "assets/other.js",
        "assets/Feedback.js",
        "assets/feedback.JS",
        "assets/feedback.json",
        "assets/feedback-store.json",
        "assets/reader-state.json",
        "assets/feedback.js.bak",
    ),
)
def test_artifact_audit_rejects_feedback_state_and_script_path_variants(
    tmp_path: Path, relative_path: str
) -> None:
    viewer = _valid_viewer(tmp_path)
    target = viewer / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unsafe", encoding="utf-8")

    with pytest.raises(ValueError):
        ArtifactAuditor(tmp_path).audit(viewer)


def test_artifact_audit_rejects_a_symlinked_feedback_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = _valid_viewer(tmp_path)
    target = viewer / "assets" / "feedback.js"
    target.write_text("export {};", encoding="utf-8")
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.artifacts._is_link_or_junction",
        lambda path: Path(path) == target,
    )

    with pytest.raises(ValueError, match="symbolic link|junction"):
        ArtifactAuditor(tmp_path).audit(viewer)


@pytest.mark.parametrize(
    "relative_path",
    (
        ".env",
        "cache/value.json",
        "data/zotero/private.json",
        "viewer/feedback.json",
        "paper.pdf",
        "backup.zip",
        "state.sqlite",
        "script.js",
    ),
)
def test_artifact_audit_rejects_private_cache_archive_and_unapproved_files(
    tmp_path: Path, relative_path: str
) -> None:
    viewer = _valid_viewer(tmp_path)
    target = viewer / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unsafe", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact contains a forbidden path"):
        ArtifactAuditor(tmp_path).audit(viewer)


def test_artifact_audit_rejects_missing_manifest_member_and_unlisted_html(tmp_path: Path) -> None:
    viewer = _valid_viewer(tmp_path)
    (viewer / "assets" / "site.css").unlink()
    with pytest.raises(ValueError, match="manifest path is missing"):
        ArtifactAuditor(tmp_path).audit(viewer)

    viewer = _valid_viewer(tmp_path / "second")
    (viewer / "unreviewed.html").write_text("unsafe", encoding="utf-8")
    with pytest.raises(ValueError, match="not declared"):
        ArtifactAuditor(tmp_path / "second").audit(viewer)


def test_output_boundaries_reject_traversal_unc_foreign_drive_and_symlinks(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    with pytest.raises(ValueError, match="within the run root"):
        ManifestStore(run_root, run_root / ".." / "escape.json")
    with pytest.raises(ValueError, match="UNC"):
        ManifestStore(run_root, Path(r"\\server\share\manifest.json"))

    current_drive = Path(run_root.resolve()).drive.upper()
    foreign_drive = "Z:" if current_drive != "Z:" else "Y:"
    with pytest.raises(ValueError, match="Windows drive"):
        ManifestStore(run_root, Path(foreign_drive + r"\outside\manifest.json"))

    real = run_root / "real"
    real.mkdir()
    link = run_root / "link"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(real)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert created.returncode == 0, created.stderr
    else:
        link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link|junction"):
        ManifestStore(run_root, link / "manifest.json")

    linked_root = tmp_path / "linked-run-root"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked_root), str(run_root)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert created.returncode == 0, created.stderr
    else:
        linked_root.symlink_to(run_root, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link|junction"):
        ManifestStore(linked_root, linked_root / "manifest.json")


def test_manifest_write_is_atomic_fsynced_and_failure_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    output = run_root / "run-manifest.json"
    fsync_calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda descriptor: fsync_calls.append(descriptor))
    store = ManifestStore(run_root, output)

    written = store.write(_run_manifest())

    assert written == output.resolve()
    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == _run_manifest().run_id
    assert fsync_calls

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("injected replace failure")

    failing = ManifestStore(run_root, output, replace=fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        failing.write(_run_manifest())
    assert not tuple(run_root.glob("*.tmp"))
