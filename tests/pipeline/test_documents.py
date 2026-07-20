from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from omegaconf import OmegaConf

from zotero_arxiv_daily.analysis.document_schemas import DocumentIssue
from zotero_arxiv_daily.documents.downloader import DownloadedPdf, PdfDownloadError
from zotero_arxiv_daily.documents.inspector import InspectedPage, PdfInspection
from zotero_arxiv_daily.documents.parser import ParsedDocument, ParsedDocumentResult
from zotero_arxiv_daily.pipeline.documents import (
    DocumentPipelineDependencies,
    DocumentPipelineSettings,
    build_document_dependencies,
    build_document_batch,
)
from tests.analysis.test_document_schemas import graph
from tests.documents.test_selection import batch


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def ready_inspection():
    return PdfInspection(
        status="ready",
        page_count=2,
        pages=(
            InspectedPage(
                pdf_page=1,
                width=100,
                height=120,
                text_char_count=10,
                image_count=0,
                text_layer_status="present",
            ),
            InspectedPage(
                pdf_page=2,
                width=100,
                height=120,
                text_char_count=10,
                image_count=0,
                text_layer_status="present",
            ),
        ),
        issues=(),
    )


class FakeDownloader:
    def __init__(self, fail_first=False):
        self.urls = []
        self.fail_first = fail_first

    def download(self, url):
        self.urls.append(url)
        if self.fail_first and len(self.urls) == 1:
            raise PdfDownloadError("download_timeout", "synthetic timeout", source_url="safe")
        return DownloadedPdf(
            source_url=url,
            path=Path("cache/documents/pdf/paper.pdf"),
            sha256="a" * 64,
            byte_size=100,
            content_type="application/pdf",
            cache_hit=False,
        )


class FakeParser:
    parser_version = "2.113.0"

    def __init__(self, status="success"):
        self.paths = []
        self.status = status

    def parse(self, path, **kwargs):
        self.paths.append(path)
        return ParsedDocumentResult(
            status=self.status,
            document=ParsedDocument(parser_version="2.113.0", items=()),
            issues=(
                DocumentIssue(
                    code="parser_partial", severity="warning", message="partial conversion"
                ),
            ) if self.status == "partial" else (),
        )


class FakeCache:
    def __init__(self, cached=None):
        self.documents = []
        self.cached = cached
        self.reads = []

    def read(self, **kwargs):
        self.reads.append(kwargs)
        return self.cached

    def write(self, document):
        self.documents.append(document)
        return SimpleNamespace(path=Path("cache/documents/graph.json"))


def dependencies(downloader=None, inspection=None, cached=None):
    downloader = downloader or FakeDownloader()
    parser = FakeParser()
    cache = FakeCache(cached)
    clean = graph()
    clean = clean.model_copy(
        update={
            "issues": (),
            "visuals": tuple(visual.model_copy(update={"issues": ()}) for visual in clean.visuals),
        }
    )
    deps = DocumentPipelineDependencies(
        downloader=downloader,
        inspector=lambda path, max_pages: inspection or ready_inspection(),
        parser=parser,
        mapper=lambda parsed, pdf, inspected, **kwargs: clean.model_copy(update={"pdf": pdf}),
        image_extractor=lambda document, root: document,
        cache=cache,
        clock=lambda: NOW,
    )
    return deps, parser, cache


def test_pipeline_only_processes_selected_papers_in_stage_one_order(tmp_path):
    deps, parser, cache = dependencies()
    result = build_document_batch(
        batch(count=8, selected=5),
        DocumentPipelineSettings(cache_root=tmp_path, docling_artifacts_path=tmp_path / "models"),
        deps,
    )
    assert len(result.results) == 5
    assert tuple(entry.paper_id for entry in result.results) == tuple(
        f"arxiv:2401.{index:05d}" for index in range(1, 6)
    )
    assert len(deps.downloader.urls) == len(parser.paths) == len(cache.documents) == 5
    assert all(entry.status == "success" for entry in result.results)


def test_pipeline_isolates_one_download_failure_and_continues(tmp_path):
    deps, parser, cache = dependencies(downloader=FakeDownloader(fail_first=True))
    result = build_document_batch(
        batch(count=2, selected=2),
        DocumentPipelineSettings(cache_root=tmp_path, docling_artifacts_path=tmp_path / "models"),
        deps,
    )
    assert [entry.status for entry in result.results] == ["failed", "success"]
    assert result.results[0].issues[0].code == "download_timeout"
    assert len(parser.paths) == len(cache.documents) == 1


def test_pipeline_stops_scanned_document_before_docling(tmp_path):
    scanned = PdfInspection(
        status="scanned",
        page_count=1,
        pages=(
            InspectedPage(
                pdf_page=1,
                width=100,
                height=120,
                text_char_count=0,
                image_count=1,
                text_layer_status="absent",
            ),
        ),
        issues=(
            DocumentIssue(
                code="scanned_document",
                severity="error",
                message="no text layer",
            ),
        ),
    )
    deps, parser, cache = dependencies(inspection=scanned)
    result = build_document_batch(
        batch(count=1, selected=1),
        DocumentPipelineSettings(cache_root=tmp_path, docling_artifacts_path=tmp_path / "models"),
        deps,
    )
    assert result.results[0].status == "failed"
    assert result.results[0].issues[0].code == "scanned_document"
    assert not parser.paths
    assert not cache.documents


def test_pipeline_uses_exact_version_graph_cache_before_docling(tmp_path):
    cached = graph(parser_version="2.113.0")
    deps, parser, cache = dependencies(cached=cached)
    result = build_document_batch(
        batch(count=1, selected=1),
        DocumentPipelineSettings(cache_root=tmp_path, docling_artifacts_path=tmp_path / "models"),
        deps,
    )
    assert result.results[0].cache_hit is True
    assert result.results[0].status == "partial"
    assert not parser.paths
    assert not cache.documents
    assert cache.reads == [
        {
            "pdf_sha256": "a" * 64,
            "parser_version": "2.113.0",
            "mapper_version": "1",
            "config_version": "1",
        }
    ]


def test_pipeline_does_not_cache_partial_parser_result(tmp_path):
    deps, _, cache = dependencies()
    deps.parser = FakeParser(status="partial")
    result = build_document_batch(
        batch(count=1, selected=1),
        DocumentPipelineSettings(cache_root=tmp_path, docling_artifacts_path=tmp_path / "models"),
        deps,
    )
    assert result.results[0].status == "partial"
    assert "parser_partial" in {issue.code for issue in result.results[0].issues}
    assert not cache.documents


def test_stage_two_config_has_bounded_resource_defaults():
    base = OmegaConf.load(Path(__file__).parents[2] / "config" / "base.yaml")
    assert OmegaConf.to_container(base.document_pipeline, resolve=True) == {
        "cache_root": "cache/documents",
        "docling_artifacts_path": "models/docling",
        "max_download_bytes": 52428800,
        "max_pages": 100,
        "document_timeout_seconds": 300,
        "request_timeout": {"connect": 10, "read": 30, "write": 10, "pool": 10},
        "retry": {"max_attempts": 3, "backoff_seconds": 1, "max_retry_after_seconds": 60},
        "config_version": "1",
    }


def test_production_dependency_factory_wires_local_bounded_components(tmp_path):
    settings = DocumentPipelineSettings(
        cache_root=tmp_path / "cache",
        docling_artifacts_path=tmp_path / "models",
        max_download_bytes=1234,
        document_timeout_seconds=12,
    )
    deps = build_document_dependencies(settings)
    try:
        assert deps.downloader.root == tmp_path / "cache" / "downloads"
        assert deps.downloader.policy.max_bytes == 1234
        assert deps.parser.config.artifacts_path == tmp_path / "models"
        assert deps.parser.config.document_timeout_seconds == 12
        assert deps.cache.root == (tmp_path / "cache").resolve()
    finally:
        deps.close()
