from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.analysis.schemas import (
    CandidateBatch,
    CandidateCounts,
    CandidatePaper,
    InterestPaper,
    RankingModelVersions,
    RankingRecord,
)


NOW = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)


def candidate(arxiv_id: str, version: int = 1) -> CandidatePaper:
    return CandidatePaper(
        paper_id=f"arxiv:{arxiv_id}",
        arxiv_id=arxiv_id,
        version=version,
        title=f"Paper {arxiv_id}",
        authors=("A. Author",),
        abstract="An abstract.",
        categories=("cs.CV",),
        primary_category="cs.CV",
        published_at=NOW,
        updated_at=NOW,
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}v{version}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}v{version}",
        code_url=None,
    )


def ranking(paper_id: str, rank: int, score: float) -> RankingRecord:
    return RankingRecord(
        paper_id=paper_id,
        embedding_score=score,
        llm_score=None,
        final_score=score,
        rank=rank,
        reason=f"embedding rank {rank}",
        model_versions=RankingModelVersions(
            provider="fake",
            model="deterministic-v1",
            task="retrieval",
            scorer="weighted-similarity-v1",
        ),
    )


def batch(size: int = 2) -> CandidateBatch:
    papers = tuple(candidate(f"2401.{index:05d}") for index in range(1, size + 1))
    rankings = tuple(ranking(paper.paper_id, index, float(size - index + 1)) for index, paper in enumerate(papers, 1))
    ids = tuple(paper.paper_id for paper in papers)
    return CandidateBatch(
        run_id="2026-07-20",
        created_at=NOW,
        retrieved_at=NOW,
        categories=("cs.AI", "cs.CV", "cs.LG"),
        config_hash="a" * 64,
        interest_corpus_fingerprint="b" * 64,
        candidates=papers,
        rankings=rankings,
        selected_for_llm=ids[:15],
        selected_for_full_analysis=ids[:5],
        counts=CandidateCounts(retrieved=size, deduplicated=size, invalid=0, excluded=0),
    )


def test_interest_paper_normalizes_paths_and_uses_utc():
    paper = InterestPaper(
        paper_id="zotero:item-1",
        title="  A title  ",
        abstract="  An abstract  ",
        collection_paths=("PaperDaily/03-Read/X", "PaperDaily/03-Read/X"),
        added_at=NOW,
    )
    assert paper.title == "A title"
    assert paper.abstract == "An abstract"
    assert paper.collection_paths == ("PaperDaily/03-Read/X",)
    assert paper.feedback_weight == 1.0


def test_models_forbid_unknown_fields():
    payload = candidate("2401.00001").model_dump(mode="json")
    payload["full_text"] = "must not be accepted"
    with pytest.raises(ValidationError, match="full_text"):
        CandidatePaper.model_validate(payload)


def test_candidate_requires_timezone_aware_datetime():
    payload = candidate("2401.00001").model_dump()
    payload["published_at"] = datetime(2026, 7, 20)
    with pytest.raises(ValidationError, match="timezone-aware"):
        CandidatePaper.model_validate(payload)


def test_candidate_rejects_mismatched_stable_id():
    payload = candidate("2401.00001").model_dump()
    payload["paper_id"] = "arxiv:2401.99999"
    with pytest.raises(ValidationError, match="paper_id"):
        CandidatePaper.model_validate(payload)


def test_candidate_batch_json_is_deterministic():
    first = batch().to_deterministic_json()
    second = CandidateBatch.model_validate_json(first).to_deterministic_json()
    assert first == second
    assert '"schema_version": "1.0"' in first
    assert '"llm_score": null' in first


def test_candidate_batch_rejects_non_prefix_selection():
    payload = batch().model_dump(mode="json")
    payload["selected_for_full_analysis"] = [payload["candidates"][1]["paper_id"]]
    with pytest.raises(ValidationError, match="ordered prefix"):
        CandidateBatch.model_validate(payload)


def test_candidate_batch_rejects_nonconsecutive_ranks():
    payload = batch().model_dump(mode="json")
    payload["rankings"][1]["rank"] = 3
    with pytest.raises(ValidationError, match="consecutive"):
        CandidateBatch.model_validate(payload)


def test_candidate_batch_rejects_duplicate_candidate_ids():
    payload = batch().model_dump(mode="json")
    payload["candidates"][1] = payload["candidates"][0]
    with pytest.raises(ValidationError, match="unique"):
        CandidateBatch.model_validate(payload)


def test_candidate_batch_enforces_thirty_paper_limit():
    with pytest.raises(ValidationError, match="capped at 30"):
        batch(31)


def test_stage_one_rejects_non_null_llm_score():
    payload = batch().model_dump(mode="json")
    payload["rankings"][0]["llm_score"] = 0.9
    with pytest.raises(ValidationError, match="llm_score"):
        CandidateBatch.model_validate(payload)
