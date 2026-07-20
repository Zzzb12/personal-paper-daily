from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.paper_schemas import PaperAnalysis
from zotero_arxiv_daily.analysis.schemas import StrictModel, validate_run_id_value


VALIDATION_SCHEMA_VERSION = "1.0"
VALIDATOR_VERSION = "stage4-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ValidationStatus = Literal["valid", "partial", "invalid"]
PublicationEligibility = Literal["eligible", "blocked"]
PaperValidationStatus = Literal["validated", "partial", "invalid", "failed", "skipped"]


def _non_empty(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _optional_text(value: str | None) -> str | None:
    return _non_empty(value) if value is not None else None


class ValidationIssue(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    paper_id: str
    claim_id: str | None = None
    field_path: str | None = None
    evidence_id: str | None = None
    visual_id: str | None = None
    message: str

    @field_validator("code", "paper_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("claim_id", "field_path", "evidence_id", "visual_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("message")
    @classmethod
    def validate_safe_message(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("validation message must be a single line")
        normalized = _non_empty(value)
        if len(normalized) > 240:
            raise ValueError("validation message must remain concise")
        return normalized

    @model_validator(mode="after")
    def require_location(self) -> Self:
        if not any((self.claim_id, self.field_path, self.evidence_id, self.visual_id)):
            raise ValueError("validation issue requires a safe location")
        return self


class ClaimValidationResult(StrictModel):
    claim_id: str
    status: Literal["valid", "invalid"]
    resolved_evidence_ids: tuple[str, ...]
    issue_codes: tuple[str, ...] = ()

    @field_validator("claim_id")
    @classmethod
    def normalize_claim_id(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("resolved_evidence_ids", "issue_codes")
    @classmethod
    def validate_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_non_empty(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("claim validation values must be unique")
        return normalized


class ValidationReport(StrictModel):
    schema_version: Literal["1.0"] = VALIDATION_SCHEMA_VERSION
    validator_version: str = VALIDATOR_VERSION
    paper_id: str
    status: ValidationStatus
    publication_eligibility: PublicationEligibility
    input_fingerprint: str
    claim_results: tuple[ClaimValidationResult, ...]
    issues: tuple[ValidationIssue, ...]

    @field_validator("validator_version", "paper_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("input_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("input_fingerprint must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.status == "valid" and any(issue.severity == "error" for issue in self.issues):
            raise ValueError("valid report cannot contain an error issue")
        expected = (
            "eligible"
            if self.status == "valid" and not any(issue.severity == "error" for issue in self.issues)
            else "blocked"
        )
        if self.publication_eligibility != expected:
            raise ValueError(f"publication eligibility must be {expected}")
        if self.status == "invalid" and not any(
            issue.severity == "error" for issue in self.issues
        ):
            raise ValueError("invalid report requires an error issue")
        claim_ids = tuple(result.claim_id for result in self.claim_results)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim validation result IDs must be unique")
        if any(issue.paper_id != self.paper_id for issue in self.issues):
            raise ValueError("validation issue paper_id must match its report")
        return self


class ValidatedPaperAnalysis(StrictModel):
    analysis: PaperAnalysis
    report: ValidationReport

    @model_validator(mode="after")
    def validate_paper_id(self) -> Self:
        if self.analysis.paper_id != self.report.paper_id:
            raise ValueError("analysis paper_id must match validation report")
        return self


class ValidationPaperResult(StrictModel):
    paper_id: str
    status: PaperValidationStatus
    validated: ValidatedPaperAnalysis | None
    issues: tuple[ValidationIssue, ...] = ()
    processing_seconds: float = Field(ge=0)
    cache_hit: bool = False
    validator_version: str = VALIDATOR_VERSION

    @field_validator("paper_id", "validator_version")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        report_status = {
            "validated": "valid",
            "partial": "partial",
            "invalid": "invalid",
        }
        if self.status in report_status:
            if self.validated is None:
                raise ValueError("validated, partial, or invalid result requires validation output")
            if self.validated.report.status != report_status[self.status]:
                raise ValueError("paper result status must match validation report status")
            if self.validated.analysis.paper_id != self.paper_id:
                raise ValueError("validated analysis paper_id must match result paper_id")
            if self.issues != self.validated.report.issues:
                raise ValueError("paper result issues must match validation report issues")
        else:
            if self.validated is not None or not self.issues:
                raise ValueError("failed or skipped result requires issues and no validation output")
        if any(issue.paper_id != self.paper_id for issue in self.issues):
            raise ValueError("paper result issue paper_id must match result paper_id")
        return self


class ValidationBatchResult(StrictModel):
    schema_version: Literal["1.0"] = VALIDATION_SCHEMA_VERSION
    validator_version: str = VALIDATOR_VERSION
    run_id: str
    created_at: datetime
    results: tuple[ValidationPaperResult, ...]
    cache_hit_count: int = Field(default=0, ge=0)

    @field_validator("validator_version")
    @classmethod
    def normalize_validator_version(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return validate_run_id_value(value)

    @field_validator("created_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if len(self.results) > 5:
            raise ValueError("validation batch may contain at most five paper results")
        paper_ids = tuple(result.paper_id for result in self.results)
        if len(paper_ids) != len(set(paper_ids)):
            raise ValueError("validation batch paper IDs must be unique")
        if any(result.validator_version != self.validator_version for result in self.results):
            raise ValueError("result validator version must match batch validator version")
        if self.cache_hit_count != sum(result.cache_hit for result in self.results):
            raise ValueError("cache hit count must match validation results")
        return self
