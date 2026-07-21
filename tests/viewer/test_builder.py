from __future__ import annotations

from pathlib import Path

from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
from zotero_arxiv_daily.viewer.schemas import ViewerSettings


def test_builder_writes_index_detail_css_and_manifest_from_validated_result(tmp_path: Path) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    manifest = StaticViewerBuilder(ViewerSettings(output_root=tmp_path / "site")).build(
        (result,),
        batch_label="2026-07-21",
    )

    assert manifest.published_count == 1
    assert (tmp_path / "site" / "index.html").exists()
    assert (tmp_path / "site" / "papers" / "arxiv-2401.00001.html").exists()
    assert (tmp_path / "site" / "assets" / "site.css").exists()
    assert (tmp_path / "site" / "build-manifest.json").exists()


def test_builder_excludes_invalid_result_and_generates_empty_state(tmp_path: Path) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    valid = validate_paper(*golden_inputs())
    invalid = valid.model_copy(update={"status": "invalid", "validated": None})
    manifest = StaticViewerBuilder(ViewerSettings(output_root=tmp_path / "site")).build(
        (invalid,),
        batch_label="2026-07-21",
    )

    assert manifest.published_count == 0
    assert "今日没有可发布的论文" in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
