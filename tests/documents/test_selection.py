from datetime import UTC, datetime

import pytest

from zotero_arxiv_daily.analysis.schemas import (
    CandidateBatch,
    CandidateCounts,
    CandidatePaper,
    CandidateSelectionLimits,
    RankingModelVersions,
    RankingRecord,
)
from zotero_arxiv_daily.documents.selection import selected_papers


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def candidate(index: int) -> CandidatePaper:
    arxiv_id = f"2401.{index:05d}"
    return CandidatePaper(
        paper_id=f"arxiv:{arxiv_id}",
        arxiv_id=arxiv_id,
        version=1,
        title=f"Paper {index}",
        authors=("Author",),
        abstract="Abstract",
        categories=("cs.CV",),
        primary_category="cs.CV",
        published_at=NOW,
        updated_at=NOW,
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


def batch(count: int = 8, selected: int = 5) -> CandidateBatch:
    papers = tuple(candidate(index) for index in range(1, count + 1))
    versions = RankingModelVersions(
        provider="fixture",
        model="fixture",
        task="retrieval",
        scorer="cosine",
        embedding_identity_hash="a" * 64,
    )
    rankings = tuple(
        RankingRecord(
            paper_id=paper.paper_id,
            embedding_score=1.0 / rank,
            final_score=1.0 / rank,
            rank=rank,
            reason="metadata score",
            model_versions=versions,
        )
        for rank, paper in enumerate(papers, start=1)
    )
    limits = CandidateSelectionLimits(
        candidate_pool_size=max(count, 1),
        llm_rerank_limit=max(selected, 1),
        full_analysis_limit=max(selected, 1),
    )
    return CandidateBatch(
        run_id="20260720T000000Z",
        created_at=NOW,
        retrieved_at=NOW,
        categories=("cs.CV",),
        config_hash="b" * 64,
        interest_corpus_fingerprint="c" * 64,
        candidates=papers,
        rankings=rankings,
        selected_for_llm=tuple(paper.paper_id for paper in papers[:selected]),
        selected_for_full_analysis=tuple(paper.paper_id for paper in papers[:selected]),
        counts=CandidateCounts(retrieved=count, deduplicated=count, invalid=0, excluded=0),
        limits=limits,
    )


def test_selection_returns_only_the_ordered_full_analysis_prefix():
    papers = selected_papers(batch())
    assert tuple(paper.paper_id for paper in papers) == tuple(
        f"arxiv:2401.{index:05d}" for index in range(1, 6)
    )


def test_selection_has_an_independent_hard_limit_of_five():
    with pytest.raises(ValueError, match="hard limit"):
        selected_papers(batch(), hard_limit=4)


def test_selection_rejects_a_missing_candidate_instead_of_substituting():
    valid = batch(count=3, selected=2)
    untrusted = valid.model_copy(
        update={"selected_for_full_analysis": (valid.candidates[0].paper_id, "arxiv:missing")}
    )
    with pytest.raises(ValueError, match="missing candidate"):
        selected_papers(untrusted)
