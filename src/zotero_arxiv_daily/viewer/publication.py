from __future__ import annotations

from dataclasses import dataclass

from zotero_arxiv_daily.analysis.validation_schemas import ValidationPaperResult
from zotero_arxiv_daily.viewer.schemas import PublicationDecision


@dataclass(frozen=True, slots=True)
class PublicationPolicy:
    allow_partial: bool

    def decide(self, result: ValidationPaperResult) -> PublicationDecision:
        report = result.report
        if (
            result.status == "validated"
            and result.validated is not None
            and report is not None
            and report.status == "valid"
            and report.publication_eligibility == "eligible"
        ):
            return PublicationDecision(paper_id=result.paper_id, kind="full")
        if (
            self.allow_partial
            and result.status == "partial"
            and result.validated is not None
            and report is not None
            and report.status == "partial"
            and report.publication_eligibility == "blocked"
        ):
            return PublicationDecision(
                paper_id=result.paper_id,
                kind="partial",
                issues=report.issues,
            )
        return PublicationDecision(
            paper_id=result.paper_id,
            kind="excluded",
            issues=report.issues if report is not None else result.issues,
        )
