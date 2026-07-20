import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.analysis.document_schemas import (
    BoundingBox,
    DocumentBlock,
    DocumentGraph,
    DocumentIssue,
    DocumentPage,
    PaperDocumentResult,
    PdfArtifact,
    SectionNode,
    SourceMapping,
    VisualArtifact,
    VisualRegion,
)


@pytest.mark.parametrize(
    "values",
    [
        (1.0, 0.0, 1.0, 2.0),
        (2.0, 0.0, 1.0, 2.0),
        (0.0, 2.0, 1.0, 1.0),
        (0.0, 0.0, math.inf, 1.0),
    ],
)
def test_bounding_box_rejects_empty_reversed_or_non_finite_coordinates(values):
    with pytest.raises(ValidationError):
        BoundingBox(left=values[0], top=values[1], right=values[2], bottom=values[3])


def test_bounding_box_accepts_a_positive_finite_rectangle():
    box = BoundingBox(left=1.0, top=2.0, right=3.0, bottom=4.0)
    assert box.model_dump() == {
        "left": 1.0,
        "top": 2.0,
        "right": 3.0,
        "bottom": 4.0,
        "coordinate_origin": "top_left",
    }


def mapping(
    source_id: str,
    page: int,
    box: BoundingBox,
    parser_version: str = "2.0",
) -> SourceMapping:
    return SourceMapping(
        parser="docling",
        parser_version=parser_version,
        source_item_id=source_id,
        pdf_page=page,
        bbox=box,
        mapping_method="provenance",
        confidence=0.95,
    )


def artifact() -> PdfArtifact:
    return PdfArtifact(
        source_url="https://arxiv.org/pdf/2401.00001",
        local_path=Path("cache/documents/pdf/aa/paper.pdf"),
        sha256="a" * 64,
        byte_size=128,
        content_type="application/pdf",
        page_count=2,
        downloaded_at=datetime(2026, 7, 20, tzinfo=UTC),
        cache_hit=False,
    )


def graph(parser_version: str = "2.0") -> DocumentGraph:
    page_one_box = BoundingBox(left=10, top=10, right=90, bottom=30)
    page_two_box = BoundingBox(left=10, top=20, right=90, bottom=60)
    blocks = (
        DocumentBlock(
            block_id="heading-1",
            block_type="heading",
            text="1 Method",
            pdf_page=1,
            bbox=page_one_box,
            reading_order=0,
            section_id="section-1",
            source_mapping=mapping("docling-heading", 1, page_one_box, parser_version),
        ),
        DocumentBlock(
            block_id="caption-1",
            block_type="caption",
            text="Table 1: Results continued on the next page.",
            pdf_page=2,
            bbox=page_two_box,
            reading_order=1,
            section_id="section-1",
            source_mapping=mapping("docling-caption", 2, page_two_box, parser_version),
        ),
    )
    issue = DocumentIssue(
        code="cross_page_caption",
        severity="warning",
        message="caption provenance spans a page boundary",
        pdf_page=2,
    )
    visual = VisualArtifact(
        visual_id="table-1",
        kind="table",
        label="Table 1",
        caption="Table 1: Results continued on the next page.",
        caption_block_ids=("caption-1",),
        section_id="section-1",
        regions=(
            VisualRegion(
                pdf_page=1,
                bbox=BoundingBox(left=10, top=40, right=90, bottom=90),
                image_path=Path("cache/documents/images/table-1-page-1.png"),
                source_mapping=mapping(
                    "docling-table",
                    1,
                    BoundingBox(left=10, top=40, right=90, bottom=90),
                    parser_version,
                ),
                confidence=0.9,
            ),
            VisualRegion(
                pdf_page=2,
                bbox=BoundingBox(left=10, top=70, right=90, bottom=110),
                image_path=Path("cache/documents/images/table-1-page-2.png"),
                source_mapping=mapping(
                    "docling-table",
                    2,
                    BoundingBox(left=10, top=70, right=90, bottom=110),
                    parser_version,
                ),
                confidence=0.85,
            ),
        ),
        confidence=0.87,
        issues=(issue,),
    )
    return DocumentGraph(
        schema_version="1.0",
        parser="docling",
        parser_version=parser_version,
        mapper_version="1",
        config_version="1",
        content_fingerprint="b" * 64,
        evidence_root=Path("cache/documents/images"),
        pdf=artifact(),
        pages=(
            DocumentPage(
                pdf_page=1,
                width=100,
                height=120,
                text_layer_status="present",
                block_ids=("heading-1",),
            ),
            DocumentPage(
                pdf_page=2,
                width=100,
                height=120,
                text_layer_status="present",
                block_ids=("caption-1",),
            ),
        ),
        blocks=blocks,
        sections=(
            SectionNode(
                section_id="section-1",
                title="1 Method",
                level=1,
                parent_id=None,
                block_ids=("heading-1", "caption-1"),
                start_pdf_page=1,
                end_pdf_page=2,
                source_mapping=mapping(
                    "docling-heading", 1, page_one_box, parser_version
                ),
                confidence=0.95,
            ),
        ),
        visuals=(visual,),
        issues=(issue,),
    )


def test_document_graph_preserves_cross_page_visual_regions_and_round_trips_json():
    document = graph()
    assert [region.pdf_page for region in document.visuals[0].regions] == [1, 2]
    assert document.visuals[0].caption_block_ids == ("caption-1",)
    assert DocumentGraph.model_validate_json(document.model_dump_json()) == document


def test_visual_allows_unknown_label_and_caption_only_with_structured_issues():
    box = BoundingBox(left=1, top=1, right=2, bottom=2)
    visual = VisualArtifact(
        visual_id="figure-unknown",
        kind="figure",
        label=None,
        caption=None,
        caption_block_ids=(),
        section_id=None,
        regions=(
            VisualRegion(
                pdf_page=1,
                bbox=box,
                image_path=None,
                source_mapping=mapping("unknown", 1, box),
                confidence=0.2,
            ),
        ),
        confidence=0.2,
        issues=(
            DocumentIssue(
                code="caption_not_found",
                severity="warning",
                message="parser did not provide a caption",
                pdf_page=1,
            ),
        ),
    )
    assert visual.label is None
    assert visual.caption is None


def test_document_graph_rejects_a_dangling_caption_block_reference():
    payload = graph().model_dump()
    payload["visuals"][0]["caption_block_ids"] = ("missing",)
    with pytest.raises(ValidationError, match="caption block"):
        DocumentGraph.model_validate(payload)


def test_document_graph_rejects_a_region_outside_its_page():
    payload = graph().model_dump()
    payload["visuals"][0]["regions"][0]["bbox"]["right"] = 101
    with pytest.raises(ValidationError, match="page bounds"):
        DocumentGraph.model_validate(payload)


def test_source_mapping_requires_one_based_page_and_bounded_confidence():
    box = BoundingBox(left=1, top=1, right=2, bottom=2)
    with pytest.raises(ValidationError):
        mapping("bad-page", 0, box)
    with pytest.raises(ValidationError):
        SourceMapping(
            parser="docling",
            parser_version="2.0",
            source_item_id="bad-confidence",
            pdf_page=1,
            bbox=box,
            mapping_method="provenance",
            confidence=1.01,
        )


def test_document_graph_rejects_source_mapping_that_disagrees_with_its_entity():
    payload = graph().model_dump()
    payload["blocks"][0]["source_mapping"]["pdf_page"] = 2
    with pytest.raises(ValidationError, match="block source mapping"):
        DocumentGraph.model_validate(payload)

    payload = graph().model_dump()
    payload["visuals"][0]["regions"][0]["source_mapping"]["bbox"]["left"] = 11
    with pytest.raises(ValidationError, match="visual source mapping"):
        DocumentGraph.model_validate(payload)


def test_document_graph_requires_each_block_on_exactly_its_own_page():
    payload = graph().model_dump()
    payload["pages"][0]["block_ids"] = ()
    with pytest.raises(ValidationError, match="exactly once on its PDF page"):
        DocumentGraph.model_validate(payload)


def test_document_graph_rejects_duplicate_visuals_cycles_and_non_caption_evidence():
    payload = graph().model_dump()
    payload["visuals"] = (payload["visuals"][0], payload["visuals"][0])
    with pytest.raises(ValidationError, match="visual IDs"):
        DocumentGraph.model_validate(payload)

    payload = graph().model_dump()
    payload["sections"][0]["parent_id"] = payload["sections"][0]["section_id"]
    with pytest.raises(ValidationError, match="section parent cycle"):
        DocumentGraph.model_validate(payload)

    payload = graph().model_dump()
    payload["visuals"][0]["caption_block_ids"] = ("heading-1",)
    with pytest.raises(ValidationError, match="caption block reference"):
        DocumentGraph.model_validate(payload)


def test_document_graph_rejects_evidence_images_outside_declared_root():
    payload = graph().model_dump()
    payload["visuals"][0]["regions"][0]["image_path"] = Path("../escape.png").resolve()
    with pytest.raises(ValidationError, match="evidence root"):
        DocumentGraph.model_validate(payload)


def test_success_result_rejects_error_severity_issues():
    with pytest.raises(ValidationError, match="success result cannot contain error"):
        PaperDocumentResult(
            paper_id="arxiv:2401.00001",
            status="success",
            document=graph(),
            issues=(
                DocumentIssue(code="broken", severity="error", message="blocking issue"),
            ),
            processing_seconds=0,
        )


def test_document_graph_enforces_section_page_source_and_bidirectional_membership():
    payload = graph().model_dump()
    payload["sections"][0]["end_pdf_page"] = 999
    with pytest.raises(ValidationError, match="section page range"):
        DocumentGraph.model_validate(payload)

    payload = graph().model_dump()
    payload["sections"][0]["source_mapping"]["pdf_page"] = 999
    with pytest.raises(ValidationError, match="section source mapping"):
        DocumentGraph.model_validate(payload)

    payload = graph().model_dump()
    payload["sections"][0]["block_ids"] = ()
    with pytest.raises(ValidationError, match="section membership"):
        DocumentGraph.model_validate(payload)

    payload = graph().model_dump()
    payload["blocks"][0]["source_mapping"]["parser_version"] = "wrong"
    with pytest.raises(ValidationError, match="parser identity"):
        DocumentGraph.model_validate(payload)


def test_document_graph_rejects_untraceable_non_empty_caption():
    payload = graph().model_dump()
    payload["visuals"][0]["caption_block_ids"] = ()
    with pytest.raises(ValidationError, match="non-empty caption"):
        DocumentGraph.model_validate(payload)
