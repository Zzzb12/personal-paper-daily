from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic import field_validator
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


class PaperPageModel(StrictModel):
    paper_id: str
    relative_path: str
    english_title: str
    chinese_title: str | None
    publication_kind: Literal["full", "partial"]

    @field_validator("relative_path")
    @classmethod
    def require_safe_html_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".html":
            raise ValueError("relative_path must be a safe HTML path")
        return value


class IndexPageModel(StrictModel):
    batch_label: str
    papers: tuple[PaperPageModel, ...]
    valid_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
