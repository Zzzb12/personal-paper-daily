from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import Field

from zotero_arxiv_daily.analysis.schemas import StrictModel
from zotero_arxiv_daily.pipeline.daily_schemas import RunManifest
from zotero_arxiv_daily.viewer.schemas import BuildManifest


_ALLOWED_ARTIFACT_SUFFIXES = frozenset(
    {".html", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".json", ".js"}
)
_ALLOWED_EVIDENCE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_FORBIDDEN_PARTS = frozenset(
    {
        ".env",
        "cache",
        "zotero",
        "feedback.json",
        "favorites.json",
        "reader-state.json",
        ".reader-state.json",
        "private",
    }
)
_FORBIDDEN_SUFFIXES = frozenset(
    {".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".db", ".sqlite", ".sqlite3"}
)
_REVIEWED_FEEDBACK_SCRIPT = "assets/feedback.js"
_FEEDBACK_STATE_SIGNALS = frozenset(
    {
        "archive",
        "backup",
        "browser",
        "bundle",
        "feedback",
        "favorite",
        "localstorage",
        "migration",
        "snapshot",
        "state",
        "store",
    }
)


class ArtifactAudit(StrictModel):
    artifact_hash: str
    file_count: int = Field(ge=1)
    byte_count: int = Field(ge=1)
    build_manifest: BuildManifest


def _windows_path_kind(path: Path) -> tuple[str, str]:
    windows = PureWindowsPath(str(path))
    drive = windows.drive
    if drive.startswith("\\\\"):
        return "unc", drive
    return "drive", drive.upper()


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def resolve_within(run_root: Path, candidate: Path) -> Path:
    supplied_root = Path(run_root)
    if _is_link_or_junction(supplied_root):
        raise ValueError("run root must not be a symbolic link or junction")
    root = supplied_root.resolve()
    path = Path(candidate)
    kind, drive = _windows_path_kind(path)
    if kind == "unc":
        raise ValueError("UNC output paths are not allowed")
    root_kind, root_drive = _windows_path_kind(root)
    if kind == "drive" and drive and root_kind == "drive" and drive != root_drive:
        raise ValueError("output path must remain on the run root Windows drive")

    lexical = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise ValueError("output path must remain within the run root") from exc

    current = root
    if _is_link_or_junction(current):
        raise ValueError("run root must not be a symbolic link or junction")
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link_or_junction(current):
            raise ValueError("output path must not traverse a symbolic link or junction")

    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("output path must remain within the run root") from exc
    return resolved


def atomic_write_bytes(
    destination: Path,
    payload: bytes,
    *,
    replace: Callable[[Path, Path], Any] = os.replace,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        replace(temporary_path, destination)
        return destination
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


class ManifestStore:
    def __init__(
        self,
        run_root: Path,
        output: Path,
        *,
        replace: Callable[[Path, Path], Any] = os.replace,
    ) -> None:
        self._output = resolve_within(Path(run_root), Path(output))
        self._run_root = Path(run_root).resolve()
        if self._output.suffix.lower() != ".json":
            raise ValueError("manifest output must use a JSON file")
        self._replace = replace

    @property
    def output(self) -> Path:
        return self._output

    def write(self, manifest: RunManifest) -> Path:
        payload = (manifest.model_dump_json(indent=2) + "\n").encode("utf-8")
        return atomic_write_bytes(self._output, payload, replace=self._replace)


class ArtifactAuditor:
    def __init__(
        self,
        run_root: Path,
        *,
        max_files: int = 500,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        if max_files < 1 or max_bytes < 1:
            raise ValueError("artifact limits must be positive")
        supplied_root = Path(run_root)
        if _is_link_or_junction(supplied_root):
            raise ValueError("run root must not be a symbolic link or junction")
        self._run_root = supplied_root.resolve()
        self._max_files = max_files
        self._max_bytes = max_bytes

    def audit(self, viewer_root: Path) -> ArtifactAudit:
        root = resolve_within(self._run_root, Path(viewer_root))
        if not root.is_dir():
            raise ValueError("viewer artifact root must be a directory")
        if _is_link_or_junction(root):
            raise ValueError("viewer artifact root must not be a symbolic link or junction")

        build_path = root / "build-manifest.json"
        try:
            build_manifest = BuildManifest.model_validate_json(
                build_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise ValueError("viewer build manifest is invalid") from exc

        declared = self._declared_paths(build_manifest)
        files: list[tuple[str, Path, int]] = []
        total_bytes = 0
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if _is_link_or_junction(path):
                raise ValueError("artifact contains a symbolic link or junction")
            if path.is_dir():
                continue
            relative = path.relative_to(root).as_posix()
            self._validate_artifact_path(relative, declared)
            size = path.stat().st_size
            total_bytes += size
            files.append((relative, path, size))
            if len(files) > self._max_files or total_bytes > self._max_bytes:
                raise ValueError("artifact exceeds configured limits")

        missing = tuple(relative for relative in declared if not (root / relative).is_file())
        if missing:
            raise ValueError("viewer manifest path is missing")
        if not files:
            raise ValueError("viewer artifact must not be empty")

        digest = hashlib.sha256()
        for relative, path, size in files:
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(size).encode("ascii"))
            digest.update(b"\0")
            with path.open("rb") as artifact_file:
                while chunk := artifact_file.read(1024 * 1024):
                    digest.update(chunk)
        return ArtifactAudit(
            artifact_hash=digest.hexdigest(),
            file_count=len(files),
            byte_count=total_bytes,
            build_manifest=build_manifest,
        )

    @staticmethod
    def _declared_paths(build_manifest: BuildManifest) -> frozenset[str]:
        declared: set[str] = set()
        for value in build_manifest.written_paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("viewer manifest contains an unsafe path")
            declared.add(path.as_posix())
        if "build-manifest.json" not in declared or "index.html" not in declared:
            raise ValueError("viewer manifest omits required files")
        return frozenset(declared)

    @staticmethod
    def _validate_artifact_path(relative: str, declared: frozenset[str]) -> None:
        path = PurePosixPath(relative)
        lowered_parts = tuple(part.lower() for part in path.parts)
        suffix = path.suffix.lower()
        if (
            (
                relative != _REVIEWED_FEEDBACK_SCRIPT
                and any(
                    signal in part
                    for part in lowered_parts
                    for signal in _FEEDBACK_STATE_SIGNALS
                )
            )
            or any(part in _FORBIDDEN_PARTS or part.startswith(".env.") for part in lowered_parts)
            or suffix in _FORBIDDEN_SUFFIXES
            or suffix not in _ALLOWED_ARTIFACT_SUFFIXES
            or (suffix == ".json" and relative != "build-manifest.json")
            or (suffix == ".js" and relative != _REVIEWED_FEEDBACK_SCRIPT)
        ):
            raise ValueError("artifact contains a forbidden path")
        is_evidence_image = (
            len(path.parts) >= 3
            and lowered_parts[:2] == ("assets", "evidence")
            and suffix in _ALLOWED_EVIDENCE_SUFFIXES
        )
        if relative not in declared and not is_evidence_image:
            raise ValueError("artifact path is not declared by the viewer manifest")
