from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1.0"
_ARXIV_ID_RE = re.compile(r"^(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})$", re.IGNORECASE)
_CATEGORY_RE = re.compile(r"^[a-z-]+(?:\.[A-Z]{2})?$", re.IGNORECASE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def validate_run_id_value(value: str) -> str:
    normalized = _non_empty(value)
    stem = normalized.split(".", 1)[0].upper()
    if (
        not _SAFE_RUN_ID_RE.fullmatch(normalized)
        or normalized.endswith((".", " "))
        or stem in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError("run_id must be a cross-platform safe file name")
    return normalized


def _non_empty(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


class InterestPaper(StrictModel):
    paper_id: str
    title: str
    abstract: str
    collection_paths: tuple[str, ...]
    added_at: datetime
    feedback_weight: float = 1.0

    @field_validator("paper_id", "title", "abstract")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("collection_paths")
    @classmethod
    def normalize_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        paths = tuple(sorted({_non_empty(path).replace("\\", "/").strip("/") for path in value}))
        if not paths:
            raise ValueError("collection_paths must not be empty")
        return paths

    @field_validator("added_at")
    @classmethod
    def require_aware_added_at(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("feedback_weight")
    @classmethod
    def require_finite_weight(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("feedback_weight must be finite")
        return value


class CandidatePaper(StrictModel):
    paper_id: str
    arxiv_id: str
    version: int = Field(ge=1)
    title: str
    authors: tuple[str, ...]
    abstract: str
    categories: tuple[str, ...]
    primary_category: str
    published_at: datetime
    updated_at: datetime
    arxiv_url: str
    pdf_url: str
    code_url: str | None = None

    @field_validator("title", "abstract", "arxiv_url", "pdf_url")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("arxiv_id")
    @classmethod
    def validate_arxiv_id(cls, value: str) -> str:
        normalized = value.strip().removeprefix("arxiv:")
        if re.search(r"v\d+$", normalized, re.IGNORECASE) or not _ARXIV_ID_RE.fullmatch(normalized):
            raise ValueError("arxiv_id must be a normalized base identifier")
        return normalized

    @field_validator("authors")
    @classmethod
    def normalize_authors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        authors = tuple(_non_empty(author) for author in value)
        if not authors:
            raise ValueError("authors must not be empty")
        return authors

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        categories = tuple(sorted({_non_empty(category) for category in value}))
        if not categories or any(not _CATEGORY_RE.fullmatch(category) for category in categories):
            raise ValueError("categories contain an invalid identifier")
        return categories

    @field_validator("published_at", "updated_at")
    @classmethod
    def require_aware_dates(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.paper_id != f"arxiv:{self.arxiv_id}":
            raise ValueError("paper_id must match arxiv_id")
        if self.primary_category not in self.categories:
            raise ValueError("primary_category must occur in categories")
        if self.updated_at < self.published_at:
            raise ValueError("updated_at must not precede published_at")
        return self


class RankingModelVersions(StrictModel):
    provider: str
    model: str
    task: str
    scorer: str
    embedding_identity_hash: str

    @field_validator("provider", "model", "task", "scorer")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("embedding_identity_hash")
    @classmethod
    def validate_identity_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("embedding_identity_hash must be a lowercase SHA-256 digest")
        return value


class RankingRecord(StrictModel):
    paper_id: str
    embedding_score: float
    feedback_adjustment: float = Field(default=0.0, ge=0.0, le=0.10)
    llm_score: None = None
    final_score: float
    rank: int = Field(ge=1)
    reason: str
    model_versions: RankingModelVersions

    @field_validator("paper_id", "reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("embedding_score", "feedback_adjustment", "final_score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("ranking score must be finite")
        return value

    @model_validator(mode="after")
    def validate_stage_one_score(self) -> Self:
        if not math.isclose(
            self.final_score,
            self.embedding_score + self.feedback_adjustment,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "Stage 1 final_score must equal embedding_score plus feedback_adjustment"
            )
        return self


class CandidateCounts(StrictModel):
    retrieved: int = Field(ge=0)
    deduplicated: int = Field(ge=0)
    invalid: int = Field(ge=0)
    excluded: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.deduplicated > self.retrieved:
            raise ValueError("deduplicated count cannot exceed retrieved count")
        return self


class CandidateSelectionLimits(StrictModel):
    candidate_pool_size: int = Field(default=30, ge=1, le=30)
    llm_rerank_limit: int = Field(default=15, ge=1, le=15)
    full_analysis_limit: int = Field(default=5, ge=1, le=5)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.full_analysis_limit > self.llm_rerank_limit:
            raise ValueError("full_analysis_limit cannot exceed llm_rerank_limit")
        if self.llm_rerank_limit > self.candidate_pool_size:
            raise ValueError("llm_rerank_limit cannot exceed candidate_pool_size")
        return self


class CandidateBatch(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    created_at: datetime
    retrieved_at: datetime
    categories: tuple[str, ...]
    config_hash: str
    interest_corpus_fingerprint: str
    candidates: tuple[CandidatePaper, ...]
    rankings: tuple[RankingRecord, ...]
    selected_for_llm: tuple[str, ...]
    selected_for_full_analysis: tuple[str, ...]
    counts: CandidateCounts
    limits: CandidateSelectionLimits = CandidateSelectionLimits()

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return validate_run_id_value(value)

    @field_validator("created_at", "retrieved_at")
    @classmethod
    def require_aware_dates(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value)))
        if not normalized or any(not _CATEGORY_RE.fullmatch(category) for category in normalized):
            raise ValueError("categories contain an invalid identifier")
        return normalized

    @field_validator("config_hash", "interest_corpus_fingerprint")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("value must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        ids = tuple(paper.paper_id for paper in self.candidates)
        if len(ids) > self.limits.candidate_pool_size:
            raise ValueError("candidates must be capped at 30 and the configured candidate_pool_size")
        if len(set(ids)) != len(ids):
            raise ValueError("candidate IDs must be unique")
        if tuple(record.paper_id for record in self.rankings) != ids:
            raise ValueError("rankings must follow candidate order")
        if tuple(record.rank for record in self.rankings) != tuple(range(1, len(ids) + 1)):
            raise ValueError("ranks must be consecutive")
        if self.selected_for_llm != ids[: min(self.limits.llm_rerank_limit, len(ids))]:
            raise ValueError("selected_for_llm must be the configured ordered prefix capped at 15")
        if self.selected_for_full_analysis != ids[: min(self.limits.full_analysis_limit, len(ids))]:
            raise ValueError("selected_for_full_analysis must be the configured ordered prefix capped at 5")
        if any(record.llm_score is not None for record in self.rankings):
            raise ValueError("Stage 1 llm_score must be null")
        return self

    def to_deterministic_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"
