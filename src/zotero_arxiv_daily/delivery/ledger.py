from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import field_validator, model_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel
from zotero_arxiv_daily.pipeline.artifacts import atomic_write_bytes


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DeliveryLedgerState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    idempotency_keys: tuple[str, ...] = ()

    @field_validator("idempotency_keys")
    @classmethod
    def validate_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _SHA256_RE.fullmatch(item) for item in value):
            raise ValueError("delivery ledger contains an invalid idempotency key")
        return value

    @model_validator(mode="after")
    def validate_unique_keys(self) -> Self:
        if len(self.idempotency_keys) != len(set(self.idempotency_keys)):
            raise ValueError("delivery ledger keys must be unique")
        return self


class DeliveryLedger:
    """Small non-sensitive, atomic ledger used to suppress cross-run sends."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 1024 * 1024,
        replace: Callable[[Path, Path], Any] = os.replace,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("delivery ledger byte limit must be positive")
        self._path = Path(path).resolve()
        self._max_bytes = max_bytes
        self._replace = replace

    def contains(self, idempotency_key: str) -> bool:
        self._validate_key(idempotency_key)
        return idempotency_key in self._load().idempotency_keys

    def record(self, idempotency_key: str) -> None:
        self._validate_key(idempotency_key)
        state = self._load()
        if idempotency_key in state.idempotency_keys:
            return
        updated = DeliveryLedgerState(
            idempotency_keys=(*state.idempotency_keys, idempotency_key)
        )
        payload = (updated.model_dump_json(indent=2) + "\n").encode("utf-8")
        if len(payload) > self._max_bytes:
            raise ValueError("delivery ledger exceeds its byte limit")
        atomic_write_bytes(self._path, payload, replace=self._replace)

    def _load(self) -> DeliveryLedgerState:
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return DeliveryLedgerState()
        if size > self._max_bytes:
            raise ValueError("delivery ledger is invalid")
        try:
            return DeliveryLedgerState.model_validate_json(self._path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError("delivery ledger is invalid") from exc

    @staticmethod
    def _validate_key(value: str) -> None:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("delivery idempotency key must be a lowercase SHA-256 digest")
