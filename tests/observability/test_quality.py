from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.observability.quality import (
    EVALUATOR_VERSION,
    ClaimLabel,
    EvidenceLabel,
    QualityEvaluation,
    RankedLabel,
    RequiredFieldLabel,
    evaluate_quality,
)


HASH = "a" * 64


def _rankings(*, imperfect: bool = False) -> tuple[RankedLabel, ...]:
    relevant = {1, 2, 3, 4, 5, 10}
    if imperfect:
        relevant = {1, 2, 3, 4, 6, 20}
    return tuple(
        RankedLabel(item_id=f"item-{index}", rank=index, relevant=index in relevant)
        for index in range(1, 31)
    )


def _evidence() -> tuple[EvidenceLabel, ...]:
    return tuple(
        EvidenceLabel(
            evidence_id=f"evidence-{index}",
            expected=True,
            accepted=True,
        )
        for index in range(1, 6)
    )


def _claims() -> tuple[ClaimLabel, ...]:
    return tuple(
        ClaimLabel(
            claim_id=f"claim-{index}",
            accepted=True,
            supported=True,
        )
        for index in range(1, 6)
    )


def _fields() -> tuple[RequiredFieldLabel, ...]:
    return tuple(
        RequiredFieldLabel(field_name=f"field-{index}", present=True)
        for index in range(1, 6)
    )


def _evaluate(**updates: object) -> QualityEvaluation:
    values: dict[str, object] = {
        "rankings": _rankings(),
        "evidence": _evidence(),
        "claims": _claims(),
        "required_fields": _fields(),
        "fixture_identity_hash": HASH,
    }
    values.update(updates)
    return evaluate_quality(**values)


def test_perfect_fixture_has_exact_integer_quality_and_passes_budget() -> None:
    result = _evaluate()

    assert result.evaluator_version == EVALUATOR_VERSION == "stage9-quality-v1"
    assert result.evaluated_count == 30
    assert result.relevant_count == 6
    assert result.retrieved_count == 15
    assert result.precision_at_5_ppm == 1_000_000
    assert result.recall_at_15_ppm == 1_000_000
    assert result.ndcg_at_5_ppm == 1_000_000
    assert result.evidence_precision_ppm == 1_000_000
    assert result.evidence_recall_ppm == 1_000_000
    assert result.unsupported_claim_rate_ppm == 0
    assert result.missing_required_field_rate_ppm == 0
    assert result.budget_passed is True


def test_imperfect_ranking_uses_deterministic_fixed_point_math() -> None:
    result = _evaluate(rankings=_rankings(imperfect=True))

    assert result.precision_at_5_ppm == 800_000
    assert result.recall_at_15_ppm == 833_333
    assert result.ndcg_at_5_ppm == 868_795
    assert result.budget_passed is False


def test_evidence_precision_recall_and_rounding_are_exact() -> None:
    labels = (
        EvidenceLabel(evidence_id="e1", expected=True, accepted=True),
        EvidenceLabel(evidence_id="e2", expected=True, accepted=False),
        EvidenceLabel(evidence_id="e3", expected=True, accepted=False),
        EvidenceLabel(evidence_id="e4", expected=False, accepted=True),
    )
    result = _evaluate(evidence=labels)

    assert result.expected_evidence_count == 3
    assert result.accepted_evidence_count == 2
    assert result.supported_evidence_count == 1
    assert result.rejected_evidence_count == 2
    assert result.evidence_precision_ppm == 500_000
    assert result.evidence_recall_ppm == 333_333
    assert result.budget_passed is False


def test_claim_and_required_field_failures_are_aggregate_only() -> None:
    result = _evaluate(
        claims=(
            ClaimLabel(claim_id="c1", accepted=True, supported=True),
            ClaimLabel(claim_id="c2", accepted=True, supported=False),
            ClaimLabel(claim_id="c3", accepted=False, supported=False),
        ),
        required_fields=(
            RequiredFieldLabel(field_name="f1", present=True),
            RequiredFieldLabel(field_name="f2", present=False),
            RequiredFieldLabel(field_name="f3", present=False),
        ),
    )

    assert result.expected_claim_count == 3
    assert result.accepted_claim_count == 2
    assert result.supported_claim_count == 1
    assert result.rejected_claim_count == 1
    assert result.unsupported_claim_rate_ppm == 500_000
    assert result.missing_required_field_rate_ppm == 666_667
    assert result.budget_passed is False


@pytest.mark.parametrize(
    "rankings",
    [
        (
            RankedLabel(item_id="same", rank=1, relevant=True),
            RankedLabel(item_id="same", rank=2, relevant=False),
        ),
        (
            RankedLabel(item_id="one", rank=1, relevant=True),
            RankedLabel(item_id="two", rank=1, relevant=False),
        ),
        (
            RankedLabel(item_id="one", rank=1, relevant=True),
            RankedLabel(item_id="two", rank=3, relevant=False),
        ),
    ],
)
def test_duplicate_ids_rank_ties_and_nonconsecutive_order_fail_closed(
    rankings: tuple[RankedLabel, ...],
) -> None:
    with pytest.raises(ValueError, match="ranking"):
        _evaluate(rankings=rankings)


def test_duplicate_evidence_claim_and_field_domains_fail_closed() -> None:
    with pytest.raises(ValueError, match="evidence"):
        _evaluate(evidence=(_evidence()[0], _evidence()[0]))
    with pytest.raises(ValueError, match="claim"):
        _evaluate(claims=(_claims()[0], _claims()[0]))
    with pytest.raises(ValueError, match="required field"):
        _evaluate(required_fields=(_fields()[0], _fields()[0]))


def test_inconsistent_claim_and_empty_denominators_fail_closed() -> None:
    with pytest.raises(ValueError, match="rejected claim"):
        _evaluate(
            claims=(ClaimLabel(claim_id="c1", accepted=False, supported=True),)
        )
    with pytest.raises(ValueError, match="ranking"):
        _evaluate(rankings=())
    with pytest.raises(ValueError, match="evidence"):
        _evaluate(evidence=())
    with pytest.raises(ValueError, match="claim"):
        _evaluate(claims=())
    with pytest.raises(ValueError, match="required field"):
        _evaluate(required_fields=())


def test_each_quality_budget_boundary_is_closed() -> None:
    base = _evaluate()
    fields = (
        "precision_at_5_ppm",
        "recall_at_15_ppm",
        "ndcg_at_5_ppm",
        "evidence_precision_ppm",
        "evidence_recall_ppm",
    )
    for field in fields:
        with pytest.raises(ValidationError, match="budget"):
            QualityEvaluation(**base.model_copy(update={field: 999_999}).model_dump())
    with pytest.raises(ValidationError, match="budget"):
        QualityEvaluation(
            **base.model_copy(
                update={"unsupported_claim_rate_ppm": 1}
            ).model_dump()
        )


def test_quality_json_contains_no_ids_text_or_free_form_metadata() -> None:
    result = _evaluate()
    payload = result.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True)

    assert "item-" not in serialized
    assert "evidence-" not in serialized
    assert "claim-" not in serialized
    assert "field-" not in serialized
    assert set(payload) == {
        "schema_version",
        "evaluator_version",
        "fixture_identity_hash",
        "evaluator_identity_hash",
        "evaluated_count",
        "relevant_count",
        "retrieved_count",
        "precision_at_5_ppm",
        "recall_at_15_ppm",
        "ndcg_at_5_ppm",
        "expected_evidence_count",
        "accepted_evidence_count",
        "supported_evidence_count",
        "rejected_evidence_count",
        "evidence_precision_ppm",
        "evidence_recall_ppm",
        "expected_claim_count",
        "accepted_claim_count",
        "supported_claim_count",
        "rejected_claim_count",
        "unsupported_claim_rate_ppm",
        "required_field_count",
        "present_required_field_count",
        "missing_required_field_rate_ppm",
        "budget_passed",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        QualityEvaluation(**payload, paper_id="private")
