from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from pydantic import Field

from zotero_arxiv_daily.analysis.schemas import CandidateBatch, CandidateCounts, StrictModel
from zotero_arxiv_daily.candidates.ranking import (
    CandidateRanker,
    EmbeddingIdentity,
    RankedCandidates,
    RankingLimits,
)
from zotero_arxiv_daily.candidates.store import CandidateStore
from zotero_arxiv_daily.interest.base import (
    InterestReadResult,
    ZoteroCollection,
    ZoteroItem,
)
from zotero_arxiv_daily.interest.zotero import ZoteroInterestProvider
from zotero_arxiv_daily.retriever.arxiv_retriever import (
    ArxivMetadataEntry,
    ArxivMetadataResult,
    ArxivMetadataRetriever,
)


class EmptyInterestCorpusError(ValueError):
    pass


class InterestProvider(Protocol):
    def read(self) -> InterestReadResult: ...


class MetadataRetriever(Protocol):
    def retrieve(self) -> ArxivMetadataResult: ...


class Ranker(Protocol):
    def rank(self, candidates: Sequence[Any], interests: Sequence[Any], limits: RankingLimits) -> RankedCandidates: ...


class Store(Protocol):
    def write(self, batch: CandidateBatch) -> Any: ...


class CandidatePipelineSettings(StrictModel):
    categories: tuple[str, ...] = ("cs.CV", "cs.LG", "cs.AI")
    candidate_pool_size: int = Field(default=30, ge=1, le=30)
    llm_rerank_limit: int = Field(default=15, ge=1, le=15)
    full_analysis_limit: int = Field(default=5, ge=1, le=5)
    dry_run: bool = False

    def ranking_limits(self) -> RankingLimits:
        return RankingLimits(
            candidate_pool_size=self.candidate_pool_size,
            llm_rerank_limit=self.llm_rerank_limit,
            full_analysis_limit=self.full_analysis_limit,
        )

    def config_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass
class CandidatePipelineDependencies:
    interest_provider: InterestProvider
    arxiv_retriever: MetadataRetriever
    ranker: Ranker
    store: Store
    run_id_factory: Callable[[datetime], str]


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
        metadata.candidates, interests.papers, settings.ranking_limits()
    )
    now = clock()
    batch = CandidateBatch(
        run_id=dependencies.run_id_factory(now),
        created_at=now,
        retrieved_at=now,
        categories=settings.categories,
        config_hash=settings.config_hash(),
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
    identity = EmbeddingIdentity(provider="offline-fixture", model="v1", task="retrieval")

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
        run_id_factory=lambda value: value.strftime("%Y%m%dT%H%M%SZ"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Stage 1 candidate batch")
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--offline-fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    fixture = json.loads(args.offline_fixture.read_text(encoding="utf-8"))
    now = datetime.fromisoformat(fixture["now"].replace("Z", "+00:00"))
    batch = build_candidate_batch(
        CandidatePipelineSettings(dry_run=True), _offline_dependencies(fixture), lambda: now
    )
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
