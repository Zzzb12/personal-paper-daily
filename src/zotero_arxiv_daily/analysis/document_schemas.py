from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel


DOCUMENT_SCHEMA_VERSION = "1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _non_empty(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


class BoundingBox(StrictModel):
    left: float
    top: float
    right: float
    bottom: float
    coordinate_origin: Literal["top_left"] = "top_left"

    @model_validator(mode="after")
    def validate_rectangle(self) -> Self:
        coordinates = (self.left, self.top, self.right, self.bottom)
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("bounding box coordinates must be finite")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("bounding box must have positive area")
        return self

    def within(self, *, width: float, height: float) -> bool:
        return self.left >= 0 and self.top >= 0 and self.right <= width and self.bottom <= height


class DocumentIssue(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    pdf_page: int | None = Field(default=None, ge=1)
    source_item_id: str | None = None

    @field_validator("code", "message")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)


class SourceMapping(StrictModel):
    parser: str
    parser_version: str
    source_item_id: str
    pdf_page: int = Field(ge=1)
    bbox: BoundingBox | None
    mapping_method: str
    confidence: float = Field(ge=0, le=1)

    @field_validator("parser", "parser_version", "source_item_id", "mapping_method")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)


class PdfArtifact(StrictModel):
    source_url: str
    local_path: Path
    sha256: str
    byte_size: int = Field(gt=0)
    content_type: str
    page_count: int = Field(ge=1)
    downloaded_at: datetime
    cache_hit: bool

    @field_validator("source_url", "content_type")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        return value

    @field_validator("downloaded_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("downloaded_at must be timezone-aware")
        return value.astimezone(UTC)


class DocumentBlock(StrictModel):
    block_id: str
    block_type: Literal[
        "heading", "text", "caption", "figure", "table", "formula", "list_item", "other"
    ]
    text: str
    pdf_page: int = Field(ge=1)
    bbox: BoundingBox
    reading_order: int = Field(ge=0)
    section_id: str | None
    source_mapping: SourceMapping

    @field_validator("block_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return _non_empty(value)


class DocumentPage(StrictModel):
    pdf_page: int = Field(ge=1)
    printed_page_label: str | None = None
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    text_layer_status: Literal["present", "absent", "uncertain"]
    block_ids: tuple[str, ...]


class SectionNode(StrictModel):
    section_id: str
    title: str
    level: int = Field(ge=1)
    parent_id: str | None
    block_ids: tuple[str, ...]
    start_pdf_page: int = Field(ge=1)
    end_pdf_page: int = Field(ge=1)
    source_mapping: SourceMapping
    confidence: float = Field(ge=0, le=1)

    @field_validator("section_id", "title")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @model_validator(mode="after")
    def validate_page_range(self) -> Self:
        if self.end_pdf_page < self.start_pdf_page:
            raise ValueError("section end page must not precede start page")
        return self


class VisualRegion(StrictModel):
    pdf_page: int = Field(ge=1)
    bbox: BoundingBox
    image_path: Path | None
    source_mapping: SourceMapping
    confidence: float = Field(ge=0, le=1)


class VisualArtifact(StrictModel):
    visual_id: str
    kind: Literal["figure", "table"]
    label: str | None
    caption: str | None
    caption_block_ids: tuple[str, ...]
    section_id: str | None
    regions: tuple[VisualRegion, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    issues: tuple[DocumentIssue, ...] = ()

    @field_validator("visual_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return _non_empty(value)


class DocumentGraph(StrictModel):
    schema_version: Literal["1.0"] = DOCUMENT_SCHEMA_VERSION
    parser: str
    parser_version: str
    mapper_version: str
    config_version: str
    content_fingerprint: str
    evidence_root: Path | None = None
    pdf: PdfArtifact
    pages: tuple[DocumentPage, ...]
    blocks: tuple[DocumentBlock, ...]
    sections: tuple[SectionNode, ...]
    visuals: tuple[VisualArtifact, ...]
    issues: tuple[DocumentIssue, ...] = ()

    @field_validator("parser", "parser_version", "mapper_version", "config_version")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("content_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("content_fingerprint must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        page_by_number = {page.pdf_page: page for page in self.pages}
        if len(page_by_number) != len(self.pages):
            raise ValueError("PDF page numbers must be unique")
        if set(page_by_number) != set(range(1, self.pdf.page_count + 1)):
            raise ValueError("document pages must cover every PDF page")

        block_by_id = {block.block_id: block for block in self.blocks}
        section_by_id = {section.section_id: section for section in self.sections}
        visual_ids = tuple(visual.visual_id for visual in self.visuals)
        if len(block_by_id) != len(self.blocks):
            raise ValueError("block IDs must be unique")
        if len(section_by_id) != len(self.sections):
            raise ValueError("section IDs must be unique")
        if len(set(visual_ids)) != len(visual_ids):
            raise ValueError("visual IDs must be unique")

        for page in self.pages:
            if any(block_id not in block_by_id for block_id in page.block_ids):
                raise ValueError("page contains a dangling block reference")
        page_memberships: dict[str, list[int]] = {block_id: [] for block_id in block_by_id}
        for page in self.pages:
            for block_id in page.block_ids:
                page_memberships[block_id].append(page.pdf_page)
        for block in self.blocks:
            page = page_by_number.get(block.pdf_page)
            if page is None or not block.bbox.within(width=page.width, height=page.height):
                raise ValueError("block bounding box is outside page bounds")
            if page_memberships[block.block_id] != [block.pdf_page]:
                raise ValueError("each block must occur exactly once on its PDF page")
            if (
                block.source_mapping.pdf_page != block.pdf_page
                or block.source_mapping.bbox != block.bbox
            ):
                raise ValueError("block source mapping must match its page and bounding box")
            if (
                block.source_mapping.parser != self.parser
                or block.source_mapping.parser_version != self.parser_version
            ):
                raise ValueError("source mapping parser identity must match the document graph")
            if block.section_id is not None and block.section_id not in section_by_id:
                raise ValueError("block contains a dangling section reference")
        for section in self.sections:
            if section.parent_id is not None and section.parent_id not in section_by_id:
                raise ValueError("section contains a dangling parent reference")
            if any(block_id not in block_by_id for block_id in section.block_ids):
                raise ValueError("section contains a dangling block reference")
            if section.end_pdf_page > self.pdf.page_count:
                raise ValueError("section page range must remain inside the PDF")
            source_page = page_by_number.get(section.source_mapping.pdf_page)
            if (
                source_page is None
                or section.source_mapping.bbox is None
                or not section.source_mapping.bbox.within(
                    width=source_page.width, height=source_page.height
                )
                or not (
                    section.start_pdf_page
                    <= section.source_mapping.pdf_page
                    <= section.end_pdf_page
                )
            ):
                raise ValueError("section source mapping must resolve inside its page range")
            if (
                section.source_mapping.parser != self.parser
                or section.source_mapping.parser_version != self.parser_version
            ):
                raise ValueError("source mapping parser identity must match the document graph")
            if any(block_by_id[block_id].section_id != section.section_id for block_id in section.block_ids):
                raise ValueError("section membership must agree with every referenced block")
        for block in self.blocks:
            if (
                block.section_id is not None
                and block.block_id not in section_by_id[block.section_id].block_ids
            ):
                raise ValueError("section membership must be bidirectionally complete")
        for section in self.sections:
            visited: set[str] = set()
            current: SectionNode | None = section
            while current is not None:
                if current.section_id in visited:
                    raise ValueError("section parent cycle is not allowed")
                visited.add(current.section_id)
                current = section_by_id.get(current.parent_id) if current.parent_id else None
        for visual in self.visuals:
            if visual.section_id is not None and visual.section_id not in section_by_id:
                raise ValueError("visual contains a dangling section reference")
            if any(block_id not in block_by_id for block_id in visual.caption_block_ids):
                raise ValueError("visual contains a dangling caption block reference")
            if any(block_by_id[block_id].block_type != "caption" for block_id in visual.caption_block_ids):
                raise ValueError("visual caption block reference must point to a caption block")
            if visual.caption is not None and not visual.caption_block_ids:
                raise ValueError("non-empty caption requires mapped caption block evidence")
            for region in visual.regions:
                page = page_by_number.get(region.pdf_page)
                if page is None or not region.bbox.within(width=page.width, height=page.height):
                    raise ValueError("visual region is outside page bounds")
                if (
                    region.source_mapping.pdf_page != region.pdf_page
                    or region.source_mapping.bbox != region.bbox
                ):
                    raise ValueError("visual source mapping must match its page and bounding box")
                if (
                    region.source_mapping.parser != self.parser
                    or region.source_mapping.parser_version != self.parser_version
                ):
                    raise ValueError("source mapping parser identity must match the document graph")
                if region.image_path is not None:
                    if self.evidence_root is None or not region.image_path.resolve().is_relative_to(
                        self.evidence_root.resolve()
                    ):
                        raise ValueError("visual image path must remain inside the evidence root")
        return self


class PaperDocumentResult(StrictModel):
    paper_id: str
    status: Literal["success", "partial", "failed", "skipped"]
    document: DocumentGraph | None
    issues: tuple[DocumentIssue, ...] = ()
    processing_seconds: float = Field(ge=0)
    cache_hit: bool = False

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status in {"success", "partial"} and self.document is None:
            raise ValueError("successful or partial results require a document")
        if self.status in {"failed", "skipped"} and self.document is not None:
            raise ValueError("failed or skipped results must not contain a document")
        if self.status == "success":
            all_issues = (
                *self.issues,
                *(self.document.issues if self.document else ()),
                *(
                    issue
                    for visual in (self.document.visuals if self.document else ())
                    for issue in visual.issues
                ),
            )
            if any(issue.severity == "error" for issue in all_issues):
                raise ValueError("success result cannot contain error severity issues")
        return self


class DocumentBatchResult(StrictModel):
    schema_version: Literal["1.0"] = DOCUMENT_SCHEMA_VERSION
    run_id: str
    created_at: datetime
    results: tuple[PaperDocumentResult, ...]

    @field_validator("run_id")
    @classmethod
    def normalize_run_id(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("created_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)
