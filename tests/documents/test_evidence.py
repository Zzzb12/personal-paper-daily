from datetime import UTC, datetime
from pathlib import Path

from zotero_arxiv_daily.analysis.document_schemas import (
    BoundingBox,
    DocumentBlock,
    DocumentGraph,
    DocumentPage,
    PdfArtifact,
    SectionNode,
    SourceMapping,
    VisualArtifact,
    VisualRegion,
)
from zotero_arxiv_daily.documents.evidence import (
    EvidenceBuildSettings,
    build_evidence_packet,
)


PAPER_ID = "arxiv:2401.00001"


def mapping(source_id, page, box):
    return SourceMapping(
        parser="docling",
        parser_version="2.113.0",
        source_item_id=source_id,
        pdf_page=page,
        bbox=box,
        mapping_method="provenance",
        confidence=0.95,
    )


def document_graph(*, long_text=False, visual_count=4):
    section_specs = (
        ("abstract", "Abstract", 1),
        ("introduction", "1 Introduction", 1),
        ("method", "3 Method", 2),
        ("experiments", "4 Experiments", 3),
        ("ablation", "4.3 Ablation Study", 4),
    )
    blocks = []
    sections = []
    page_blocks = {page: [] for page in range(1, 5)}
    reading_order = 0
    for index, (key, title, page) in enumerate(section_specs):
        heading_box = BoundingBox(left=10, top=10 + index, right=90, bottom=20 + index)
        text_box = BoundingBox(left=10, top=25 + index, right=90, bottom=55 + index)
        heading_id = f"heading-{key}"
        text_id = f"text-{key}"
        section_id = f"section-{key}"
        text = f"Synthetic {title} evidence."
        if long_text:
            text = ("中文证据段落" * 80) + title
        blocks.extend(
            (
                DocumentBlock(
                    block_id=heading_id,
                    block_type="heading",
                    text=title,
                    pdf_page=page,
                    bbox=heading_box,
                    reading_order=reading_order,
                    section_id=section_id,
                    source_mapping=mapping(heading_id, page, heading_box),
                ),
                DocumentBlock(
                    block_id=text_id,
                    block_type="text",
                    text=text,
                    pdf_page=page,
                    bbox=text_box,
                    reading_order=reading_order + 1,
                    section_id=section_id,
                    source_mapping=mapping(text_id, page, text_box),
                ),
            )
        )
        reading_order += 2
        page_blocks[page].extend((heading_id, text_id))
        sections.append(
            SectionNode(
                section_id=section_id,
                title=title,
                level=1,
                parent_id=None,
                block_ids=(heading_id, text_id),
                start_pdf_page=page,
                end_pdf_page=page,
                source_mapping=mapping(heading_id, page, heading_box),
                confidence=0.95,
            )
        )

    visuals = []
    for index in range(visual_count):
        page = 3 if index < 3 else 4
        section_id = "section-experiments" if page == 3 else "section-ablation"
        caption_box = BoundingBox(left=10, top=60 + index, right=90, bottom=65 + index)
        caption_id = f"caption-{index}"
        caption = f"Table {index + 1}: Synthetic visual evidence."
        blocks.append(
            DocumentBlock(
                block_id=caption_id,
                block_type="caption",
                text=caption,
                pdf_page=page,
                bbox=caption_box,
                reading_order=reading_order,
                section_id=section_id,
                source_mapping=mapping(caption_id, page, caption_box),
            )
        )
        reading_order += 1
        page_blocks[page].append(caption_id)
        region_box = BoundingBox(left=10, top=70, right=90, bottom=95)
        regions = (
            VisualRegion(
                pdf_page=page,
                bbox=region_box,
                image_path=Path(f"cache/documents/evidence/table-{index + 1}.png"),
                source_mapping=mapping(f"visual-{index}-p{page}", page, region_box),
                confidence=0.9,
            ),
        )
        if index == 0:
            second_box = BoundingBox(left=10, top=96, right=90, bottom=110)
            regions += (
                VisualRegion(
                    pdf_page=4,
                    bbox=second_box,
                    image_path=Path("cache/documents/evidence/table-1-page-4.png"),
                    source_mapping=mapping("visual-0-p4", 4, second_box),
                    confidence=0.85,
                ),
            )
        visuals.append(
            VisualArtifact(
                visual_id=f"table-{index + 1}",
                kind="table",
                label=f"Table {index + 1}",
                caption=caption,
                caption_block_ids=(caption_id,),
                section_id=section_id,
                regions=regions,
                confidence=0.9,
            )
        )

    section_members = {
        section.section_id: [*section.block_ids] for section in sections
    }
    for block in blocks:
        if block.block_type == "caption":
            section_members[block.section_id].append(block.block_id)
    sections = [
        section.model_copy(
            update={
                "block_ids": tuple(section_members[section.section_id]),
                "end_pdf_page": max(
                    block.pdf_page
                    for block in blocks
                    if block.block_id in section_members[section.section_id]
                ),
            }
        )
        for section in sections
    ]

    return DocumentGraph(
        parser="docling",
        parser_version="2.113.0",
        mapper_version="1",
        config_version="1",
        content_fingerprint="b" * 64,
        evidence_root=Path("cache/documents/evidence"),
        pdf=PdfArtifact(
            source_url="https://arxiv.org/pdf/2401.00001",
            local_path=Path("cache/documents/pdf/synthetic.pdf"),
            sha256="a" * 64,
            byte_size=100,
            content_type="application/pdf",
            page_count=4,
            downloaded_at=datetime(2026, 7, 20, tzinfo=UTC),
            cache_hit=False,
        ),
        pages=tuple(
            DocumentPage(
                pdf_page=page,
                width=100,
                height=120,
                text_layer_status="present",
                block_ids=tuple(page_blocks[page]),
            )
            for page in range(1, 5)
        ),
        blocks=tuple(blocks),
        sections=tuple(sections),
        visuals=tuple(visuals),
    )


def settings(**updates):
    return EvidenceBuildSettings(
        max_candidates=48,
        max_chars=24_000,
        max_block_chars=2_000,
        max_visuals=3,
    ).model_copy(update=updates)


def test_evidence_prioritizes_preferred_sections_and_marks_abstract():
    packet = build_evidence_packet(PAPER_ID, document_graph(), settings())
    text_items = [item for item in packet.candidates if item.kind == "text"]
    assert [item.section_title for item in text_items[:4]] == [
        "1 Introduction",
        "3 Method",
        "4 Experiments",
        "4.3 Ablation Study",
    ]
    abstract = next(item for item in text_items if item.section_title == "Abstract")
    assert abstract.abstract_only is True
    assert text_items[0].abstract_only is False


def test_evidence_retains_at_most_three_visuals_with_all_regions():
    packet = build_evidence_packet(PAPER_ID, document_graph(), settings())
    visuals = [item for item in packet.candidates if item.kind != "text"]
    assert len(visuals) == 3
    assert visuals[0].visual_id == "table-1"
    assert len(visuals[0].regions) == 2
    assert visuals[0].regions[0].source_mapping.parser == "docling"
    assert visuals[0].regions[0].image_path == Path(
        "cache/documents/evidence/table-1.png"
    )


def test_evidence_budget_is_deterministic_unicode_safe_and_stable():
    constrained = settings(max_chars=256, max_block_chars=80, max_candidates=4)
    first = build_evidence_packet(
        PAPER_ID, document_graph(long_text=True), constrained
    )
    second = build_evidence_packet(
        PAPER_ID, document_graph(long_text=True), constrained
    )
    assert first == second
    assert len(first.candidates) <= 4
    assert len("".join(item.evidence_text or "" for item in first.candidates)) <= 256
    assert first.packet_fingerprint == second.packet_fingerprint
    assert all("�" not in (item.evidence_text or "") for item in first.candidates)


def test_evidence_ids_and_order_do_not_depend_on_input_tuple_order():
    document = document_graph()
    reordered = DocumentGraph.model_validate(
        document.model_copy(
            update={
                "sections": tuple(reversed(document.sections)),
                "visuals": tuple(reversed(document.visuals)),
            }
        ).model_dump()
    )
    first = build_evidence_packet(PAPER_ID, document, settings())
    second = build_evidence_packet(PAPER_ID, reordered, settings())
    assert [item.evidence_id for item in first.candidates] == [
        item.evidence_id for item in second.candidates
    ]
