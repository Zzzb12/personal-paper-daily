from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import field_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel
from zotero_arxiv_daily.pipeline.artifacts import atomic_write_bytes
from zotero_arxiv_daily.pipeline.daily_schemas import CACHE_VERSION, CacheIdentity


_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        ".env",
        "api_key",
        "credentials",
        "secret",
        "prompt",
        "full_text",
        "abstract",
        "zotero",
        "feedback",
        "analysis",
        "validation",
    }
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cache time must be timezone-aware")
    return value.astimezone(UTC)


def _validate_safe_payload(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_PAYLOAD_KEYS or normalized.startswith(".env"):
                raise ValueError("workflow cache payload is not allowed")
            _validate_safe_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_safe_payload(child)


class CacheEnvelope(StrictModel):
    cache_version: str
    identity: CacheIdentity
    created_at: datetime
    expires_at: datetime
    payload_hash: str
    payload: dict[str, Any]

    @field_validator("created_at", "expires_at")
    @classmethod
    def normalize_times(cls, value: datetime) -> datetime:
        return _aware(value)


class WorkflowCache:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = 1024 * 1024,
        ttl: timedelta = timedelta(days=7),
        replace: Callable[[Path, Path], Any] = os.replace,
    ) -> None:
        if max_bytes < 1 or ttl <= timedelta(0):
            raise ValueError("cache limits must be positive")
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._ttl = ttl
        self._replace = replace

    @staticmethod
    def key(identity: CacheIdentity) -> str:
        digest = _sha256(_canonical_json(identity.model_dump(mode="json")))
        return f"personal-paper-daily-{CACHE_VERSION}-{digest}"

    def path_for(self, identity: CacheIdentity) -> Path:
        return self._root / f"{self.key(identity)}.json"

    def read(self, identity: CacheIdentity, *, now: datetime) -> dict[str, Any] | None:
        path = self.path_for(identity)
        try:
            if path.stat().st_size > self._max_bytes:
                return None
            envelope = CacheEnvelope.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            return None
        current = _aware(now)
        if (
            envelope.cache_version != CACHE_VERSION
            or envelope.identity != identity
            or current < envelope.created_at
            or current >= envelope.expires_at
            or envelope.payload_hash != _sha256(_canonical_json(envelope.payload))
        ):
            return None
        try:
            _validate_safe_payload(envelope.payload)
        except ValueError:
            return None
        return envelope.payload

    def write(
        self,
        identity: CacheIdentity,
        payload: Mapping[str, Any],
        *,
        now: datetime,
    ) -> Path:
        safe_payload = dict(payload)
        _validate_safe_payload(safe_payload)
        created_at = _aware(now)
        envelope = CacheEnvelope(
            cache_version=CACHE_VERSION,
            identity=identity,
            created_at=created_at,
            expires_at=created_at + self._ttl,
            payload_hash=_sha256(_canonical_json(safe_payload)),
            payload=safe_payload,
        )
        encoded = (envelope.model_dump_json() + "\n").encode("utf-8")
        if len(encoded) > self._max_bytes:
            raise ValueError("workflow cache entry exceeds the byte limit")
        return atomic_write_bytes(
            self.path_for(identity), encoded, replace=self._replace
        )
