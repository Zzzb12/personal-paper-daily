from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from collections.abc import Mapping
from datetime import datetime
from time import sleep
from typing import Any, TypeVar

import httpx
from pyzotero import zotero

from ..analysis.schemas import InterestPaper, StrictModel
from ..utils import glob_match
from .base import InterestIssue, InterestReadResult, ZoteroCollection, ZoteroGateway, ZoteroItem


T = TypeVar("T")


class RetryPolicy(StrictModel):
    max_attempts: int = 3
    backoff_seconds: float = 1
    max_retry_after_seconds: float = 60


class PyzoteroGateway:
    def __init__(
        self,
        client: Any,
        *,
        retry_policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = sleep,
        owned_http_client: httpx.Client | None = None,
    ) -> None:
        self._client = client
        self._retry = retry_policy or RetryPolicy()
        self._sleeper = sleeper
        self._owned_http_client = owned_http_client

    @classmethod
    def from_credentials(cls, library_id: str, api_key: str) -> "PyzoteroGateway":
        http_client = httpx.Client(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10)
        )
        try:
            client = zotero.Zotero(library_id, "user", api_key, client=http_client)
        except Exception:
            http_client.close()
            raise
        return cls(client, owned_http_client=http_client)

    @staticmethod
    def _transient(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            return status in {408, 425, 429} or status >= 500
        return False

    def _retry_delay(self, attempt: int, exc: Exception) -> float:
        fallback = self._retry.backoff_seconds * attempt
        if not isinstance(exc, httpx.HTTPStatusError):
            return fallback
        raw = exc.response.headers.get("Retry-After")
        try:
            retry_after = float(raw) if raw is not None else fallback
        except ValueError:
            retry_after = fallback
        return min(max(retry_after, 0), self._retry.max_retry_after_seconds)

    def _call(self, operation: Callable[[], T]) -> T:
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                return operation()
            except Exception as exc:
                if attempt == self._retry.max_attempts or not self._transient(exc):
                    raise
                self._sleeper(self._retry_delay(attempt, exc))
        raise RuntimeError("unreachable retry loop")

    def list_collections(self) -> tuple[ZoteroCollection, ...]:
        raw = self._call(lambda: self._client.everything(self._client.collections()))
        return tuple(
            ZoteroCollection(
                key=entry["key"],
                name=entry["data"]["name"],
                parent_key=entry["data"].get("parentCollection") or None,
            )
            for entry in raw
        )

    def list_items(self) -> tuple[ZoteroItem, ...]:
        raw = self._call(
            lambda: self._client.everything(
                self._client.items(itemType="conferencePaper || journalArticle || preprint")
            )
        )
        return tuple(
            ZoteroItem(
                key=entry["key"],
                title=entry["data"].get("title", ""),
                abstract=entry["data"].get("abstractNote", ""),
                collection_keys=tuple(entry["data"].get("collections", ())),
                added_at=datetime.fromisoformat(entry["data"]["dateAdded"].replace("Z", "+00:00")),
            )
            for entry in raw
        )

    def close(self) -> None:
        if self._owned_http_client is not None:
            self._owned_http_client.close()
            self._owned_http_client = None


class CollectionPathError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ZoteroInterestProvider:
    def __init__(
        self,
        gateway: ZoteroGateway,
        *,
        include_paths: tuple[str, ...],
        exclude_paths: tuple[str, ...],
    ) -> None:
        self._gateway = gateway
        self._include_paths = tuple(include_paths)
        self._exclude_paths = tuple(exclude_paths)

    @staticmethod
    def _resolve_path(key: str, collections: Mapping[str, ZoteroCollection]) -> str:
        names: list[str] = []
        visited: set[str] = set()
        current: str | None = key
        while current:
            if current in visited:
                raise CollectionPathError("collection_cycle")
            visited.add(current)
            collection = collections.get(current)
            if collection is None:
                raise CollectionPathError("missing_collection")
            names.append(collection.name)
            current = collection.parent_key
        return "/".join(reversed(names))

    def _matches(self, path: str, patterns: tuple[str, ...]) -> bool:
        return any(glob_match(path, pattern) for pattern in patterns)

    def read(self) -> InterestReadResult:
        collections = {collection.key: collection for collection in self._gateway.list_collections()}
        papers: list[InterestPaper] = []
        issues: list[InterestIssue] = []
        excluded_count = 0
        invalid_count = 0

        for item in self._gateway.list_items():
            if not item.key.strip() or not item.title.strip() or not item.abstract.strip():
                invalid_count += 1
                issues.append(InterestIssue(code="invalid_item", message="Zotero item is missing ranking metadata"))
                continue

            paths: list[str] = []
            for collection_key in item.collection_keys:
                try:
                    paths.append(self._resolve_path(collection_key, collections))
                except CollectionPathError as exc:
                    issues.append(InterestIssue(code=exc.code, message="Zotero collection ancestry is invalid"))

            unique_paths = tuple(sorted(set(paths)))
            if not unique_paths:
                if item.collection_keys:
                    invalid_count += 1
                else:
                    excluded_count += 1
                continue

            included = any(self._matches(path, self._include_paths) for path in unique_paths)
            excluded = any(self._matches(path, self._exclude_paths) for path in unique_paths)
            if not included or excluded:
                excluded_count += 1
                continue

            papers.append(
                InterestPaper(
                    paper_id=f"zotero:{item.key.strip()}",
                    title=item.title,
                    abstract=item.abstract,
                    collection_paths=unique_paths,
                    added_at=item.added_at,
                )
            )

        ordered = tuple(sorted(papers, key=lambda paper: paper.paper_id))
        canonical = {
            "include_paths": sorted(self._include_paths),
            "exclude_paths": sorted(self._exclude_paths),
            "papers": [paper.model_dump(mode="json") for paper in ordered],
        }
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return InterestReadResult(
            papers=ordered,
            corpus_fingerprint=hashlib.sha256(encoded).hexdigest(),
            eligible_count=len(ordered),
            excluded_count=excluded_count,
            invalid_count=invalid_count,
            issues=tuple(issues),
        )
