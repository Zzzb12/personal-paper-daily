from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from zotero_arxiv_daily.analysis.schemas import StrictModel
from zotero_arxiv_daily.analysis.validation_schemas import ValidationIssue


class PublicationDecision(StrictModel):
    paper_id: str
    kind: Literal["full", "partial", "excluded"]
    issues: tuple[ValidationIssue, ...] = ()


class ViewerSettings(StrictModel):
    output_root: Path
    site_title: str = "Personal Paper Daily"
    allow_partial: bool = False
    max_papers: int = Field(default=30, ge=1, le=30)
    max_assets_per_paper: int = Field(default=3, ge=0, le=3)
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    build_version: str = "stage5-v1"
    template_version: str = "stage5-v1"
