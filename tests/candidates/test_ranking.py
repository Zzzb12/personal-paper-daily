from datetime import UTC, datetime, timedelta

import numpy as np

from zotero_arxiv_daily.analysis.schemas import CandidatePaper, InterestPaper
from zotero_arxiv_daily.candidates.ranking import (
    CandidateRanker,
    EmbeddingIdentity,
    RankingLimits,
)
from zotero_arxiv_daily.reranker.base import weighted_similarity_scores


NOW = datetime(2026, 7, 20, tzinfo=UTC)


class FakeProvider:
    identity = EmbeddingIdentity.from_settings(
        provider="fake", implementation_version="1", model="v1", task="retrieval",
        settings={}, dimension=2, dtype="float64",
    )

    def __init__(self, vectors):
        self.vectors = vectors

    def encode(self, texts):
        return np.asarray([self.vectors[text] for text in texts], dtype=np.float64)


def interest(index=1, days=0):
    return InterestPaper(
        paper_id=f"zotero:{index}", title=f"Interest {index}", abstract=f"I{index}",
        collection_paths=("PaperDaily/00-Seeds/X",), added_at=NOW - timedelta(days=days)
    )


def candidate(index):
    arxiv_id = f"2401.{index:05d}"
    return CandidatePaper(
        paper_id=f"arxiv:{arxiv_id}", arxiv_id=arxiv_id, version=1,
        title=f"Candidate {index}", authors=("Author",), abstract=f"C{index}",
        categories=("cs.CV",), primary_category="cs.CV", published_at=NOW, updated_at=NOW,
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}", pdf_url=f"https://arxiv.org/pdf/{arxiv_id}"
    )


def text(paper):
    return f"{paper.title}\n\n{paper.abstract}"


def test_weighted_scores_match_legacy_formula():
    similarity = np.array([[1.0, 0.5], [0.2, 0.9]])
    weights = 1 / (1 + np.log10(np.arange(2) + 1))
    expected = (similarity * (weights / weights.sum())).sum(axis=1) * 10
    np.testing.assert_allclose(weighted_similarity_scores(similarity), expected)


def test_rank_ties_by_stable_paper_id():
    papers = (candidate(2), candidate(1))
    seed = interest()
    vectors = {text(seed): [1, 0], text(papers[0]): [1, 0], text(papers[1]): [1, 0]}
    result = CandidateRanker(FakeProvider(vectors)).rank(papers, (seed,))
    assert [paper.paper_id for paper in result.candidates] == ["arxiv:2401.00001", "arxiv:2401.00002"]
    assert [record.rank for record in result.rankings] == [1, 2]
    assert all(record.llm_score is None for record in result.rankings)


def test_rank_enforces_thirty_fifteen_five_limits():
    papers = tuple(candidate(index) for index in range(1, 36))
    seed = interest()
    vectors = {text(seed): [1, 0]}
    vectors.update({text(paper): [float(100 - index), 1] for index, paper in enumerate(papers)})
    result = CandidateRanker(FakeProvider(vectors)).rank(
        papers, (seed,), RankingLimits(candidate_pool_size=30, llm_rerank_limit=15, full_analysis_limit=5)
    )
    assert len(result.candidates) == 30
    assert len(result.selected_for_llm) == 15
    assert len(result.selected_for_full_analysis) == 5


def test_more_recent_interests_have_greater_weight():
    recent, old = interest(1, 0), interest(2, 100)
    paper = candidate(1)
    vectors = {text(recent): [1, 0], text(old): [0, 1], text(paper): [1, 0]}
    result = CandidateRanker(FakeProvider(vectors)).rank((paper,), (old, recent))
    assert result.rankings[0].embedding_score > 5
