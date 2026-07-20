from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from ..analysis.schemas import InterestPaper
from ..utils import glob_match
from .base import InterestIssue, InterestReadResult, ZoteroCollection, ZoteroGateway


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
