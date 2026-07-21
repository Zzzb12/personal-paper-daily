from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath


class AtomicOutputRoot:
    def __init__(self, root: Path, *, dry_run: bool = False) -> None:
        self._root = root
        self._dry_run = dry_run

    def write_text(self, relative: PurePosixPath, content: str) -> Path:
        target = self._target(relative)
        if self._dry_run:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=target.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target

    def remove_stale_files(
        self,
        directory: PurePosixPath,
        *,
        suffix: str,
        keep_names: set[str],
    ) -> tuple[PurePosixPath, ...]:
        """Remove only stale generated files from one controlled output directory."""
        target_directory = self._target(directory)
        if self._dry_run or not target_directory.exists():
            return ()
        if not target_directory.is_dir():
            raise ValueError("stale-file directory must be a directory")
        removed: list[PurePosixPath] = []
        for candidate in target_directory.iterdir():
            if candidate.is_symlink():
                raise ValueError("stale-file directory must not contain symbolic links")
            if candidate.is_file() and candidate.suffix == suffix and candidate.name not in keep_names:
                candidate.unlink()
                removed.append(directory / candidate.name)
        return tuple(removed)

    def _target(self, relative: PurePosixPath) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("output path must be relative and contained")
        if self._root.is_symlink():
            raise ValueError("output root must not be a symbolic link")
        target = self._root / Path(*relative.parts)
        current = self._root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("output path must not traverse a symbolic link")
        return target
