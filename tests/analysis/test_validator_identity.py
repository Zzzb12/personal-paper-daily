from __future__ import annotations

import pytest

from zotero_arxiv_daily.analysis.validator import validate_paper
from zotero_arxiv_daily.documents.evidence import evidence_packet_fingerprint
from tests.analysis.stage4_factories import (
    golden_inputs,
    mutate_analysis_title,
    mutate_link,
    mutate_packet_document_fingerprint,
    mutate_paper_id,
    mutate_saved_evidence_candidate,
    mutate_visual_id,
    mutate_visual_provenance,
)


def _replace_packet_and_saved_candidates(inputs, candidates):
    packet = inputs.packet.model_copy(update={"candidates": candidates})
    analysis = inputs.analysis_result.analysis.model_copy(
        update={"evidence_candidates": candidates}
    )
    analysis_result = inputs.analysis_result.model_copy(update={"analysis": analysis})
    return inputs._replace(packet=packet, analysis_result=analysis_result)


def issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_valid_golden_preserves_original_analysis_object() -> None:
    inputs = golden_inputs()

    result = validate_paper(*inputs)

    assert result.status == "validated"
    assert result.validated.report.status == "valid"
    assert result.validated.report.publication_eligibility == "eligible"
    assert result.validated.analysis is inputs.analysis_result.analysis


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (mutate_analysis_title, "candidate_title_mismatch"),
        (lambda value: mutate_link(value, "pdf_url"), "candidate_link_mismatch"),
        (lambda value: mutate_link(value, "arxiv_url"), "candidate_link_mismatch"),
        (lambda value: mutate_link(value, "code_url"), "candidate_link_mismatch"),
        (mutate_saved_evidence_candidate, "analysis_evidence_candidates_modified"),
        (mutate_packet_document_fingerprint, "packet_document_mismatch"),
        (mutate_paper_id, "paper_id_mismatch"),
    ],
)
def test_identity_or_saved_evidence_tampering_is_invalid(mutation, code) -> None:
    result = validate_paper(*mutation(golden_inputs()))

    assert result.status == "invalid"
    assert code in issue_codes(result)
    assert all("Tampered" not in issue.message for issue in result.issues)


@pytest.mark.parametrize(
    "field",
    [
        "label",
        "caption",
        "pdf_page",
        "bbox",
        "section_id",
        "section_title",
        "section_path",
        "source_mapping",
        "image_path",
        "confidence",
    ],
)
def test_visual_provenance_field_tampering_is_invalid(field: str) -> None:
    result = validate_paper(*mutate_visual_provenance(golden_inputs(), field))

    assert result.status == "invalid"
    assert "visual_provenance_mismatch" in issue_codes(result)


def test_nonexistent_figure_or_table_is_invalid() -> None:
    result = validate_paper(*mutate_visual_id(golden_inputs()))

    assert result.status == "invalid"
    assert "evidence_source_missing" in issue_codes(result)


def test_synchronized_text_tampering_is_invalid() -> None:
    inputs = golden_inputs()
    candidates = tuple(
        item.model_copy(update={"evidence_text": "Fabricated source text"})
        if item.kind == "text" and not item.abstract_only
        else item
        for item in inputs.packet.candidates
    )

    result = validate_paper(*_replace_packet_and_saved_candidates(inputs, candidates))

    assert result.status == "invalid"
    assert "text_provenance_mismatch" in issue_codes(result)


def test_text_candidate_cannot_reference_heading_block() -> None:
    inputs = golden_inputs()
    text = next(item for item in inputs.packet.candidates if item.kind == "text")
    blocks = tuple(
        block.model_copy(update={"block_type": "heading"})
        if block.block_id == text.block_ids[0]
        else block
        for block in inputs.document.blocks
    )

    result = validate_paper(*inputs._replace(document=inputs.document.model_copy(update={"blocks": blocks})))

    assert result.status == "invalid"
    assert "text_provenance_mismatch" in issue_codes(result)


def test_synchronized_evidence_id_tampering_is_invalid() -> None:
    inputs = golden_inputs()
    candidates = tuple(
        item.model_copy(update={"evidence_id": "evidence-" + "f" * 24})
        if item.kind == "text" and not item.abstract_only
        else item
        for item in inputs.packet.candidates
    )

    result = validate_paper(*_replace_packet_and_saved_candidates(inputs, candidates))

    assert result.status == "invalid"
    assert "evidence_id_mismatch" in issue_codes(result)


def test_stale_packet_fingerprint_is_invalid() -> None:
    inputs = golden_inputs()
    packet = inputs.packet.model_copy(update={"packet_fingerprint": "f" * 64})

    result = validate_paper(*inputs._replace(packet=packet))

    assert result.status == "invalid"
    assert "packet_fingerprint_mismatch" in issue_codes(result)


@pytest.mark.parametrize("title", ["1. Abstract", "A.1 Abstract", "1) Summary"])
def test_numbered_abstract_synchronized_tampering_is_invalid(title: str) -> None:
    inputs = golden_inputs()
    sections = tuple(
        section.model_copy(update={"title": title})
        if section.section_id == "section-abstract"
        else section
        for section in inputs.document.sections
    )
    candidates = tuple(
        item.model_copy(
            update={
                "section_title": title,
                "section_path": (title,),
                "abstract_only": False,
            }
        )
        if item.section_id == "section-abstract"
        else item
        for item in inputs.packet.candidates
    )
    packet = inputs.packet.model_copy(update={"candidates": candidates})
    packet = packet.model_copy(
        update={
            "packet_fingerprint": evidence_packet_fingerprint(
                paper_id=packet.paper_id,
                document_fingerprint=packet.document_fingerprint,
                builder_version=packet.builder_version,
                candidates=candidates,
            )
        }
    )
    abstract_id = next(
        item.evidence_id for item in candidates if item.section_id == "section-abstract"
    )
    analysis = inputs.analysis_result.analysis
    insight = analysis.insights[0].model_copy(update={"evidence_ids": (abstract_id,)})
    analysis = analysis.model_copy(
        update={"insights": (insight,), "evidence_candidates": candidates}
    )
    changed = inputs._replace(
        document=inputs.document.model_copy(update={"sections": sections}),
        packet=packet,
        analysis_result=inputs.analysis_result.model_copy(update={"analysis": analysis}),
    )

    result = validate_paper(*changed)

    assert result.status == "invalid"
    assert result.report.publication_eligibility == "blocked"
    assert "text_provenance_mismatch" in issue_codes(result)


@pytest.mark.parametrize(
    ("section_id", "parent_id"),
    [
        ("section-introduction", "section-introduction"),
        ("section-introduction", "section-missing"),
        ("section-experiments", "section-experiments"),
        ("section-experiments", "section-missing"),
    ],
)
def test_invalid_text_or_visual_section_hierarchy_is_rejected(
    section_id: str, parent_id: str
) -> None:
    inputs = golden_inputs()
    sections = tuple(
        section.model_copy(update={"parent_id": parent_id})
        if section.section_id == section_id
        else section
        for section in inputs.document.sections
    )

    result = validate_paper(
        inputs.candidate,
        inputs.document.model_copy(update={"sections": sections}),
        inputs.packet,
        inputs.analysis_result,
    )

    assert result.status == "invalid"
    assert "section_hierarchy_invalid" in issue_codes(result)


@pytest.mark.parametrize("unsafe_section_id", ["section/unsafe", "section unsafe"])
def test_unsafe_section_id_hierarchy_error_is_structured_invalid(
    unsafe_section_id: str,
) -> None:
    inputs = golden_inputs()
    original = inputs.document.sections[0]
    corrupt = original.model_copy(
        update={"section_id": unsafe_section_id, "parent_id": unsafe_section_id}
    )
    sections = (corrupt, *inputs.document.sections[1:])

    result = validate_paper(
        inputs.candidate,
        inputs.document.model_copy(update={"sections": sections}),
        inputs.packet,
        inputs.analysis_result,
    )

    assert result.status == "invalid"
    issue = next(item for item in result.issues if item.code == "section_hierarchy_invalid")
    assert issue.field_path == "sections.0"
