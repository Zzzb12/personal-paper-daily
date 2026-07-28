from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np
from omegaconf import OmegaConf
from pydantic import Field

from zotero_arxiv_daily.analysis.schemas import CandidateBatch, CandidateCounts, StrictModel
from zotero_arxiv_daily.candidates.ranking import (
    CandidateRanker,
    CachedEmbeddingProvider,
    EmbeddingIdentity,
    FileEmbeddingCache,
    RankedCandidates,
    RankingLimits,
    SentenceTransformerEmbeddingProvider,
)
from zotero_arxiv_daily.candidates.feedback import (
    FEEDBACK_PROJECTION_IMPLEMENTATION_VERSION,
    FeedbackProjectionLoader,
)
from zotero_arxiv_daily.candidates.store import CandidateStore
from zotero_arxiv_daily.viewer.feedback import InterestFeedbackProjection
from zotero_arxiv_daily.interest.base import (
    InterestReadResult,
    ZoteroCollection,
    ZoteroItem,
)
from zotero_arxiv_daily.interest.zotero import PyzoteroGateway, RetryPolicy, ZoteroInterestProvider
from zotero_arxiv_daily.retriever.arxiv_retriever import (
    ArxivMetadataEntry,
    ArxivMetadataResult,
    ArxivMetadataRetriever,
    ArxivRetryPolicy,
    HttpArxivMetadataGateway,
)


class EmptyInterestCorpusError(ValueError):
    pass


class InterestProvider(Protocol):
    def read(self) -> InterestReadResult: ...


class MetadataRetriever(Protocol):
    def retrieve(self) -> ArxivMetadataResult: ...


class Ranker(Protocol):
    def rank(
        self,
        candidates: Sequence[Any],
        interests: Sequence[Any],
        limits: RankingLimits,
        feedback: InterestFeedbackProjection | None = None,
    ) -> RankedCandidates: ...


class Store(Protocol):
    def write(self, batch: CandidateBatch) -> Any: ...


class CandidatePipelineSettings(StrictModel):
    categories: tuple[str, ...] = ("cs.CV", "cs.LG", "cs.AI")
    include_paths: tuple[str, ...] = (
        "PaperDaily/00-Seeds/**", "PaperDaily/03-Read/**", "PaperDaily/04-Favorite/**"
    )
    exclude_paths: tuple[str, ...] = ("PaperDaily/99-Exclude/**",)
    include_cross_list: bool = False
    candidate_pool_size: int = Field(default=30, ge=1, le=30)
    llm_rerank_limit: int = Field(default=15, ge=1, le=15)
    full_analysis_limit: int = Field(default=5, ge=1, le=5)
    output_dir: Path = Path("data/candidates")
    embedding_cache_dir: Path = Path("cache/embeddings")
    feedback_store_path: Path | None = None
    feedback_favorite_delta: float = Field(default=0.05, ge=0.0, le=0.10)
    dry_run: bool = False

    def ranking_limits(self) -> RankingLimits:
        return RankingLimits(
            candidate_pool_size=self.candidate_pool_size,
            llm_rerank_limit=self.llm_rerank_limit,
            full_analysis_limit=self.full_analysis_limit,
        )

    def config_hash(self, embedding_identity_hash: str | None = None) -> str:
        configuration = self.model_dump(
            mode="json", exclude={"dry_run", "feedback_store_path"}
        )
        configuration["feedback_projection_implementation_version"] = (
            FEEDBACK_PROJECTION_IMPLEMENTATION_VERSION
        )
        configuration["embedding_identity_hash"] = embedding_identity_hash
        payload = json.dumps(
            configuration,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass
class CandidatePipelineDependencies:
    interest_provider: InterestProvider
    arxiv_retriever: MetadataRetriever
    ranker: Ranker
    store: Store
    run_id_factory: Callable[[datetime], str]
    feedback: InterestFeedbackProjection = InterestFeedbackProjection()
    close_callbacks: tuple[Callable[[], None], ...] = ()
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for callback in reversed(self.close_callbacks):
            callback()


def build_candidate_batch(
    settings: CandidatePipelineSettings,
    dependencies: CandidatePipelineDependencies,
    clock: Callable[[], datetime],
) -> CandidateBatch:
    interests = dependencies.interest_provider.read()
    if not interests.papers:
        raise EmptyInterestCorpusError("no eligible Zotero interest papers")
    metadata = dependencies.arxiv_retriever.retrieve()
    ranked = dependencies.ranker.rank(
        metadata.candidates,
        interests.papers,
        settings.ranking_limits(),
        feedback=dependencies.feedback,
    )
    now = clock()
    provider = getattr(dependencies.ranker, "provider", None)
    identity = getattr(provider, "identity", None)
    identity_hash = identity.fingerprint() if isinstance(identity, EmbeddingIdentity) else None
    batch = CandidateBatch(
        run_id=dependencies.run_id_factory(now),
        created_at=now,
        retrieved_at=now,
        categories=settings.categories,
        config_hash=settings.config_hash(identity_hash),
        interest_corpus_fingerprint=interests.corpus_fingerprint,
        candidates=ranked.candidates,
        rankings=ranked.rankings,
        selected_for_llm=ranked.selected_for_llm,
        selected_for_full_analysis=ranked.selected_for_full_analysis,
        counts=CandidateCounts(
            retrieved=metadata.retrieved_count,
            deduplicated=metadata.deduplicated_count,
            invalid=metadata.invalid_count,
            excluded=interests.excluded_count,
        ),
        limits=settings.ranking_limits(),
    )
    if not settings.dry_run:
        dependencies.store.write(batch)
    return batch


class _FixtureZoteroGateway:
    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture

    def list_collections(self) -> tuple[ZoteroCollection, ...]:
        return tuple(ZoteroCollection.model_validate(item) for item in self.fixture["collections"])

    def list_items(self) -> tuple[ZoteroItem, ...]:
        return tuple(ZoteroItem.model_validate(item) for item in self.fixture["items"])


class _FixtureArxivGateway:
    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture

    def retrieve_entries(
        self, categories: tuple[str, ...], include_cross_list: bool
    ) -> tuple[ArxivMetadataEntry, ...]:
        del categories, include_cross_list
        return tuple(
            ArxivMetadataEntry.model_validate(item) for item in self.fixture["arxiv_entries"]
        )


class _FixtureEmbeddings:
    identity = EmbeddingIdentity.from_settings(
        provider="offline-fixture", implementation_version="1", model="v1", task="retrieval",
        settings={}, dimension=2, dtype="float64",
    )

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.embeddings = fixture["embeddings"]

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        try:
            return np.asarray([self.embeddings[text] for text in texts], dtype=np.float64)
        except KeyError as exc:
            raise ValueError(f"offline fixture lacks an embedding for {exc.args[0]!r}") from exc


def _offline_dependencies(fixture: dict[str, Any]) -> CandidatePipelineDependencies:
    interest_provider = ZoteroInterestProvider(
        _FixtureZoteroGateway(fixture),
        include_paths=(
            "PaperDaily/00-Seeds/**",
            "PaperDaily/03-Read/**",
            "PaperDaily/04-Favorite/**",
        ),
        exclude_paths=("PaperDaily/99-Exclude/**",),
    )
    retriever = ArxivMetadataRetriever(
        _FixtureArxivGateway(fixture), categories=("cs.CV", "cs.LG", "cs.AI")
    )
    return CandidatePipelineDependencies(
        interest_provider=interest_provider,
        arxiv_retriever=retriever,
        ranker=CandidateRanker(_FixtureEmbeddings(fixture)),
        store=CandidateStore(Path("data/candidates")),
        run_id_factory=lambda value: value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
        feedback=InterestFeedbackProjection(),
    )


def _merged_config(config_dir: Path) -> Any:
    base = OmegaConf.load(config_dir / "base.yaml")
    custom_path = config_dir / "custom.yaml"
    return OmegaConf.merge(base, OmegaConf.load(custom_path) if custom_path.exists() else {})


def build_production_pipeline(
    config_dir: Path,
    *,
    environ: dict[str, str] | os._Environ[str],
    dry_run: bool,
) -> tuple[CandidatePipelineSettings, CandidatePipelineDependencies]:
    library_id = environ.get("ZOTERO_ID", "").strip()
    api_key = environ.get("ZOTERO_KEY", "").strip()
    if not library_id or not api_key:
        raise RuntimeError("ZOTERO_ID and ZOTERO_KEY must be set in the process environment")

    config = _merged_config(Path(config_dir))
    feedback_config = config.candidate_pipeline.feedback
    configured_feedback_path = feedback_config.store_path
    feedback_path = (
        None if configured_feedback_path is None else Path(str(configured_feedback_path))
    )
    feedback = FeedbackProjectionLoader.from_optional_path(
        feedback_path,
        root=Path.cwd(),
        favorite_delta=float(feedback_config.favorite_delta),
    ).load()
    timeout_values = OmegaConf.to_container(config.candidate_pipeline.request_timeout, resolve=True)
    retry_values = OmegaConf.to_container(config.candidate_pipeline.retry, resolve=True)
    timeout = httpx.Timeout(**timeout_values)
    zotero_retry = RetryPolicy(**retry_values)
    arxiv_retry = ArxivRetryPolicy(**retry_values)
    zotero_gateway = PyzoteroGateway.from_credentials(
        library_id, api_key, timeout=timeout, retry_policy=zotero_retry
    )
    try:
        arxiv_gateway = HttpArxivMetadataGateway.from_defaults(
            timeout=timeout, retry_policy=arxiv_retry
        )
        try:
            raw_encode = OmegaConf.to_container(config.reranker.local.encode_kwargs, resolve=True)
            encode_kwargs = dict(raw_encode or {})
            task = str(encode_kwargs.pop("task", "retrieval"))
            prompt_name = encode_kwargs.pop("prompt_name", None)
            embedding_provider = SentenceTransformerEmbeddingProvider(
                model=str(config.reranker.local.model),
                revision=str(config.reranker.local.revision),
                cache_folder=Path(str(config.reranker.local.cache_folder)),
                task=task,
                prompt_name=prompt_name,
                encode_kwargs=encode_kwargs,
            )
        except Exception:
            arxiv_gateway.close()
            raise
    except Exception:
        zotero_gateway.close()
        raise

    settings = CandidatePipelineSettings(
        categories=tuple(config.source.arxiv.category),
        include_paths=tuple(config.zotero.include_path),
        exclude_paths=tuple(config.zotero.ignore_path),
        include_cross_list=bool(config.source.arxiv.include_cross_list),
        candidate_pool_size=int(config.candidate_pipeline.candidate_pool_size),
        llm_rerank_limit=int(config.candidate_pipeline.llm_rerank_limit),
        full_analysis_limit=int(config.candidate_pipeline.full_analysis_limit),
        output_dir=Path(str(config.candidate_pipeline.output_dir)),
        embedding_cache_dir=Path(str(config.candidate_pipeline.embedding_cache_dir)),
        feedback_store_path=feedback_path,
        feedback_favorite_delta=float(feedback_config.favorite_delta),
        dry_run=dry_run,
    )
    ranking_provider = (
        embedding_provider
        if dry_run
        else CachedEmbeddingProvider(
            embedding_provider, FileEmbeddingCache(settings.embedding_cache_dir)
        )
    )
    dependencies = CandidatePipelineDependencies(
        interest_provider=ZoteroInterestProvider(
            zotero_gateway,
            include_paths=settings.include_paths,
            exclude_paths=settings.exclude_paths,
        ),
        arxiv_retriever=ArxivMetadataRetriever(
            arxiv_gateway,
            categories=settings.categories,
            include_cross_list=settings.include_cross_list,
        ),
        ranker=CandidateRanker(ranking_provider),
        store=CandidateStore(settings.output_dir),
        run_id_factory=lambda value: value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
        feedback=feedback,
        close_callbacks=tuple(
            callback
            for callback in (
                zotero_gateway.close,
                arxiv_gateway.close,
                getattr(ranking_provider, "close", None),
            )
            if callable(callback)
        ),
    )
    return settings, dependencies


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Stage 1 candidate batch")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline-fixture", type=Path)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    args = parser.parse_args(argv)
    if args.offline_fixture:
        if not args.dry_run:
            parser.error("--offline-fixture requires --dry-run")
        fixture = json.loads(args.offline_fixture.read_text(encoding="utf-8"))
        now = datetime.fromisoformat(fixture["now"].replace("Z", "+00:00"))
        settings = CandidatePipelineSettings(dry_run=True)
        dependencies = _offline_dependencies(fixture)
        clock = lambda: now
    else:
        settings, dependencies = build_production_pipeline(
            args.config_dir, environ=os.environ, dry_run=args.dry_run
        )
        clock = lambda: datetime.now(UTC)
    try:
        batch = build_candidate_batch(settings, dependencies, clock)
    finally:
        dependencies.close()
    print(
        json.dumps(
            {
                "schema_version": batch.schema_version,
                "counts": batch.counts.model_dump(),
                "candidate_count": len(batch.candidates),
                "llm_count": len(batch.selected_for_llm),
                "full_analysis_count": len(batch.selected_for_full_analysis),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
