from __future__ import annotations

from zotero_arxiv_daily.analysis.schemas import CandidateBatch, CandidatePaper


def selected_papers(
    batch: CandidateBatch, *, hard_limit: int = 5
) -> tuple[CandidatePaper, ...]:
    if hard_limit < 1 or hard_limit > 5:
        raise ValueError("hard limit must be between 1 and 5")
    selected_ids = batch.selected_for_full_analysis
    if len(selected_ids) > hard_limit:
        raise ValueError(f"selected papers exceed the hard limit of {hard_limit}")
    candidates = {paper.paper_id: paper for paper in batch.candidates}
    missing = tuple(paper_id for paper_id in selected_ids if paper_id not in candidates)
    if missing:
        raise ValueError(f"selected paper references a missing candidate: {missing[0]}")
    return tuple(candidates[paper_id] for paper_id in selected_ids)
