from __future__ import annotations

import pytest

from zotero_arxiv_daily.analysis.validator import validate_paper
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
