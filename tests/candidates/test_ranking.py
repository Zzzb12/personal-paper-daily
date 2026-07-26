from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

import zotero_arxiv_daily.candidates.ranking as ranking_module

from zotero_arxiv_daily.analysis.schemas import CandidatePaper, InterestPaper
from zotero_arxiv_daily.candidates.ranking import (
    CandidateRanker,
    EmbeddingIdentity,
    RankingLimits,
)
from zotero_arxiv_daily.viewer.feedback import InterestFeedbackProjection
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


class RecordingProvider(FakeProvider):
    def __init__(self, vectors):
        super().__init__(vectors)
        self.calls = []

    def encode(self, texts):
        self.calls.append(tuple(texts))
        return super().encode(texts)


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


def test_empty_projection_and_read_have_no_ranking_effect():
    papers = (candidate(2), candidate(1))
    seed = interest()
    vectors = {text(seed): [1, 0], text(papers[0]): [1, 0], text(papers[1]): [1, 0]}
    ranker = CandidateRanker(FakeProvider(vectors))
    baseline = ranker.rank(papers, (seed,))
    projected = ranker.rank(
        papers, (seed,),
        feedback=InterestFeedbackProjection(read_ids=("2401.00002",)),
    )

    assert projected == baseline


def test_favorite_bonus_is_bounded_clamped_and_ties_by_paper_id():
    papers = (candidate(2), candidate(1))
    seed = interest()
    vectors = {text(seed): [1, 0], text(papers[0]): [1, 0], text(papers[1]): [1, 0]}
    result = CandidateRanker(FakeProvider(vectors)).rank(
        papers,
        (seed,),
        feedback=InterestFeedbackProjection(
            favorite_ids=("2401.00002v7",), favorite_delta=0.10
        ),
    )

    assert [paper.paper_id for paper in result.candidates] == ["arxiv:2401.00001", "arxiv:2401.00002"]
    assert [record.final_score for record in result.rankings] == [10.0, 10.0]
    assert [record.embedding_score for record in result.rankings] == [10.0, 10.0]
    assert [record.feedback_adjustment for record in result.rankings] == [0.0, 0.0]


def test_favorite_adds_exact_default_delta_before_sorting():
    normal, favorite = candidate(1), candidate(2)
    seed = interest()
    vectors = {
        text(seed): [1, 0],
        text(normal): [0.60, 0.80],
        text(favorite): [0.599, 0.800749],
    }
    result = CandidateRanker(FakeProvider(vectors)).rank(
        (normal, favorite),
        (seed,),
        feedback=InterestFeedbackProjection(favorite_ids=("2401.00002",)),
    )

    assert result.candidates[0].paper_id == favorite.paper_id
    assert result.rankings[0].embedding_score == pytest.approx(5.99, abs=0.02)
    assert result.rankings[0].feedback_adjustment == pytest.approx(0.05)
    assert result.rankings[0].final_score == pytest.approx(6.04, abs=0.02)
    assert result.rankings[0].reason == "embedding similarity to the Zotero interest corpus; explicit feedback adjustment applied"


def test_floating_point_similarity_above_upper_bound_is_clamped_before_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = candidate(1)
    seed = interest()
    vectors = {text(seed): [1, 0], text(paper): [1, 0]}
    monkeypatch.setattr(
        ranking_module,
        "weighted_similarity_scores",
        lambda similarity: np.asarray([10.000000000000002]),
    )

    result = CandidateRanker(FakeProvider(vectors)).rank((paper,), (seed,))

    assert result.rankings[0].embedding_score == 10.0
    assert result.rankings[0].feedback_adjustment == 0.0
    assert result.rankings[0].final_score == 10.0


def test_irrelevant_version_match_is_vetoed_before_any_embedding_call():
    blocked, allowed = candidate(1), candidate(2)
    seed = interest()
    vectors = {text(seed): [1, 0], text(blocked): [1, 0], text(allowed): [1, 0]}
    provider = RecordingProvider(vectors)
    result = CandidateRanker(provider).rank(
        (blocked, allowed),
        (seed,),
        feedback=InterestFeedbackProjection(irrelevant_ids=("2401.00001v9",)),
    )

    assert [paper.paper_id for paper in result.candidates] == [allowed.paper_id]
    assert provider.calls == [(text(allowed),), (text(seed),)]


def test_all_irrelevant_candidates_make_zero_provider_calls():
    blocked = candidate(1)
    seed = interest()
    provider = RecordingProvider({text(seed): [1, 0], text(blocked): [1, 0]})

    result = CandidateRanker(provider).rank(
        (blocked,), (seed,),
        feedback=InterestFeedbackProjection(irrelevant_ids=("2401.00001",)),
    )

    assert result.candidates == ()
    assert provider.calls == []


def test_invalid_feedback_projection_is_rejected_before_provider_calls():
    paper = candidate(1)
    seed = interest()
    provider = RecordingProvider({text(seed): [1, 0], text(paper): [1, 0]})

    with np.testing.assert_raises_regex(ValueError, "feedback projection rejected"):
        CandidateRanker(provider).rank((paper,), (seed,), feedback=object())

    assert provider.calls == []
