from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.retriever import arxiv_retriever
from zotero_arxiv_daily.retriever.arxiv_retriever import (
    ArxivMetadataEntry,
    ArxivMetadataRetriever,
    ArxivRetryPolicy,
    HttpArxivMetadataGateway,
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


def test_metadata_entry_rejects_multi_digit_version_suffix():
    with pytest.raises(ValidationError, match="version suffix"):
        entry("2401.00001v10", version=10)


ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>https://arxiv.org/abs/2401.00001v2</id>
    <updated>2026-07-20T01:00:00Z</updated>
    <published>2026-07-20T00:00:00Z</published>
    <title> Metadata title </title>
    <summary> Metadata abstract </summary>
    <author><name>A. Author</name></author>
    <arxiv:primary_category term="cs.LG" />
    <category term="cs.LG"/><category term="cs.CV"/>
    <link href="https://arxiv.org/abs/2401.00001v2" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2401.00001v2" rel="related" type="application/pdf" title="pdf"/>
  </entry>
</feed>"""


def test_http_gateway_retries_transient_status_without_real_network():
    calls = []
    sleeps = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, request=request, content=ATOM)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = HttpArxivMetadataGateway(
        client,
        retry_policy=ArxivRetryPolicy(max_attempts=3, backoff_seconds=1),
        sleeper=sleeps.append,
    )
    assert len(gateway.retrieve_entries(("cs.CV",), include_cross_list=True)) == 1
    assert len(calls) == 2
    assert sleeps == [1]


def test_http_gateway_bounds_numeric_retry_after():
    calls = []
    sleeps = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": "999"})
        return httpx.Response(200, request=request, content=ATOM)

    gateway = HttpArxivMetadataGateway(
        httpx.Client(transport=httpx.MockTransport(handler)),
        retry_policy=ArxivRetryPolicy(max_attempts=2, backoff_seconds=1, max_retry_after_seconds=60),
        sleeper=sleeps.append,
    )
    gateway.retrieve_entries(("cs.CV",), include_cross_list=True)
    assert sleeps == [60]


def test_http_gateway_supports_http_date_retry_after():
    calls = []
    sleeps = []
    retry_at = NOW + timedelta(seconds=12)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": format_datetime(retry_at, usegmt=True)})
        return httpx.Response(200, request=request, content=ATOM)

    gateway = HttpArxivMetadataGateway(
        httpx.Client(transport=httpx.MockTransport(handler)),
        retry_policy=ArxivRetryPolicy(max_attempts=2, backoff_seconds=1, max_retry_after_seconds=60),
        sleeper=sleeps.append,
        clock=lambda: NOW,
    )
    gateway.retrieve_entries(("cs.CV",), include_cross_list=True)
    assert sleeps == [12]


def test_http_gateway_does_not_retry_permanent_error():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, request=request)

    gateway = HttpArxivMetadataGateway(
        httpx.Client(transport=httpx.MockTransport(handler)), sleeper=lambda _: None
    )
    with pytest.raises(httpx.HTTPStatusError):
        gateway.retrieve_entries(("cs.CV",), include_cross_list=False)
    assert len(calls) == 1


def test_http_gateway_honors_cross_list_policy():
    def handler(request):
        return httpx.Response(200, request=request, content=ATOM)

    gateway = HttpArxivMetadataGateway(
        httpx.Client(transport=httpx.MockTransport(handler)), sleeper=lambda _: None
    )
    assert gateway.retrieve_entries(("cs.CV",), include_cross_list=False) == ()
    assert len(gateway.retrieve_entries(("cs.CV",), include_cross_list=True)) == 1


def test_http_gateway_isolates_malformed_entry_and_counts_it():
    malformed = b"""<entry><id>not-an-arxiv-id</id><title>Private bad record</title></entry>"""
    content = ATOM.replace(b"</feed>", malformed + b"</feed>")

    def handler(request):
        return httpx.Response(200, request=request, content=content)

    gateway = HttpArxivMetadataGateway(
        httpx.Client(transport=httpx.MockTransport(handler)), sleeper=lambda _: None
    )
    result = ArxivMetadataRetriever(gateway, categories=("cs.CV",), include_cross_list=True).retrieve()
    assert len(result.candidates) == 1
    assert result.retrieved_count == 2
    assert result.invalid_count == 1


def test_http_gateway_default_client_has_explicit_timeouts(monkeypatch):
    captured = {}
    real_client = httpx.Client

    def constructor(*args, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request, content=ATOM)), **kwargs)

    monkeypatch.setattr(arxiv_retriever.httpx, "Client", constructor)
    gateway = HttpArxivMetadataGateway.from_defaults()
    try:
        timeout = captured["timeout"]
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (10, 30, 10, 10)
    finally:
        gateway.close()
