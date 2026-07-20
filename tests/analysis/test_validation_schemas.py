from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.analysis.validation_schemas import (
    VALIDATION_SCHEMA_VERSION,
    VALIDATION_MESSAGES,
    VALIDATOR_VERSION,
    ClaimValidationResult,
    ValidationBatchResult,
    ValidationIssue,
    ValidationPaperResult,
    ValidationReport,
    ValidatedPaperAnalysis,
)
from zotero_arxiv_daily.pipeline.validation import ValidationSettings
from tests.analysis.stage4_factories import golden_inputs


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def _issue(*, severity: str = "error") -> ValidationIssue:
    return ValidationIssue(
        code="unknown_evidence",
        severity=severity,
        paper_id="arxiv:2401.00001",
        field_path="insights.0.evidence_ids.0",
        evidence_id="missing",
        message=VALIDATION_MESSAGES["unknown_evidence"],
    )


def _report(
    *,
    status: str = "valid",
    publication_eligibility: str | None = None,
    issues: tuple[ValidationIssue, ...] = (),
) -> ValidationReport:
    eligibility = publication_eligibility or (
        "eligible" if status == "valid" else "blocked"
    )
    return ValidationReport(
        paper_id="arxiv:2401.00001",
        status=status,
        publication_eligibility=eligibility,
        input_fingerprint="a" * 64,
        claim_results=(
            ClaimValidationResult(
                claim_id="insight-1",
                status="valid",
                resolved_evidence_ids=("evidence-1",),
            ),
        ),
        issues=issues,
    )


def _failed_result(index: int) -> ValidationPaperResult:
    paper_id = f"arxiv:2401.{index:05d}"
    issue = ValidationIssue(
        code="validation_input_missing",
        severity="error",
        paper_id=paper_id,
        field_path="analysis",
        message=VALIDATION_MESSAGES["validation_input_missing"],
    )
    return ValidationPaperResult(
        paper_id=paper_id,
        status="failed",
        validated=None,
        issues=(issue,),
        processing_seconds=0,
    )


def test_only_valid_report_is_publication_eligible() -> None:
    report = _report()

    assert report.publication_eligibility == "eligible"

    with pytest.raises(ValidationError, match="blocked"):
        _report(status="invalid", publication_eligibility="eligible", issues=(_issue(),))


def test_error_issue_prevents_valid_report() -> None:
    with pytest.raises(ValidationError, match="error issue"):
        _report(status="valid", issues=(_issue(),))


def test_validation_issue_is_safe_and_locatable() -> None:
    issue = _issue()

    assert issue.claim_id is None
    assert issue.evidence_id == "missing"

    with pytest.raises(ValidationError, match="location"):
        ValidationIssue(
            code="input_error",
            severity="error",
            paper_id="arxiv:2401.00001",
            message="Validation input is invalid",
        )

    with pytest.raises(ValidationError, match="controlled message"):
        ValidationIssue(
            code="unknown_evidence",
            severity="error",
            paper_id="arxiv:2401.00001",
            field_path="analysis",
            message="See https://user:password@example.test/?token=secret",
        )
    with pytest.raises(ValidationError, match="controlled message"):
        ValidationIssue(
            code="unknown_evidence",
            severity="error",
            paper_id="arxiv:2401.00001",
            field_path="analysis",
            message="Evidence packet fingerprint does not match its contents",
        )
    with pytest.raises(ValidationError, match="safe identifier"):
        ValidationIssue(
            code="unknown_evidence",
            severity="error",
            paper_id="arxiv:2401.00001",
            field_path="analysis",
            evidence_id="https://example.test/?token=secret",
            message=VALIDATION_MESSAGES["unknown_evidence"],
        )


def test_validated_analysis_uses_strict_paper_analysis_boundary() -> None:
    inputs = golden_inputs()
    report = _report()

    with pytest.raises(ValidationError):
        ValidatedPaperAnalysis(analysis={"paper_id": inputs.candidate.paper_id}, report=report)


def test_validation_settings_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        ValidationSettings(schema_version="2.0")
    with pytest.raises(ValidationError, match="single line"):
        ValidationIssue(
            code="input_error",
            severity="error",
            paper_id="arxiv:2401.00001",
            field_path="analysis",
            message="unsafe\nsource content",
        )


def test_claim_validation_result_rejects_duplicate_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ClaimValidationResult(
            claim_id="insight-1",
            status="valid",
            resolved_evidence_ids=("evidence-1", "evidence-1"),
        )


def test_validation_batch_is_versioned_unique_and_capped_at_five() -> None:
    batch = ValidationBatchResult(run_id="run-1", created_at=NOW, results=())

    assert batch.schema_version == VALIDATION_SCHEMA_VERSION == "1.0"
    assert batch.validator_version == VALIDATOR_VERSION == "stage4-v1"

    with pytest.raises(ValidationError, match="at most five"):
        ValidationBatchResult(
            run_id="run-1",
            created_at=NOW,
            results=tuple(_failed_result(index) for index in range(6)),
        )
    with pytest.raises(ValidationError, match="unique"):
        ValidationBatchResult(
            run_id="run-1",
            created_at=NOW,
            results=(_failed_result(1), _failed_result(1)),
        )


def test_validation_batch_requires_aware_time_and_safe_run_id() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ValidationBatchResult(run_id="run-1", created_at=NOW.replace(tzinfo=None), results=())
    with pytest.raises(ValidationError, match="safe file name"):
        ValidationBatchResult(run_id="../run", created_at=NOW, results=())


def test_failed_result_requires_issue_and_no_validated_analysis() -> None:
    issue = _issue()
    with pytest.raises(ValidationError, match="failed or skipped"):
        ValidationPaperResult(
            paper_id="arxiv:2401.00001",
            status="failed",
            validated=None,
            issues=(),
            processing_seconds=0,
        )
    with pytest.raises(ValidationError, match="validator version"):
        ValidationBatchResult(
            validator_version="stage4-v2",
            run_id="run-1",
            created_at=NOW,
            results=(
                ValidationPaperResult(
                    paper_id="arxiv:2401.00001",
                    status="failed",
                    validated=None,
                    issues=(issue,),
                    processing_seconds=0,
                    validator_version="stage4-v1",
                ),
            ),
        )
