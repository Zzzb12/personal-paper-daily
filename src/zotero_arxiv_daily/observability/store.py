from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from zotero_arxiv_daily.observability.metrics import RunMetrics
from zotero_arxiv_daily.pipeline.artifacts import (
    _is_link_or_junction,
    atomic_write_bytes,
    resolve_within,
)


class MetricsStoreError(RuntimeError):
    """A fixed-message metrics persistence error."""


@dataclass(frozen=True)
class MetricsWriteResult:
    output: Path
    sha256: str


class MetricsWriter:
    def __init__(
        self,
        run_root: Path,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        replace: Callable[[Path, Path], Any] = os.replace,
        directory_sync: Callable[[Path], None] | None = None,
    ) -> None:
        if isinstance(max_bytes, bool) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        root = Path(run_root)
        windows_root = PureWindowsPath(str(run_root))
        if windows_root.drive.startswith("\\\\"):
            raise MetricsStoreError("metrics root is unsafe")
        if not root.is_dir():
            raise MetricsStoreError("metrics root must be an existing directory")
        try:
            output = resolve_within(root, Path("run-metrics.json"))
        except Exception as exc:
            message = "metrics root contains a symbolic link" if "link" in str(exc) else "metrics root is unsafe"
            raise MetricsStoreError(message) from None
        self._root = root.resolve()
        self._output = output
        self._max_bytes = max_bytes
        self._replace = replace
        self._directory_sync = directory_sync

    @property
    def output(self) -> Path:
        return self._output

    def write(self, metrics: RunMetrics) -> MetricsWriteResult:
        if self._output.exists():
            if _is_link_or_junction(self._output):
                raise MetricsStoreError("metrics destination is a symbolic link")
            if self._output.stat().st_size > self._max_bytes:
                raise MetricsStoreError("existing metrics exceeds the size limit")
            try:
                existing = RunMetrics.model_validate_json(
                    self._output.read_bytes()
                )
            except Exception:
                raise MetricsStoreError("existing metrics document is invalid") from None
            if (
                existing.run_id != metrics.run_id
                or existing.config_hash != metrics.config_hash
            ):
                raise MetricsStoreError("existing metrics identity differs")
        payload = metrics.to_canonical_json().encode("utf-8")
        if len(payload) > self._max_bytes:
            raise MetricsStoreError("metrics payload exceeds the size limit")
        try:
            if RunMetrics.model_validate_json(payload) != metrics:
                raise MetricsStoreError("metrics canonical validation failed")
            atomic_write_bytes(
                self._output,
                payload,
                replace=self._replace,
                directory_sync=self._directory_sync,
            )
        except MetricsStoreError:
            raise
        except Exception:
            raise MetricsStoreError("metrics write failed") from None
        return MetricsWriteResult(
            output=self._output,
            sha256=hashlib.sha256(payload).hexdigest(),
        )
