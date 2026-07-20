from __future__ import annotations

from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import Field

from zotero_arxiv_daily.analysis.document_schemas import DocumentIssue
from zotero_arxiv_daily.analysis.schemas import StrictModel


class InspectedPage(StrictModel):
    pdf_page: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    text_char_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    text_layer_status: Literal["present", "absent", "uncertain"]


class PdfInspection(StrictModel):
    status: Literal[
        "ready", "scanned", "no_text", "corrupt", "encrypted", "page_limit_exceeded"
    ]
    page_count: int = Field(ge=0)
    pages: tuple[InspectedPage, ...]
    issues: tuple[DocumentIssue, ...]

    @property
    def can_parse(self) -> bool:
        return self.status == "ready"


def inspect_pdf(path: Path, *, max_pages: int) -> PdfInspection:
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    try:
        document = pymupdf.open(Path(path))
    except (pymupdf.FileDataError, RuntimeError, ValueError, OSError) as exc:
        return PdfInspection(
            status="corrupt",
            page_count=0,
            pages=(),
            issues=(
                DocumentIssue(
                    code="corrupt_pdf",
                    severity="error",
                    message=f"PyMuPDF could not open the document: {type(exc).__name__}",
                ),
            ),
        )

    try:
        page_count = document.page_count
        if document.needs_pass:
            return PdfInspection(
                status="encrypted",
                page_count=page_count,
                pages=(),
                issues=(
                    DocumentIssue(
                        code="encrypted_pdf",
                        severity="error",
                        message="PDF requires a password",
                    ),
                ),
            )
        if page_count > max_pages:
            return PdfInspection(
                status="page_limit_exceeded",
                page_count=page_count,
                pages=(),
                issues=(
                    DocumentIssue(
                        code="page_limit_exceeded",
                        severity="error",
                        message=f"PDF has {page_count} pages; configured maximum is {max_pages}",
                    ),
                ),
            )

        pages: list[InspectedPage] = []
        issues: list[DocumentIssue] = []
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            image_count = len(page.get_images(full=True))
            text_status: Literal["present", "absent", "uncertain"] = (
                "present" if text else "absent"
            )
            pages.append(
                InspectedPage(
                    pdf_page=index,
                    width=page.rect.width,
                    height=page.rect.height,
                    text_char_count=len(text),
                    image_count=image_count,
                    text_layer_status=text_status,
                )
            )
            if not text:
                issues.append(
                    DocumentIssue(
                        code="no_text_layer",
                        severity="warning",
                        message="page has no extractable text layer",
                        pdf_page=index,
                    )
                )

        if pages and all(page.text_layer_status == "absent" for page in pages):
            scanned = any(page.image_count > 0 for page in pages)
            if scanned:
                issues.append(
                    DocumentIssue(
                        code="scanned_document",
                        severity="error",
                        message="document appears to contain page images without a text layer",
                    )
                )
            return PdfInspection(
                status="scanned" if scanned else "no_text",
                page_count=page_count,
                pages=tuple(pages),
                issues=tuple(issues),
            )
        return PdfInspection(
            status="ready",
            page_count=page_count,
            pages=tuple(pages),
            issues=tuple(issues),
        )
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        return PdfInspection(
            status="corrupt",
            page_count=0,
            pages=(),
            issues=(
                DocumentIssue(
                    code="corrupt_pdf",
                    severity="error",
                    message=f"PyMuPDF could not inspect the document: {type(exc).__name__}",
                ),
            ),
        )
    finally:
        document.close()
