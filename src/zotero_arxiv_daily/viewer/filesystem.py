from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath


class AtomicOutputRoot:
    def __init__(self, root: Path, *, dry_run: bool = False) -> None:
        self._root = root
        self._dry_run = dry_run

    def write_text(self, relative: PurePosixPath, content: str) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("output path must be relative and contained")
        target = self._root / Path(*relative.parts)
        current = self._root
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ValueError("output path must not traverse a symbolic link")
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
