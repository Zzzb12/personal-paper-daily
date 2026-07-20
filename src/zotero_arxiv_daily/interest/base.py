from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import field_validator

from ..analysis.schemas import InterestPaper, StrictModel


class ZoteroCollection(StrictModel):
    key: str
    name: str
    parent_key: str | None = None

    @field_validator("key", "name")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("collection value must not be blank")
        return value


class ZoteroItem(StrictModel):
    key: str
    title: str
    abstract: str
    collection_keys: tuple[str, ...]
    added_at: datetime


class InterestIssue(StrictModel):
    code: str
    message: str


class InterestReadResult(StrictModel):
    papers: tuple[InterestPaper, ...]
    corpus_fingerprint: str
    eligible_count: int
    excluded_count: int
    invalid_count: int
    issues: tuple[InterestIssue, ...]


class ZoteroGateway(Protocol):
    def list_collections(self) -> tuple[ZoteroCollection, ...]:
        raise NotImplementedError

    def list_items(self) -> tuple[ZoteroItem, ...]:
        raise NotImplementedError

