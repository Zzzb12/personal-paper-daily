from __future__ import annotations

import pytest

from zotero_arxiv_daily.analysis.validator import validate_paper
from tests.analysis.stage4_factories import (
    GoldenInputs,
    golden_inputs,
    replace_analysis,
)


def issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def assert_invalid(inputs: GoldenInputs, code: str) -> None:
    result = validate_paper(*inputs)
    assert result.status == "invalid"
    assert result.validated is None
    assert result.report.publication_eligibility == "blocked"
    assert code in issue_codes(result)


def _replace_insight(inputs: GoldenInputs, **updates) -> GoldenInputs:
    analysis = inputs.analysis_result.analysis
    insight = analysis.insights[0].model_copy(update=updates)
    return replace_analysis(inputs, analysis.model_copy(update={"insights": (insight,)}))


def test_unknown_evidence_id_is_invalid() -> None:
    assert_invalid(
        _replace_insight(golden_inputs(), evidence_ids=("unknown-evidence",)),
        "unknown_evidence",
    )


def test_abstract_only_insight_is_invalid() -> None:
    inputs = golden_inputs()
    abstract_id = next(
        item.evidence_id for item in inputs.packet.candidates if item.abstract_only
    )
    assert_invalid(
        _replace_insight(inputs, evidence_ids=(abstract_id,)),
        "abstract_only_insight",
    )


def test_duplicate_claim_id_is_invalid() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    duplicate = analysis.experimental_conclusions[0].model_copy(
        update={"claim_id": analysis.insights[0].claim_id}
    )
    changed = analysis.model_copy(update={"experimental_conclusions": (duplicate,)})
    assert_invalid(replace_analysis(inputs, changed), "duplicate_claim_id")


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"kind": "result"}, "claim_kind_mismatch"),
        (
            {"source_type": "system_inference", "inferred": False},
            "claim_source_inference_mismatch",
        ),
    ],
)
def test_claim_kind_or_inference_corruption_is_invalid(updates, code) -> None:
    assert_invalid(_replace_insight(golden_inputs(), **updates), code)


def test_supporting_visual_pointing_to_text_is_invalid() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    text = next(item for item in inputs.packet.candidates if item.kind == "text")
    visual = analysis.supporting_visuals[0].model_copy(
        update={
            "evidence_id": text.evidence_id,
            "visual_id": "text-is-not-a-visual",
            "kind": "figure",
            "support_explanation": analysis.supporting_visuals[0].support_explanation.model_copy(
                update={"evidence_ids": (text.evidence_id,)}
            ),
        }
    )
    changed = analysis.model_copy(update={"supporting_visuals": (visual,)})
    assert_invalid(
        replace_analysis(inputs, changed),
        "supporting_visual_requires_figure_or_table",
    )


def test_supporting_visual_requires_existing_insight() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    visual = analysis.supporting_visuals[0].model_copy(
        update={"insight_ids": ("missing-insight",)}
    )
    assert_invalid(
        replace_analysis(
            inputs, analysis.model_copy(update={"supporting_visuals": (visual,)})
        ),
        "supporting_visual_unknown_insight",
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": "method"},
        {"text_zh": ""},
        {"evidence_ids": ()},
    ],
)
def test_supporting_visual_requires_structured_explanation(updates) -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    visual = analysis.supporting_visuals[0]
    explanation = visual.support_explanation.model_copy(update=updates)
    changed_visual = visual.model_copy(update={"support_explanation": explanation})
    assert_invalid(
        replace_analysis(
            inputs,
            analysis.model_copy(update={"supporting_visuals": (changed_visual,)}),
        ),
        "invalid_support_explanation",
    )


def test_supporting_visual_materialized_provenance_must_match_packet() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    visual = analysis.supporting_visuals[0].model_copy(update={"label": "Table 999"})
    assert_invalid(
        replace_analysis(
            inputs, analysis.model_copy(update={"supporting_visuals": (visual,)})
        ),
        "supporting_visual_provenance_mismatch",
    )


def test_ablation_requires_real_figure_or_table() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    text_id = next(item.evidence_id for item in inputs.packet.candidates if item.kind == "text")
    ablation = analysis.ablations[0].model_copy(
        update={"visual_evidence_ids": (text_id,)}
    )
    assert_invalid(
        replace_analysis(inputs, analysis.model_copy(update={"ablations": (ablation,)})),
        "ablation_requires_visual",
    )


def test_ablation_conclusion_must_bind_declared_visual() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    text_id = next(item.evidence_id for item in inputs.packet.candidates if item.kind == "text")
    ablation = analysis.ablations[0]
    changed = ablation.model_copy(
        update={
            "conclusion": ablation.conclusion.model_copy(
                update={"evidence_ids": (text_id,)}
            )
        }
    )

    assert_invalid(
        replace_analysis(inputs, analysis.model_copy(update={"ablations": (changed,)})),
        "ablation_conclusion_missing_visual",
    )


def test_duplicate_packet_evidence_id_is_safe_invalid_result() -> None:
    inputs = golden_inputs()
    duplicate = inputs.packet.candidates[0].model_copy(
        update={"evidence_id": inputs.packet.candidates[1].evidence_id}
    )
    candidates = (duplicate, *inputs.packet.candidates[1:])
    packet = inputs.packet.model_copy(update={"candidates": candidates})
    analysis = inputs.analysis_result.analysis.model_copy(
        update={"evidence_candidates": candidates}
    )

    result = validate_paper(
        inputs.candidate,
        inputs.document,
        packet,
        inputs.analysis_result.model_copy(update={"analysis": analysis}),
    )

    assert result.status == "invalid"
    assert "duplicate_evidence_id" in issue_codes(result)


def test_schema_bypassed_four_supporting_visuals_is_safe_invalid_result() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis.model_copy(
        update={"supporting_visuals": inputs.analysis_result.analysis.supporting_visuals * 4}
    )

    result = validate_paper(*replace_analysis(inputs, analysis))

    assert result.status == "invalid"
    assert "too_many_supporting_visuals" in issue_codes(result)


def test_schema_bypassed_duplicate_claim_evidence_is_safe_invalid_result() -> None:
    inputs = golden_inputs()
    evidence_id = inputs.analysis_result.analysis.insights[0].evidence_ids[0]
    changed = _replace_insight(inputs, evidence_ids=(evidence_id, evidence_id))

    result = validate_paper(*changed)

    assert result.status == "invalid"
    assert "duplicate_claim_evidence_id" in issue_codes(result)
    claim = next(item for item in result.report.claim_results if item.claim_id == "insight-1")
    assert claim.resolved_evidence_ids == (evidence_id,)
    assert "duplicate_claim_evidence_id" in claim.issue_codes


def test_ablation_parameter_name_must_resolve() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    ablation = analysis.ablations[0].model_copy(
        update={"parameter_names": ("missing parameter",)}
    )
    assert_invalid(
        replace_analysis(inputs, analysis.model_copy(update={"ablations": (ablation,)})),
        "ablation_unknown_parameter",
    )


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"ablation_ids": ("missing-ablation",)}, "parameter_unknown_ablation"),
        ({"evidence_ids": ("missing-evidence",)}, "parameter_unknown_evidence"),
        (
            {"source_type": "system_inference", "inferred": False},
            "parameter_source_inference_mismatch",
        ),
    ],
)
def test_parameter_references_and_inference_must_resolve(updates, code) -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    parameter = analysis.parameters[0].model_copy(update=updates)
    assert_invalid(
        replace_analysis(
            inputs, analysis.model_copy(update={"parameters": (parameter,)})
        ),
        code,
    )


def test_duplicate_ablation_id_is_invalid() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    assert_invalid(
        replace_analysis(
            inputs,
            analysis.model_copy(update={"ablations": (analysis.ablations[0],) * 2}),
        ),
        "duplicate_ablation_id",
    )


def test_duplicate_parameter_name_is_invalid() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis
    second = analysis.parameters[0].model_copy(
        update={"role": analysis.parameters[0].role.model_copy(update={"claim_id": "parameter-role-2"})}
    )
    assert_invalid(
        replace_analysis(
            inputs, analysis.model_copy(update={"parameters": (analysis.parameters[0], second)})
        ),
        "duplicate_parameter_name",
    )


def test_stage3_partial_never_upgrades_to_valid() -> None:
    inputs = golden_inputs()
    partial = inputs.analysis_result.model_copy(update={"status": "partial"})

    result = validate_paper(*inputs._replace(analysis_result=partial))

    assert result.status == "partial"
    assert result.validated.report.status == "partial"
    assert result.validated.report.publication_eligibility == "blocked"


def test_missing_core_insight_is_partial() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis.model_copy(
        update={"insights": (), "supporting_visuals": ()}
    )
    result = validate_paper(*replace_analysis(inputs, analysis))

    assert result.status == "partial"
    assert "core_insight_missing" in issue_codes(result)


def test_missing_optional_facts_are_valid_and_remain_missing() -> None:
    inputs = golden_inputs()
    analysis = inputs.analysis_result.analysis.model_copy(
        update={"parameters": (), "ablations": (), "limitations": ()}
    )
    result = validate_paper(*replace_analysis(inputs, analysis))

    assert result.status == "validated"
    assert result.validated.analysis.parameters == ()
    assert result.validated.analysis.ablations == ()
    assert result.validated.analysis.limitations == ()
    assert result.validated.analysis.links.code_url is None
