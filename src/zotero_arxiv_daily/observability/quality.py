from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel


QUALITY_SCHEMA_VERSION = "1.0"
EVALUATOR_VERSION = "stage9-quality-v1"
_PPM = 1_000_000
_NDCG_DISCOUNTS_PPM = (1_000_000, 630_930, 500_000, 430_677, 386_853)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EVALUATOR_IDENTITY_PAYLOAD = json.dumps(
    {
        "version": EVALUATOR_VERSION,
        "ndcg_discounts_ppm": _NDCG_DISCOUNTS_PPM,
        "rounding": "half-up-integer-v1",
    },
    separators=(",", ":"),
    sort_keys=True,
).encode("ascii")
EVALUATOR_IDENTITY_HASH = hashlib.sha256(_EVALUATOR_IDENTITY_PAYLOAD).hexdigest()


@dataclass(frozen=True)
class RankedLabel:
    item_id: str
    rank: int
    relevant: bool


@dataclass(frozen=True)
class EvidenceLabel:
    evidence_id: str
    expected: bool
    accepted: bool


@dataclass(frozen=True)
class ClaimLabel:
    claim_id: str
    accepted: bool
    supported: bool


@dataclass(frozen=True)
class RequiredFieldLabel:
    field_name: str
    present: bool


_COUNT_FIELDS = (
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
)


class QualityEvaluation(StrictModel):
    schema_version: Literal["1.0"] = QUALITY_SCHEMA_VERSION
    evaluator_version: Literal["stage9-quality-v1"] = EVALUATOR_VERSION
    fixture_identity_hash: str
    evaluator_identity_hash: str = EVALUATOR_IDENTITY_HASH
    evaluated_count: int = Field(ge=0)
    relevant_count: int = Field(ge=0)
    retrieved_count: int = Field(ge=0)
    precision_at_5_ppm: int = Field(ge=0, le=_PPM)
    recall_at_15_ppm: int = Field(ge=0, le=_PPM)
    ndcg_at_5_ppm: int = Field(ge=0, le=_PPM)
    expected_evidence_count: int = Field(ge=0)
    accepted_evidence_count: int = Field(ge=0)
    supported_evidence_count: int = Field(ge=0)
    rejected_evidence_count: int = Field(ge=0)
    evidence_precision_ppm: int = Field(ge=0, le=_PPM)
    evidence_recall_ppm: int = Field(ge=0, le=_PPM)
    expected_claim_count: int = Field(ge=0)
    accepted_claim_count: int = Field(ge=0)
    supported_claim_count: int = Field(ge=0)
    rejected_claim_count: int = Field(ge=0)
    unsupported_claim_rate_ppm: int = Field(ge=0, le=_PPM)
    required_field_count: int = Field(ge=0)
    present_required_field_count: int = Field(ge=0)
    missing_required_field_rate_ppm: int = Field(ge=0, le=_PPM)
    budget_passed: bool

    @field_validator("fixture_identity_hash", "evaluator_identity_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("identity must be a lowercase SHA-256 digest")
        return value

    @field_validator(*_COUNT_FIELDS, mode="before")
    @classmethod
    def reject_boolean_counts(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("quality counts must not be booleans")
        return value

    @model_validator(mode="after")
    def validate_counts_and_budget(self) -> Self:
        if self.retrieved_count > self.evaluated_count:
            raise ValueError("retrieved count cannot exceed evaluated count")
        if self.supported_evidence_count > min(
            self.expected_evidence_count, self.accepted_evidence_count
        ):
            raise ValueError("supported evidence count is inconsistent")
        if self.rejected_evidence_count > self.expected_evidence_count:
            raise ValueError("rejected evidence count is inconsistent")
        if not (
            self.supported_claim_count
            <= self.accepted_claim_count
            <= self.expected_claim_count
        ):
            raise ValueError("claim counts are inconsistent")
        if self.rejected_claim_count != (
            self.expected_claim_count - self.accepted_claim_count
        ):
            raise ValueError("rejected claim count is inconsistent")
        if self.present_required_field_count > self.required_field_count:
            raise ValueError("required field counts are inconsistent")
        expected_budget = (
            self.precision_at_5_ppm == _PPM
            and self.recall_at_15_ppm == _PPM
            and self.ndcg_at_5_ppm == _PPM
            and self.evidence_precision_ppm == _PPM
            and self.evidence_recall_ppm == _PPM
            and self.unsupported_claim_rate_ppm == 0
            and self.missing_required_field_rate_ppm == 0
        )
        if self.budget_passed != expected_budget:
            raise ValueError(f"quality budget_passed must be {expected_budget}")
        return self


def _ppm(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("quality denominator must be positive")
    return (numerator * _PPM + denominator // 2) // denominator


def _unique_non_empty(values: tuple[str, ...], *, domain: str) -> None:
    if not values or any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{domain} domain must be non-empty")
    if len(values) != len(set(values)):
        raise ValueError(f"{domain} domain contains duplicate identifiers")


def evaluate_quality(
    *,
    rankings: tuple[RankedLabel, ...],
    evidence: tuple[EvidenceLabel, ...],
    claims: tuple[ClaimLabel, ...],
    required_fields: tuple[RequiredFieldLabel, ...],
    fixture_identity_hash: str,
) -> QualityEvaluation:
    ranking_ids = tuple(item.item_id for item in rankings)
    _unique_non_empty(ranking_ids, domain="ranking")
    if len(rankings) < 15:
        raise ValueError("ranking domain must contain at least fifteen items")
    ranks = tuple(item.rank for item in rankings)
    if ranks != tuple(range(1, len(rankings) + 1)):
        raise ValueError("ranking ranks must be unique and consecutive")
    if any(not isinstance(item.relevant, bool) for item in rankings):
        raise ValueError("ranking relevance must be boolean")

    evidence_ids = tuple(item.evidence_id for item in evidence)
    claim_ids = tuple(item.claim_id for item in claims)
    field_names = tuple(item.field_name for item in required_fields)
    _unique_non_empty(evidence_ids, domain="evidence")
    _unique_non_empty(claim_ids, domain="claim")
    _unique_non_empty(field_names, domain="required field")
    if any(item.supported and not item.accepted for item in claims):
        raise ValueError("rejected claim cannot be supported")

    top_five = rankings[:5]
    top_fifteen = rankings[:15]
    relevant_count = sum(item.relevant for item in rankings)
    if relevant_count == 0:
        raise ValueError("ranking domain must contain relevant items")
    precision = _ppm(sum(item.relevant for item in top_five), 5)
    recall = _ppm(sum(item.relevant for item in top_fifteen), relevant_count)
    dcg = sum(
        discount
        for item, discount in zip(top_five, _NDCG_DISCOUNTS_PPM, strict=True)
        if item.relevant
    )
    ideal = sum(_NDCG_DISCOUNTS_PPM[: min(relevant_count, 5)])
    ndcg = _ppm(dcg, ideal)

    expected_evidence = sum(item.expected for item in evidence)
    accepted_evidence = sum(item.accepted for item in evidence)
    supported_evidence = sum(item.expected and item.accepted for item in evidence)
    if expected_evidence == 0 or accepted_evidence == 0:
        raise ValueError("evidence domain requires expected and accepted records")
    rejected_evidence = sum(item.expected and not item.accepted for item in evidence)

    accepted_claims = sum(item.accepted for item in claims)
    supported_claims = sum(item.supported for item in claims)
    if accepted_claims == 0:
        raise ValueError("claim domain requires accepted claims")
    present_fields = sum(item.present for item in required_fields)
    missing_fields = len(required_fields) - present_fields
    values = {
        "precision_at_5_ppm": precision,
        "recall_at_15_ppm": recall,
        "ndcg_at_5_ppm": ndcg,
        "evidence_precision_ppm": _ppm(supported_evidence, accepted_evidence),
        "evidence_recall_ppm": _ppm(supported_evidence, expected_evidence),
        "unsupported_claim_rate_ppm": _ppm(
            accepted_claims - supported_claims, accepted_claims
        ),
        "missing_required_field_rate_ppm": _ppm(
            missing_fields, len(required_fields)
        ),
    }
    budget_passed = (
        values["precision_at_5_ppm"] == _PPM
        and values["recall_at_15_ppm"] == _PPM
        and values["ndcg_at_5_ppm"] == _PPM
        and values["evidence_precision_ppm"] == _PPM
        and values["evidence_recall_ppm"] == _PPM
        and values["unsupported_claim_rate_ppm"] == 0
        and values["missing_required_field_rate_ppm"] == 0
    )
    return QualityEvaluation(
        fixture_identity_hash=fixture_identity_hash,
        evaluated_count=len(rankings),
        relevant_count=relevant_count,
        retrieved_count=len(top_fifteen),
        expected_evidence_count=expected_evidence,
        accepted_evidence_count=accepted_evidence,
        supported_evidence_count=supported_evidence,
        rejected_evidence_count=rejected_evidence,
        expected_claim_count=len(claims),
        accepted_claim_count=accepted_claims,
        supported_claim_count=supported_claims,
        rejected_claim_count=len(claims) - accepted_claims,
        required_field_count=len(required_fields),
        present_required_field_count=present_fields,
        budget_passed=budget_passed,
        **values,
    )
