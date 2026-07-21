from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.viewer.schemas import IndexPageModel, PaperPageModel, ViewerSettings


def test_viewer_settings_has_safe_defaults_and_bounded_limits(tmp_path: Path) -> None:
    settings = ViewerSettings(output_root=tmp_path / "viewer")

    assert settings.allow_partial is False
    assert settings.max_papers == 30
    assert settings.max_image_bytes == 10 * 1024 * 1024


@pytest.mark.parametrize("field,value", [("max_papers", 0), ("max_image_bytes", 0)])
def test_viewer_settings_rejects_unsafe_limits(tmp_path: Path, field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        ViewerSettings(output_root=tmp_path / "viewer", **{field: value})


def test_page_models_reject_unsafe_page_paths_and_keep_public_fields() -> None:
    page = PaperPageModel(
        paper_id="arxiv:2401.00001",
        relative_path="papers/2401.00001.html",
        english_title="Synthetic paper",
        chinese_title=None,
        publication_kind="full",
    )

    assert page.chinese_title is None
    assert page.relative_path == "papers/2401.00001.html"
    with pytest.raises(ValidationError):
        PaperPageModel(
            paper_id="arxiv:2401.00001",
            relative_path="../index.html",
            english_title="Synthetic paper",
            chinese_title=None,
            publication_kind="full",
        )
