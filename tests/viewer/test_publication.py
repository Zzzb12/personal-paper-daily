from __future__ import annotations

from dataclasses import replace

from zotero_arxiv_daily.analysis.validation_schemas import ValidationPaperResult
from zotero_arxiv_daily.viewer.publication import PublicationPolicy


def _result(status: str, *, eligible: bool) -> ValidationPaperResult:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    validated = validate_paper(*golden_inputs())
    if status == "validated":
        return validated
    report = validated.report.model_copy(
        update={
            "status": "partial" if status == "partial" else "invalid",
            "publication_eligibility": "blocked",
        }
    )
    return validated.model_copy(
        update={
            "status": status,
            "report": report,
            "validated": validated.validated.model_copy(update={"report": report}),
        }
    )


def test_only_eligible_validated_analysis_is_full_publication() -> None:
    result = _result("validated", eligible=True)

    decision = PublicationPolicy(allow_partial=False).decide(result)

    assert decision.kind == "full"
    assert decision.paper_id == result.paper_id


def test_partial_is_explicit_only_when_enabled() -> None:
    result = _result("partial", eligible=False)

    assert PublicationPolicy(allow_partial=False).decide(result).kind == "excluded"
    assert PublicationPolicy(allow_partial=True).decide(result).kind == "partial"


def test_invalid_is_never_published_even_when_partial_is_enabled() -> None:
    result = _result("invalid", eligible=False)

    assert PublicationPolicy(allow_partial=True).decide(result).kind == "excluded"
