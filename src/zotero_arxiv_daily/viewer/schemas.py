from __future__ import annotations

from typing import Literal

from zotero_arxiv_daily.analysis.schemas import StrictModel
from zotero_arxiv_daily.analysis.validation_schemas import ValidationIssue


class PublicationDecision(StrictModel):
    paper_id: str
    kind: Literal["full", "partial", "excluded"]
    issues: tuple[ValidationIssue, ...] = ()

