from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


@dataclass(frozen=True, slots=True)
class PublishedAsset:
    relative_path: PurePosixPath
    sha256: str
    byte_size: int


class EvidenceImagePublisher:
    def __init__(
        self,
        output_root: Path,
        *,
        max_image_bytes: int = 10 * 1024 * 1024,
        dry_run: bool = False,
    ) -> None:
        if max_image_bytes < 1:
            raise ValueError("max_image_bytes must be positive")
        self._output_root = output_root
        self._max_image_bytes = max_image_bytes
        self._dry_run = dry_run

    def publish(self, source: Path, *, evidence_root: Path) -> PublishedAsset:
        if source.is_symlink():
            raise ValueError("evidence image must not be a symbolic link")
        root = evidence_root.resolve(strict=True)
        resolved = source.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError("evidence image must remain within the approved evidence root") from error
        if not resolved.is_file() or resolved.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise ValueError("evidence image has an unsupported file type")
        payload = resolved.read_bytes()
        if not payload or len(payload) > self._max_image_bytes:
            raise ValueError("evidence image exceeds the configured size limit")
        digest = hashlib.sha256(payload).hexdigest()
        suffix = resolved.suffix.lower()
        relative = PurePosixPath("assets", "evidence", f"{digest}{suffix}")
        if self._dry_run:
            return PublishedAsset(relative_path=relative, sha256=digest, byte_size=len(payload))
        target = self._output_root / Path(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return PublishedAsset(relative_path=relative, sha256=digest, byte_size=len(payload))
