from datetime import UTC, datetime
from pathlib import Path

from zotero_arxiv_daily.analysis.document_schemas import PdfArtifact
from zotero_arxiv_daily.documents.inspector import InspectedPage, PdfInspection
from zotero_arxiv_daily.documents.mapper import map_document
from zotero_arxiv_daily.documents.parser import (
    ParsedBoundingBox,
    ParsedDocument,
    ParsedItem,
    ParsedProvenance,
)


def provenance(page, left, top, right, bottom):
    return ParsedProvenance(
        pdf_page=page,
        bbox=ParsedBoundingBox(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            coordinate_origin="bottom_left",
        ),
    )


def item(item_id, label, text, order, sources, *, level=0, parent=None, captions=()):
    return ParsedItem(
        item_id=item_id,
        label=label,
        text=text,
        parent_ref=parent,
        caption_refs=captions,
        level=level,
        reading_order=order,
        provenance=tuple(sources),
    )


def inspection():
    return PdfInspection(
        status="ready",
        page_count=2,
        pages=(
            InspectedPage(
                pdf_page=1,
                width=600,
                height=800,
                text_char_count=100,
                image_count=1,
                text_layer_status="present",
            ),
            InspectedPage(
                pdf_page=2,
                width=600,
                height=800,
                text_char_count=100,
                image_count=0,
                text_layer_status="present",
            ),
        ),
        issues=(),
    )


def pdf():
    return PdfArtifact(
        source_url="https://arxiv.org/pdf/2401.00001",
        local_path=Path("cache/documents/pdf/paper.pdf"),
        sha256="a" * 64,
        byte_size=100,
        content_type="application/pdf",
        page_count=2,
        downloaded_at=datetime(2026, 7, 20, tzinfo=UTC),
        cache_hit=False,
    )


def parsed_with_cross_page_table():
    return ParsedDocument(
        parser_version="2.113.0",
        items=(
            item("heading", "section_header", "2 Method", 0, [provenance(1, 50, 760, 550, 730)]),
            item(
                "body",
                "text",
                "We split tokens into clusters.",
                1,
                [provenance(1, 50, 710, 280, 670)],
                parent="heading",
            ),
            item(
                "caption",
                "caption",
                "Table 1. Ablation settings continued.",
                2,
                [provenance(2, 50, 760, 550, 720)],
                parent="heading",
            ),
            item(
                "table",
                "table",
                "",
                3,
                [provenance(1, 50, 640, 550, 200), provenance(2, 50, 700, 550, 300)],
                parent="heading",
                captions=("caption",),
            ),
        ),
    )


def test_mapper_preserves_blocks_sections_coordinates_and_source_mapping():
    graph = map_document(parsed_with_cross_page_table(), pdf(), inspection())
    body = next(block for block in graph.blocks if block.text.startswith("We split"))
    assert body.pdf_page == 1
    assert body.bbox.model_dump() == {
        "left": 50.0,
        "top": 90.0,
        "right": 280.0,
        "bottom": 130.0,
        "coordinate_origin": "top_left",
    }
    assert body.source_mapping.source_item_id == "body"
    assert body.section_id == graph.sections[0].section_id
    assert graph.sections[0].title == "2 Method"


def test_mapper_builds_one_cross_page_visual_with_caption_evidence():
    graph = map_document(parsed_with_cross_page_table(), pdf(), inspection())
    table = graph.visuals[0]
    assert table.kind == "table"
    assert table.label == "Table 1"
    assert table.caption == "Table 1. Ablation settings continued."
    assert len(table.caption_block_ids) == 1
    assert [region.pdf_page for region in table.regions] == [1, 2]
    assert all(region.image_path is None for region in table.regions)
    assert "cross_page_caption" in {issue.code for issue in table.issues}


def test_mapper_returns_null_caption_and_issue_instead_of_inventing_one():
    parsed = ParsedDocument(
        parser_version="2.113.0",
        items=(
            item("heading", "section_header", "Results", 0, [provenance(1, 50, 760, 550, 730)]),
            item("picture", "picture", "", 1, [provenance(1, 50, 650, 550, 250)]),
        ),
    )
    graph = map_document(parsed, pdf(), inspection())
    figure = graph.visuals[0]
    assert figure.label is None
    assert figure.caption is None
    assert figure.caption_block_ids == ()
    assert "caption_not_found" in {issue.code for issue in figure.issues}


def test_mapper_records_missing_visual_bbox_without_fabricating_coordinates():
    parsed = ParsedDocument(
        parser_version="2.113.0",
        items=(item("table", "table", "", 0, [ParsedProvenance(pdf_page=1, bbox=None)]),),
    )
    graph = map_document(parsed, pdf(), inspection())
    assert graph.visuals == ()
    assert "visual_bbox_not_found" in {issue.code for issue in graph.issues}


def test_mapper_marks_overlapping_rows_in_separate_columns_as_uncertain():
    parsed = ParsedDocument(
        parser_version="2.113.0",
        items=(
            item("left", "text", "left column", 0, [provenance(1, 40, 700, 260, 650)]),
            item("right", "text", "right column", 1, [provenance(1, 330, 700, 560, 650)]),
        ),
    )
    graph = map_document(parsed, pdf(), inspection())
    assert [block.text for block in graph.blocks] == ["left column", "right column"]
    assert "two_column_order_uncertain" in {issue.code for issue in graph.issues}


def test_mapper_does_not_invent_a_title_for_an_empty_heading():
    parsed = ParsedDocument(
        parser_version="2.113.0",
        items=(
            item("heading", "section_header", "", 0, [provenance(1, 50, 760, 550, 730)]),
            item("body", "text", "body", 1, [provenance(1, 50, 700, 550, 650)]),
        ),
    )
    document = map_document(parsed, pdf(), inspection())
    assert document.sections == ()
    assert document.blocks[0].section_id is None
    assert "section_title_not_found" in {issue.code for issue in document.issues}


def test_mapper_does_not_emit_caption_text_without_mapped_caption_evidence():
    parsed = ParsedDocument(
        parser_version="2.113.0",
        items=(
            item("caption", "caption", "Figure 1. Unmapped caption", 0, []),
            item(
                "picture",
                "picture",
                "",
                1,
                [provenance(1, 50, 650, 550, 250)],
                captions=("caption",),
            ),
        ),
    )
    visual = map_document(parsed, pdf(), inspection()).visuals[0]
    assert visual.caption is None
    assert visual.label is None
    assert visual.caption_block_ids == ()
    assert "caption_source_not_mapped" in {issue.code for issue in visual.issues}
