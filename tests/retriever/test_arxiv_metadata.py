from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from zotero_arxiv_daily.retriever import arxiv_retriever
from zotero_arxiv_daily.retriever.arxiv_retriever import (
    ArxivMetadataEntry,
    ArxivMetadataRetriever,
)


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def entry(identifier="2401.00001", version=1, updated=NOW):
    return ArxivMetadataEntry(
        arxiv_id=identifier,
        version=version,
        title="Metadata paper",
        authors=("A. Author",),
        abstract="Abstract only.",
        categories=("cs.CV", "cs.LG"),
        primary_category="cs.CV",
        published_at=NOW,
        updated_at=updated,
        arxiv_url=f"https://arxiv.org/abs/{identifier}v{version}",
        pdf_url=f"https://arxiv.org/pdf/{identifier}v{version}",
    )


class FakeGateway:
    def __init__(self, entries):
        self.entries = tuple(entries)
        self.calls = []

    def retrieve_entries(self, categories, include_cross_list):
        self.calls.append((categories, include_cross_list))
        return self.entries


def test_metadata_retrieval_never_calls_full_text(monkeypatch):
    for name in ("extract_text_from_tar", "extract_text_from_html", "extract_text_from_pdf"):
        monkeypatch.setattr(arxiv_retriever, name, Mock(side_effect=AssertionError(name)))
    gateway = FakeGateway((entry(),))
    result = ArxivMetadataRetriever(
        gateway, categories=("cs.CV", "cs.LG", "cs.AI"), include_cross_list=False
    ).retrieve()
    assert len(result.candidates) == 1
    assert result.candidates[0].abstract == "Abstract only."
    assert gateway.calls == [(('cs.CV', 'cs.LG', 'cs.AI'), False)]


def test_duplicate_versions_keep_greatest_version():
    gateway = FakeGateway((entry(version=1), entry(version=3), entry(version=2)))
    result = ArxivMetadataRetriever(gateway, categories=("cs.CV",)).retrieve()
    assert [(paper.arxiv_id, paper.version) for paper in result.candidates] == [("2401.00001", 3)]
    assert result.retrieved_count == 3
    assert result.deduplicated_count == 1


def test_same_version_uses_newest_update():
    older = entry(version=2, updated=NOW)
    newer = entry(version=2, updated=NOW + timedelta(days=1)).model_copy(update={"title": "newer"})
    result = ArxivMetadataRetriever(FakeGateway((older, newer)), categories=("cs.CV",)).retrieve()
    assert result.candidates[0].title == "newer"


def test_candidates_are_sorted_by_stable_id_before_ranking():
    result = ArxivMetadataRetriever(
        FakeGateway((entry("2401.00002"), entry("2401.00001"))), categories=("cs.CV",)
    ).retrieve()
    assert [paper.paper_id for paper in result.candidates] == ["arxiv:2401.00001", "arxiv:2401.00002"]
