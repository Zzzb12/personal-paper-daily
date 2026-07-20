from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBatchResult,
    DocumentGraph,
    DocumentIssue,
    PaperDocumentResult,
    PdfArtifact,
)
from zotero_arxiv_daily.analysis.schemas import CandidateBatch, StrictModel
from zotero_arxiv_daily.documents.cache import DocumentGraphCache
from zotero_arxiv_daily.documents.downloader import (
    DownloadedPdf,
    PdfDownloadError,
    PdfDownloadPolicy,
    SafePdfDownloader,
)
from zotero_arxiv_daily.documents.images import extract_evidence_images
from zotero_arxiv_daily.documents.inspector import PdfInspection, inspect_pdf
from zotero_arxiv_daily.documents.mapper import MAPPER_VERSION, map_document
from zotero_arxiv_daily.documents.parser import DoclingDocumentParser, ParsedDocumentResult
from zotero_arxiv_daily.documents.selection import selected_papers


class DocumentPipelineSettings(StrictModel):
    cache_root: Path = Path("cache/documents")
    docling_artifacts_path: Path = Path("models/docling")
    max_download_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_pages: int = Field(default=100, ge=1)
    document_timeout_seconds: float = Field(default=300, gt=0)
    connect_timeout: float = Field(default=10, gt=0)
    read_timeout: float = Field(default=30, gt=0)
    write_timeout: float = Field(default=10, gt=0)
    pool_timeout: float = Field(default=10, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=5)
    backoff_seconds: float = Field(default=1, ge=0, le=60)
    max_retry_after_seconds: float = Field(default=60, ge=0, le=300)
    config_version: str = "1"

    def download_policy(self) -> PdfDownloadPolicy:
        return PdfDownloadPolicy(
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            write_timeout=self.write_timeout,
            pool_timeout=self.pool_timeout,
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            max_retry_after_seconds=self.max_retry_after_seconds,
            max_bytes=self.max_download_bytes,
        )


class Downloader(Protocol):
    def download(self, url: str) -> DownloadedPdf: ...


class Parser(Protocol):
    parser_version: str

    def parse(
        self, path: Path, *, max_pages: int, max_file_size: int
    ) -> ParsedDocumentResult: ...


class Cache(Protocol):
    def read(
        self,
        *,
        pdf_sha256: str,
        parser_version: str,
        mapper_version: str,
        config_version: str,
    ) -> DocumentGraph | None: ...

    def write(self, document: DocumentGraph) -> Any: ...


@dataclass
class DocumentPipelineDependencies:
    downloader: Downloader
    inspector: Callable[..., PdfInspection]
    parser: Parser
    mapper: Callable[..., DocumentGraph]
    image_extractor: Callable[[DocumentGraph, Path], DocumentGraph]
    cache: Cache
    clock: Callable[[], datetime]
    close_callbacks: tuple[Callable[[], None], ...] = ()

    def close(self) -> None:
        for callback in reversed(self.close_callbacks):
            callback()


def build_document_dependencies(
    settings: DocumentPipelineSettings,
) -> DocumentPipelineDependencies:
    downloader = SafePdfDownloader(
        settings.cache_root / "downloads", policy=settings.download_policy()
    )
    parser = DoclingDocumentParser(
        artifacts_path=settings.docling_artifacts_path,
        document_timeout_seconds=settings.document_timeout_seconds,
    )
    return DocumentPipelineDependencies(
        downloader=downloader,
        inspector=inspect_pdf,
        parser=parser,
        mapper=map_document,
        image_extractor=extract_evidence_images,
        cache=DocumentGraphCache(settings.cache_root),
        clock=lambda: datetime.now(UTC),
        close_callbacks=(downloader.close,),
    )


def build_document_batch(
    batch: CandidateBatch,
    settings: DocumentPipelineSettings,
    dependencies: DocumentPipelineDependencies,
) -> DocumentBatchResult:
    papers = selected_papers(batch)
    created_at = dependencies.clock()
    results = tuple(
        _process_paper(paper.paper_id, paper.pdf_url, settings, dependencies)
        for paper in papers
    )
    return DocumentBatchResult(run_id=batch.run_id, created_at=created_at, results=results)


def _process_paper(
    paper_id: str,
    pdf_url: str,
    settings: DocumentPipelineSettings,
    dependencies: DocumentPipelineDependencies,
) -> PaperDocumentResult:
    started = time.perf_counter()
    try:
        downloaded = dependencies.downloader.download(pdf_url)
        inspection = dependencies.inspector(downloaded.path, max_pages=settings.max_pages)
        if not inspection.can_parse:
            return _failed_result(paper_id, inspection.issues, started)

        artifact = _pdf_artifact(downloaded, inspection, dependencies.clock())
        cached = dependencies.cache.read(
            pdf_sha256=downloaded.sha256,
            parser_version=dependencies.parser.parser_version,
            mapper_version=MAPPER_VERSION,
            config_version=settings.config_version,
        )
        if cached is not None:
            cached = DocumentGraph.model_validate(
                cached.model_copy(update={"pdf": artifact}).model_dump()
            )
            issues = _graph_issues(cached)
            return PaperDocumentResult(
                paper_id=paper_id,
                status="partial" if issues else "success",
                document=cached,
                issues=issues,
                processing_seconds=time.perf_counter() - started,
                cache_hit=True,
            )

        parsed = dependencies.parser.parse(
            downloaded.path,
            max_pages=settings.max_pages,
            max_file_size=settings.max_download_bytes,
        )
        if parsed.status == "failed" or parsed.document is None:
            return _failed_result(paper_id, parsed.issues, started)

        graph = dependencies.mapper(
            parsed.document,
            artifact,
            inspection,
            config_version=settings.config_version,
        )
        graph = dependencies.image_extractor(graph, settings.cache_root / "evidence")
        if parsed.status == "success":
            dependencies.cache.write(graph)
        issues = _all_document_issues(parsed, graph)
        return PaperDocumentResult(
            paper_id=paper_id,
            status="partial" if issues else "success",
            document=graph,
            issues=issues,
            processing_seconds=time.perf_counter() - started,
            cache_hit=downloaded.cache_hit,
        )
    except PdfDownloadError as exc:
        return _failed_result(
            paper_id,
            (
                DocumentIssue(
                    code=exc.code,
                    severity="error",
                    message="PDF download failed within the configured safety policy",
                ),
            ),
            started,
        )
    except Exception as exc:
        return _failed_result(
            paper_id,
            (
                DocumentIssue(
                    code="document_pipeline_failed",
                    severity="error",
                    message=f"Stage 2 paper processing failed: {type(exc).__name__}",
                ),
            ),
            started,
        )


def _pdf_artifact(
    downloaded: DownloadedPdf, inspection: PdfInspection, downloaded_at: datetime
) -> PdfArtifact:
    return PdfArtifact(
        source_url=downloaded.source_url,
        local_path=downloaded.path,
        sha256=downloaded.sha256,
        byte_size=downloaded.byte_size,
        content_type=downloaded.content_type,
        page_count=inspection.page_count,
        downloaded_at=downloaded_at,
        cache_hit=downloaded.cache_hit,
    )


def _all_document_issues(
    parsed: ParsedDocumentResult, graph: DocumentGraph
) -> tuple[DocumentIssue, ...]:
    return (
        *parsed.issues,
        *_graph_issues(graph),
    )


def _graph_issues(graph: DocumentGraph) -> tuple[DocumentIssue, ...]:
    return (
        *graph.issues,
        *(issue for visual in graph.visuals for issue in visual.issues),
    )


def _failed_result(
    paper_id: str, issues: tuple[DocumentIssue, ...], started: float
) -> PaperDocumentResult:
    return PaperDocumentResult(
        paper_id=paper_id,
        status="failed",
        document=None,
        issues=issues,
        processing_seconds=time.perf_counter() - started,
    )
