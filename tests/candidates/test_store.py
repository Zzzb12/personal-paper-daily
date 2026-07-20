import hashlib
import os
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.analysis.schemas import (
    CandidateBatch,
    CandidateCounts,
    CandidatePaper,
    RankingModelVersions,
    RankingRecord,
)
from zotero_arxiv_daily.candidates.store import CandidateStore


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def candidate_batch() -> CandidateBatch:
    paper = CandidatePaper(
        paper_id="arxiv:2401.00001", arxiv_id="2401.00001", version=1,
        title="Paper", authors=("Author",), abstract="Abstract", categories=("cs.CV",),
        primary_category="cs.CV", published_at=NOW, updated_at=NOW,
        arxiv_url="https://arxiv.org/abs/2401.00001",
        pdf_url="https://arxiv.org/pdf/2401.00001",
    )
    ranking = RankingRecord(
        paper_id=paper.paper_id, embedding_score=8.0, final_score=8.0, rank=1,
        reason="similarity", model_versions=RankingModelVersions(
            provider="fake", model="v1", task="retrieval", scorer="weighted-cosine-v1"
        ),
    )
    return CandidateBatch(
        run_id="20260720T000000Z", created_at=NOW, retrieved_at=NOW,
        categories=("cs.CV", "cs.LG", "cs.AI"), config_hash="a" * 64,
        interest_corpus_fingerprint="b" * 64, candidates=(paper,), rankings=(ranking,),
        selected_for_llm=(paper.paper_id,), selected_for_full_analysis=(paper.paper_id,),
        counts=CandidateCounts(retrieved=1, deduplicated=1, invalid=0, excluded=0),
    )


def test_write_round_trips_validated_batch(tmp_path):
    batch = candidate_batch()
    stored = CandidateStore(tmp_path).write(batch)
    assert stored.sha256 == hashlib.sha256(stored.path.read_bytes()).hexdigest()
    assert CandidateStore(tmp_path).read(batch.run_id) == batch


def test_failed_replace_preserves_previous_file(tmp_path, monkeypatch):
    batch = candidate_batch()
    store = CandidateStore(tmp_path)
    original = store.write(batch).path.read_bytes()
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        store.write(batch.model_copy(update={"created_at": NOW.replace(hour=1)}))
    assert store.path_for(batch.run_id).read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_read_rejects_corrupt_json(tmp_path):
    store = CandidateStore(tmp_path)
    store.path_for("broken").parent.mkdir(parents=True, exist_ok=True)
    store.path_for("broken").write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError):
        store.read("broken")


@pytest.mark.parametrize("run_id", ["../escape", "folder/run", r"folder\run", ".", ".."])
def test_path_for_rejects_unsafe_run_id(tmp_path, run_id):
    with pytest.raises(ValueError, match="run_id"):
        CandidateStore(tmp_path).path_for(run_id)
