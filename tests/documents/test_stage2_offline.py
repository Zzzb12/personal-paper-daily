from datetime import UTC, datetime

import httpx

from zotero_arxiv_daily.documents.cache import DocumentGraphCache
from zotero_arxiv_daily.documents.downloader import PdfDownloadPolicy, SafePdfDownloader
from zotero_arxiv_daily.documents.images import extract_evidence_images
from zotero_arxiv_daily.documents.inspector import inspect_pdf
from zotero_arxiv_daily.documents.mapper import map_document
from zotero_arxiv_daily.documents.parser import ParsedDocumentResult
from zotero_arxiv_daily.pipeline.documents import (
    DocumentPipelineDependencies,
    DocumentPipelineSettings,
    build_document_batch,
)
from tests.documents.test_mapper import parsed_with_cross_page_table
from tests.documents.test_selection import batch
from tests.fixtures.pdf_factory import text_pdf


def test_stage_two_offline_path_downloads_only_selected_and_emits_grounded_graph(tmp_path):
    source_pdf = text_pdf(tmp_path / "source.pdf", pages=2).read_bytes()
    requests = []

    def handler(request):
        requests.append(request.url)
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=source_pdf,
        )

    downloader = SafePdfDownloader(
        tmp_path / "runtime" / "downloads",
        policy=PdfDownloadPolicy(max_attempts=1, max_bytes=1024 * 1024),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    class OfflineParser:
        parser_version = "2.113.0"

        def parse(self, path, **kwargs):
            return ParsedDocumentResult(
                status="success", document=parsed_with_cross_page_table()
            )

    dependencies = DocumentPipelineDependencies(
        downloader=downloader,
        inspector=inspect_pdf,
        parser=OfflineParser(),
        mapper=map_document,
        image_extractor=extract_evidence_images,
        cache=DocumentGraphCache(tmp_path / "runtime" / "cache"),
        clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
    )
    result = build_document_batch(
        batch(count=2, selected=1),
        DocumentPipelineSettings(
            cache_root=tmp_path / "runtime",
            docling_artifacts_path=tmp_path / "unused-models",
        ),
        dependencies,
    )

    assert len(requests) == 1
    assert len(result.results) == 1
    paper = result.results[0]
    assert paper.paper_id == "arxiv:2401.00001"
    assert paper.status == "partial"
    assert paper.document is not None
    assert paper.document.blocks
    visual = paper.document.visuals[0]
    assert visual.label == "Table 1"
    assert visual.caption_block_ids
    assert [region.pdf_page for region in visual.regions] == [1, 2]
    assert all(region.image_path is not None and region.image_path.is_file() for region in visual.regions)
    assert all(region.source_mapping.bbox is not None for region in visual.regions)
    assert not tuple(tmp_path.rglob("*.tmp"))
