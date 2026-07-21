from __future__ import annotations

from pathlib import Path

from PIL import Image
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


def test_rebuild_removes_previous_page_when_paper_becomes_invalid(tmp_path: Path) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    valid = validate_paper(*golden_inputs())
    builder = StaticViewerBuilder(ViewerSettings(output_root=tmp_path / "site"))
    builder.build((valid,), batch_label="2026-07-21")
    detail = tmp_path / "site" / "papers" / "arxiv-2401.00001.html"
    assert detail.exists()

    invalid = valid.model_copy(update={"status": "invalid", "validated": None})
    builder.build((invalid,), batch_label="2026-07-21")

    assert not detail.exists()


def test_builder_publishes_local_evidence_image_and_renders_it(tmp_path: Path) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    assert result.validated is not None
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    image = evidence_root / "figure.png"
    Image.new("RGB", (1, 1), color="white").save(image, format="PNG")
    visual = result.validated.analysis.supporting_visuals[0]
    region = visual.regions[0].model_copy(update={"image_path": image})
    analysis = result.validated.analysis.model_copy(
        update={"supporting_visuals": (visual.model_copy(update={"regions": (region,)}),)}
    )
    with_image = result.model_copy(update={"validated": result.validated.model_copy(update={"analysis": analysis})})

    StaticViewerBuilder(
        ViewerSettings(output_root=tmp_path / "site", evidence_roots=(evidence_root,))
    ).build((with_image,), batch_label="2026-07-21")

    detail_html = (tmp_path / "site" / "papers" / "arxiv-2401.00001.html").read_text(encoding="utf-8")
    assert '<img src="../assets/evidence/' in detail_html
    assert list((tmp_path / "site" / "assets" / "evidence").glob("*.png"))


def test_builder_enforces_configured_page_limit(tmp_path: Path) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    second = result.model_copy(
        update={
            "paper_id": "arxiv:2401.00002",
            "validated": result.validated.model_copy(
                update={"analysis": result.validated.analysis.model_copy(update={"paper_id": "arxiv:2401.00002"})}
            ),
        }
    )

    manifest = StaticViewerBuilder(ViewerSettings(output_root=tmp_path / "site", max_papers=1)).build(
        (result, second), batch_label="2026-07-21"
    )

    assert manifest.published_count == 1
    assert not (tmp_path / "site" / "papers" / "arxiv-2401.00002.html").exists()
