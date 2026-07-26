from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

import numpy as np
from pydantic import Field, field_validator

from zotero_arxiv_daily.analysis.schemas import (
    CandidatePaper,
    CandidateSelectionLimits,
    InterestPaper,
    RankingModelVersions,
    RankingRecord,
    StrictModel,
)
from zotero_arxiv_daily.reranker.base import weighted_similarity_scores
from zotero_arxiv_daily.viewer.feedback import (
    InterestFeedbackProjection,
    normalize_feedback_paper_id,
)


class EmbeddingIdentity(StrictModel):
    provider: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    task: str = Field(min_length=1)
    settings_json: str
    dimension: int = Field(ge=1)
    dtype: str
    cache_schema_version: str = "1"

    @field_validator("settings_json")
    @classmethod
    def validate_canonical_settings(cls, value: str) -> str:
        parsed = json.loads(value)
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if value != canonical:
            raise ValueError("settings_json must be canonical JSON")
        return value

    @field_validator("dtype")
    @classmethod
    def normalize_dtype(cls, value: str) -> str:
        dtype = np.dtype(value)
        if dtype.kind != "f":
            raise ValueError("embedding dtype must be floating point")
        return dtype.name

    @classmethod
    def from_settings(
        cls, *, provider: str, implementation_version: str, model: str, task: str,
        settings: dict[str, object], dimension: int, dtype: str,
    ) -> "EmbeddingIdentity":
        return cls(
            provider=provider, implementation_version=implementation_version, model=model,
            task=task,
            settings_json=json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            dimension=dimension, dtype=dtype,
        )

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


RankingLimits = CandidateSelectionLimits


class EmbeddingProvider(Protocol):
    identity: EmbeddingIdentity

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class SentenceTransformerEmbeddingProvider:
    """Local production embedding provider with a complete cache identity."""

    def __init__(
        self,
        *,
        model: str,
        task: str,
        prompt_name: str | None,
        encode_kwargs: dict[str, Any],
        model_factory: Callable[[str], Any] | None = None,
        implementation_version: str | None = None,
    ) -> None:
        if model_factory is None:
            from sentence_transformers import SentenceTransformer

            model_factory = lambda name: SentenceTransformer(name, trust_remote_code=True)
        self._encoder = model_factory(model)
        self._task = task
        self._prompt_name = prompt_name
        self._encode_kwargs = dict(encode_kwargs)
        dimension = self._encoder.get_sentence_embedding_dimension()
        if not isinstance(dimension, int) or dimension <= 0:
            raise ValueError("embedding model must report a positive dimension")
        version = implementation_version or importlib.metadata.version("sentence-transformers")
        self.identity = EmbeddingIdentity.from_settings(
            provider="sentence-transformers",
            implementation_version=version,
            model=model,
            task=task,
            settings={"prompt_name": prompt_name, "encode_kwargs": self._encode_kwargs},
            dimension=dimension,
            dtype="float32",
        )

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        kwargs = dict(self._encode_kwargs)
        kwargs["task"] = self._task
        if self._prompt_name is not None:
            kwargs["prompt_name"] = self._prompt_name
        return np.asarray(self._encoder.encode(list(texts), **kwargs), dtype=np.float32)


class RankedCandidates(StrictModel):
    candidates: tuple[CandidatePaper, ...]
    rankings: tuple[RankingRecord, ...]
    selected_for_llm: tuple[str, ...]
    selected_for_full_analysis: tuple[str, ...]


class FileEmbeddingCache:
    """Versioned, per-text NumPy cache with atomic writes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _paths(self, identity: EmbeddingIdentity, text: str) -> tuple[Path, Path, str]:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        key = hashlib.sha256(f"{identity.fingerprint()}:{text_hash}".encode("ascii")).hexdigest()
        return self.root / f"{key}.npy", self.root / f"{key}.json", text_hash

    def load(self, identity: EmbeddingIdentity, text: str) -> np.ndarray | None:
        path, manifest_path, text_hash = self._paths(identity, text)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            vector = np.load(path, allow_pickle=False)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return None
        expected_manifest = {
            "cache_schema_version": identity.cache_schema_version,
            "identity": identity.model_dump(mode="json"),
            "text_sha256": text_hash,
            "dimension": identity.dimension,
            "dtype": identity.dtype,
            "shape": [identity.dimension],
        }
        if manifest != expected_manifest:
            return None
        if (
            vector.shape != (identity.dimension,)
            or vector.dtype.name != identity.dtype
            or not np.isfinite(vector).all()
        ):
            return None
        return np.asarray(vector)

    def store(self, identity: EmbeddingIdentity, text: str, vector: np.ndarray) -> None:
        value = np.asarray(vector)
        if value.shape != (identity.dimension,) or value.dtype.name != identity.dtype or not np.isfinite(value).all():
            raise ValueError("embedding must match the identity dimension and dtype")
        self.root.mkdir(parents=True, exist_ok=True)
        destination, manifest_path, text_hash = self._paths(identity, text)
        token = f"{os.getpid()}.{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"
        temporary = destination.with_name(f"{destination.name}.{token}.tmp")
        manifest_temporary = manifest_path.with_name(f"{manifest_path.name}.{token}.tmp")
        manifest = {
            "cache_schema_version": identity.cache_schema_version,
            "identity": identity.model_dump(mode="json"),
            "text_sha256": text_hash,
            "dimension": identity.dimension,
            "dtype": identity.dtype,
            "shape": [identity.dimension],
        }
        try:
            with temporary.open("wb") as stream:
                np.save(stream, value, allow_pickle=False)
                stream.flush()
                os.fsync(stream.fileno())
            with manifest_temporary.open("wb") as stream:
                stream.write(
                    (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            os.replace(manifest_temporary, manifest_path)
        finally:
            temporary.unlink(missing_ok=True)
            manifest_temporary.unlink(missing_ok=True)


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
            if generated.shape[1] != self.identity.dimension or generated.dtype.name != self.identity.dtype:
                raise ValueError("embedding provider output does not match its identity")
            if not np.isfinite(generated).all():
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
    SCORE_MIN = -10.0
    SCORE_MAX = 10.0

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
        feedback: InterestFeedbackProjection | None = None,
    ) -> RankedCandidates:
        limits = limits or RankingLimits()
        try:
            projection = (
                InterestFeedbackProjection()
                if feedback is None
                else InterestFeedbackProjection.model_validate(feedback)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("feedback projection rejected") from error
        ordered_interests = tuple(sorted(interests, key=lambda paper: paper.added_at, reverse=True))
        if not ordered_interests:
            raise ValueError("interest corpus must not be empty")
        eligible_candidates = tuple(
            paper
            for paper in candidates
            if normalize_feedback_paper_id(paper.arxiv_id) not in projection.irrelevant_ids
        )
        if not eligible_candidates:
            return RankedCandidates(
                candidates=(), rankings=(), selected_for_llm=(), selected_for_full_analysis=()
            )
        candidate_vectors = np.asarray(
            self.provider.encode(tuple(self._text(p) for p in eligible_candidates))
        )
        interest_vectors = np.asarray(self.provider.encode(tuple(self._text(p) for p in ordered_interests)))
        scores = weighted_similarity_scores(self._cosine(candidate_vectors, interest_vectors))
        scored = tuple(
            (
                paper,
                min(
                    self.SCORE_MAX,
                    max(
                        self.SCORE_MIN,
                        float(score)
                        + (
                            projection.favorite_delta
                            if normalize_feedback_paper_id(paper.arxiv_id) in projection.favorite_ids
                            else 0.0
                        ),
                    ),
                ),
            )
            for paper, score in zip(eligible_candidates, scores, strict=True)
        )
        ordered = sorted(scored, key=lambda item: (-item[1], item[0].paper_id))
        ordered = ordered[: limits.candidate_pool_size]
        versions = RankingModelVersions(
            provider=self.provider.identity.provider,
            model=self.provider.identity.model,
            task=self.provider.identity.task,
            scorer=self.SCORER_VERSION,
            embedding_identity_hash=self.provider.identity.fingerprint(),
        )
        papers = tuple(item[0] for item in ordered)
        rankings = tuple(
            RankingRecord(
                paper_id=paper.paper_id,
                embedding_score=float(score),
                final_score=float(score),
                rank=index,
                reason=(
                    "embedding similarity to the Zotero interest corpus; "
                    "explicit feedback adjustment applied"
                    if normalize_feedback_paper_id(paper.arxiv_id) in projection.favorite_ids
                    else "embedding similarity to the Zotero interest corpus"
                ),
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
