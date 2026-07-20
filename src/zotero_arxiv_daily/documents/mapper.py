from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from zotero_arxiv_daily.analysis.document_schemas import (
    BoundingBox,
    DocumentBlock,
    DocumentGraph,
    DocumentIssue,
    DocumentPage,
    PdfArtifact,
    SectionNode,
    SourceMapping,
    VisualArtifact,
    VisualRegion,
)
from zotero_arxiv_daily.documents.captions import extract_visual_label
from zotero_arxiv_daily.documents.inspector import PdfInspection
from zotero_arxiv_daily.documents.parser import ParsedDocument, ParsedItem, ParsedProvenance
from zotero_arxiv_daily.documents.sections import is_heading, normalized_section_level


MAPPER_VERSION = "1"
_VISUAL_LABELS = {"table", "picture", "figure"}


@dataclass
class _SectionBuilder:
    section_id: str
    title: str
    level: int
    parent_id: str | None
    source_mapping: SourceMapping
    confidence: float = 0.9
    block_ids: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)


def map_document(
    parsed: ParsedDocument,
    pdf: PdfArtifact,
    inspection: PdfInspection,
    *,
    mapper_version: str = MAPPER_VERSION,
    config_version: str = "1",
) -> DocumentGraph:
    page_by_number = {page.pdf_page: page for page in inspection.pages}
    item_by_id = {item.item_id: item for item in parsed.items}
    blocks: list[DocumentBlock] = []
    issues: list[DocumentIssue] = list(inspection.issues)
    section_builders: list[_SectionBuilder] = []
    section_stack: list[_SectionBuilder] = []
    item_section: dict[str, str | None] = {}
    block_ids_by_source: dict[str, list[str]] = {}

    for item in sorted(parsed.items, key=lambda value: value.reading_order):
        if is_heading(item.label) and item.provenance:
            mapping = _source_mapping(parsed, item, item.provenance[0], page_by_number, 0)
            if mapping is not None and mapping.bbox is not None:
                level = normalized_section_level(item.level)
                while section_stack and section_stack[-1].level >= level:
                    section_stack.pop()
                section = _SectionBuilder(
                    section_id=_stable_id(pdf.sha256, "section", item.item_id),
                    title=item.text or "Untitled section",
                    level=level,
                    parent_id=section_stack[-1].section_id if section_stack else None,
                    source_mapping=mapping,
                )
                section_builders.append(section)
                section_stack.append(section)
        current_section = section_stack[-1] if section_stack else None
        item_section[item.item_id] = current_section.section_id if current_section else None

        if item.label.lower() in _VISUAL_LABELS:
            continue
        for source_index, source in enumerate(item.provenance):
            mapping = _source_mapping(parsed, item, source, page_by_number, source_index)
            if mapping is None or mapping.bbox is None:
                issues.append(
                    DocumentIssue(
                        code="source_mapping_invalid",
                        severity="warning",
                        message="text item has no valid page coordinates",
                        pdf_page=source.pdf_page,
                        source_item_id=item.item_id,
                    )
                )
                continue
            block_id = _stable_id(
                pdf.sha256, "block", item.item_id, str(source.pdf_page), str(source_index)
            )
            block = DocumentBlock(
                block_id=block_id,
                block_type=_block_type(item.label),
                text=item.text,
                pdf_page=source.pdf_page,
                bbox=mapping.bbox,
                reading_order=item.reading_order,
                section_id=current_section.section_id if current_section else None,
                source_mapping=mapping,
            )
            blocks.append(block)
            block_ids_by_source.setdefault(item.item_id, []).append(block_id)
            if current_section is not None:
                current_section.block_ids.append(block_id)
                current_section.pages.append(source.pdf_page)

    visuals: list[VisualArtifact] = []
    for item in sorted(parsed.items, key=lambda value: value.reading_order):
        if item.label.lower() not in _VISUAL_LABELS:
            continue
        visual_issues: list[DocumentIssue] = []
        regions: list[VisualRegion] = []
        for source_index, source in enumerate(item.provenance):
            mapping = _source_mapping(parsed, item, source, page_by_number, source_index)
            if mapping is None or mapping.bbox is None:
                visual_issues.append(
                    DocumentIssue(
                        code="visual_bbox_not_found",
                        severity="warning",
                        message="visual item has no reliable PDF coordinates",
                        pdf_page=source.pdf_page,
                        source_item_id=item.item_id,
                    )
                )
                continue
            regions.append(
                VisualRegion(
                    pdf_page=source.pdf_page,
                    bbox=mapping.bbox,
                    image_path=None,
                    source_mapping=mapping,
                    confidence=0.9,
                )
            )
        if not regions:
            issues.extend(visual_issues)
            continue

        caption_items = tuple(
            item_by_id[reference]
            for reference in item.caption_refs
            if reference in item_by_id
        )
        caption_block_ids = tuple(
            block_id
            for caption_item in caption_items
            for block_id in block_ids_by_source.get(caption_item.item_id, ())
        )
        caption_texts = tuple(value.text.strip() for value in caption_items if value.text.strip())
        caption = " ".join(caption_texts) or None
        kind = "table" if item.label.lower() == "table" else "figure"
        if caption is None:
            visual_issues.append(
                DocumentIssue(
                    code="caption_not_found",
                    severity="warning",
                    message="Docling did not provide a caption for this visual",
                    pdf_page=regions[0].pdf_page,
                    source_item_id=item.item_id,
                )
            )
        region_pages = {region.pdf_page for region in regions}
        caption_pages = {
            source.pdf_page for caption_item in caption_items for source in caption_item.provenance
        }
        if len(region_pages) > 1 or len(caption_pages) > 1 or not caption_pages.issubset(region_pages):
            visual_issues.append(
                DocumentIssue(
                    code="cross_page_caption",
                    severity="warning",
                    message="visual or caption provenance spans a page boundary",
                    pdf_page=min(region_pages),
                    source_item_id=item.item_id,
                )
            )
        visuals.append(
            VisualArtifact(
                visual_id=_stable_id(pdf.sha256, kind, item.item_id),
                kind=kind,
                label=extract_visual_label(caption, kind=kind),
                caption=caption,
                caption_block_ids=caption_block_ids,
                section_id=item_section.get(item.item_id),
                regions=tuple(regions),
                confidence=min(region.confidence for region in regions),
                issues=tuple(visual_issues),
            )
        )

    if _looks_two_column(blocks, inspection):
        issues.append(
            DocumentIssue(
                code="two_column_order_uncertain",
                severity="warning",
                message="separate columns share vertical ranges; parser reading order was preserved",
            )
        )

    sections = tuple(_finish_section(value) for value in section_builders)
    block_ids_by_page: dict[int, list[str]] = {page.pdf_page: [] for page in inspection.pages}
    for block in blocks:
        block_ids_by_page[block.pdf_page].append(block.block_id)
    pages = tuple(
        DocumentPage(
            pdf_page=page.pdf_page,
            width=page.width,
            height=page.height,
            text_layer_status=page.text_layer_status,
            block_ids=tuple(block_ids_by_page[page.pdf_page]),
        )
        for page in inspection.pages
    )
    fingerprint_payload = "|".join(
        (pdf.sha256, parsed.parser_version, mapper_version, config_version, parsed.model_dump_json())
    )
    return DocumentGraph(
        parser=parsed.parser,
        parser_version=parsed.parser_version,
        mapper_version=mapper_version,
        config_version=config_version,
        content_fingerprint=hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest(),
        pdf=pdf,
        pages=pages,
        blocks=tuple(blocks),
        sections=sections,
        visuals=tuple(visuals),
        issues=tuple(issues),
    )


def _source_mapping(
    parsed: ParsedDocument,
    item: ParsedItem,
    source: ParsedProvenance,
    pages: dict[int, object],
    source_index: int,
) -> SourceMapping | None:
    page = pages.get(source.pdf_page)
    if page is None or source.bbox is None:
        return None
    box = source.bbox
    if box.coordinate_origin == "bottom_left":
        top = page.height - box.top
        bottom = page.height - box.bottom
    else:
        top = box.top
        bottom = box.bottom
    try:
        normalized = BoundingBox(left=box.left, top=top, right=box.right, bottom=bottom)
    except ValueError:
        return None
    if not normalized.within(width=page.width, height=page.height):
        return None
    source_id = item.item_id if len(item.provenance) == 1 else f"{item.item_id}:{source_index}"
    return SourceMapping(
        parser=parsed.parser,
        parser_version=parsed.parser_version,
        source_item_id=source_id,
        pdf_page=source.pdf_page,
        bbox=normalized,
        mapping_method="docling_provenance",
        confidence=0.95,
    )


def _block_type(label: str) -> str:
    normalized = label.lower()
    if is_heading(normalized):
        return "heading"
    if normalized in {"caption", "figure_caption", "table_caption"}:
        return "caption"
    if normalized in {"formula", "list_item"}:
        return normalized
    return "text" if normalized in {"text", "paragraph"} else "other"


def _finish_section(value: _SectionBuilder) -> SectionNode:
    pages = value.pages or [value.source_mapping.pdf_page]
    return SectionNode(
        section_id=value.section_id,
        title=value.title,
        level=value.level,
        parent_id=value.parent_id,
        block_ids=tuple(value.block_ids),
        start_pdf_page=min(pages),
        end_pdf_page=max(pages),
        source_mapping=value.source_mapping,
        confidence=value.confidence,
    )


def _looks_two_column(blocks: list[DocumentBlock], inspection: PdfInspection) -> bool:
    width_by_page = {page.pdf_page: page.width for page in inspection.pages}
    for index, left in enumerate(blocks):
        for right in blocks[index + 1 :]:
            if left.pdf_page != right.pdf_page:
                continue
            page_width = width_by_page[left.pdf_page]
            vertical_overlap = min(left.bbox.bottom, right.bbox.bottom) - max(
                left.bbox.top, right.bbox.top
            )
            horizontal_gap = max(right.bbox.left - left.bbox.right, left.bbox.left - right.bbox.right)
            if (
                vertical_overlap > 0
                and horizontal_gap > page_width * 0.05
                and left.bbox.right - left.bbox.left < page_width * 0.6
                and right.bbox.right - right.bbox.left < page_width * 0.6
            ):
                return True
    return False


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
