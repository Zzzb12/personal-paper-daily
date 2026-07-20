from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
from pydantic import Field, model_validator

from zotero_arxiv_daily.analysis.schemas import (
    CandidatePaper,
    InterestPaper,
    RankingModelVersions,
    RankingRecord,
    StrictModel,
)
from zotero_arxiv_daily.reranker.base import weighted_similarity_scores


class EmbeddingIdentity(StrictModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    task: str = Field(min_length=1)


class RankingLimits(StrictModel):
    candidate_pool_size: int = Field(default=30, ge=1, le=30)
    llm_rerank_limit: int = Field(default=15, ge=1, le=15)
    full_analysis_limit: int = Field(default=5, ge=1, le=5)

    @model_validator(mode="after")
    def validate_order(self) -> "RankingLimits":
        if self.full_analysis_limit > self.llm_rerank_limit:
            raise ValueError("full_analysis_limit cannot exceed llm_rerank_limit")
        if self.llm_rerank_limit > self.candidate_pool_size:
            raise ValueError("llm_rerank_limit cannot exceed candidate_pool_size")
        return self


class EmbeddingProvider(Protocol):
    identity: EmbeddingIdentity

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class RankedCandidates(StrictModel):
    candidates: tuple[CandidatePaper, ...]
    rankings: tuple[RankingRecord, ...]
    selected_for_llm: tuple[str, ...]
    selected_for_full_analysis: tuple[str, ...]


class FileEmbeddingCache:
    """Versioned, per-text NumPy cache with atomic writes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, identity: EmbeddingIdentity, text: str) -> Path:
        payload = json.dumps(
            {"cache_version": 1, "identity": identity.model_dump(mode="json"), "text": text},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.root / f"{hashlib.sha256(payload).hexdigest()}.npy"

    def load(self, identity: EmbeddingIdentity, text: str) -> np.ndarray | None:
        path = self._path(identity, text)
        try:
            vector = np.load(path, allow_pickle=False)
        except (FileNotFoundError, OSError, ValueError):
            return None
        if vector.ndim != 1 or vector.size == 0 or not np.isfinite(vector).all():
            return None
        return np.asarray(vector)

    def store(self, identity: EmbeddingIdentity, text: str, vector: np.ndarray) -> None:
        value = np.asarray(vector)
        if value.ndim != 1 or value.size == 0 or not np.isfinite(value).all():
            raise ValueError("embedding must be a non-empty finite vector")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path(identity, text)
        temporary = destination.with_suffix(f".{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as stream:
                np.save(stream, value, allow_pickle=False)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


class CachedEmbeddingProvider:
    def __init__(self, delegate: EmbeddingProvider, cache: FileEmbeddingCache) -> None:
        self.delegate = delegate
        self.cache = cache
        self.identity = delegate.identity

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        items = tuple(texts)
        if not items:
            return np.empty((0, 0), dtype=np.float64)
        vectors: list[np.ndarray | None] = [self.cache.load(self.identity, text) for text in items]
        missing_indexes = [index for index, vector in enumerate(vectors) if vector is None]
        if missing_indexes:
            missing_texts = tuple(items[index] for index in missing_indexes)
            generated = np.asarray(self.delegate.encode(missing_texts))
            if generated.ndim != 2 or generated.shape[0] != len(missing_texts):
                raise ValueError("embedding provider returned an invalid matrix shape")
            if generated.shape[1] == 0 or not np.isfinite(generated).all():
                raise ValueError("embedding provider returned invalid values")
            for index, vector in zip(missing_indexes, generated, strict=True):
                self.cache.store(self.identity, items[index], vector)
                vectors[index] = vector
        dimensions = {vector.shape for vector in vectors if vector is not None}
        if len(dimensions) != 1:
            raise ValueError("cached embeddings have inconsistent dimensions")
        return np.stack(vectors)


class CandidateRanker:
    SCORER_VERSION = "weighted-cosine-v1"

    def __init__(self, provider: EmbeddingProvider) -> None:
        self.provider = provider

    @staticmethod
    def _text(paper: CandidatePaper | InterestPaper) -> str:
        return f"{paper.title}\n\n{paper.abstract}"

    @staticmethod
    def _cosine(left: np.ndarray, right: np.ndarray) -> np.ndarray:
        if left.ndim != 2 or right.ndim != 2 or left.shape[1] != right.shape[1]:
            raise ValueError("embedding matrices must have matching dimensions")
        left_norm = np.linalg.norm(left, axis=1, keepdims=True)
        right_norm = np.linalg.norm(right, axis=1, keepdims=True)
        if np.any(left_norm == 0) or np.any(right_norm == 0):
            raise ValueError("zero-length embeddings cannot be ranked")
        return (left / left_norm) @ (right / right_norm).T

    def rank(
        self,
        candidates: Sequence[CandidatePaper],
        interests: Sequence[InterestPaper],
        limits: RankingLimits | None = None,
    ) -> RankedCandidates:
        limits = limits or RankingLimits()
        ordered_interests = tuple(sorted(interests, key=lambda paper: paper.added_at, reverse=True))
        if not ordered_interests:
            raise ValueError("interest corpus must not be empty")
        if not candidates:
            return RankedCandidates(
                candidates=(), rankings=(), selected_for_llm=(), selected_for_full_analysis=()
            )
        candidate_vectors = np.asarray(self.provider.encode(tuple(self._text(p) for p in candidates)))
        interest_vectors = np.asarray(self.provider.encode(tuple(self._text(p) for p in ordered_interests)))
        scores = weighted_similarity_scores(self._cosine(candidate_vectors, interest_vectors))
        ordered = sorted(zip(candidates, scores, strict=True), key=lambda item: (-item[1], item[0].paper_id))
        ordered = ordered[: limits.candidate_pool_size]
        versions = RankingModelVersions(
            provider=self.provider.identity.provider,
            model=self.provider.identity.model,
            task=self.provider.identity.task,
            scorer=self.SCORER_VERSION,
        )
        papers = tuple(item[0] for item in ordered)
        rankings = tuple(
            RankingRecord(
                paper_id=paper.paper_id,
                embedding_score=float(score),
                final_score=float(score),
                rank=index,
                reason="embedding similarity to the Zotero interest corpus",
                model_versions=versions,
            )
            for index, (paper, score) in enumerate(ordered, start=1)
        )
        ids = tuple(paper.paper_id for paper in papers)
        return RankedCandidates(
            candidates=papers,
            rankings=rankings,
            selected_for_llm=ids[: limits.llm_rerank_limit],
            selected_for_full_analysis=ids[: limits.full_analysis_limit],
        )
