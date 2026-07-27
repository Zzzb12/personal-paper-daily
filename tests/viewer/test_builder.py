from __future__ import annotations

from pathlib import Path
from PIL import Image
import pytest

from zotero_arxiv_daily.pipeline.artifacts import ArtifactAuditor
from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
from zotero_arxiv_daily.viewer.filesystem import AtomicOutputRoot
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
    assert (tmp_path / "site" / "assets" / "feedback.js").exists()
    assert (tmp_path / "site" / "build-manifest.json").exists()
    assert "assets/feedback.js" in manifest.written_paths


def test_builder_only_emits_feedback_controls_for_publication_eligible_papers(tmp_path: Path) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    eligible = validate_paper(*golden_inputs())
    excluded = eligible.model_copy(
        update={
            "paper_id": "arxiv:2401.00002",
            "status": "invalid",
            "validated": None,
        }
    )

    StaticViewerBuilder(ViewerSettings(output_root=tmp_path / "site")).build(
        (eligible, excluded),
        batch_label="2026-07-22",
    )

    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'data-paper-id="2401.00001"' in index
    assert "2401.00002" not in index
    assert not (tmp_path / "site" / "papers" / "arxiv-2401.00002.html").exists()


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


def test_parallel_and_sequential_builds_are_byte_and_artifact_equivalent(
    tmp_path: Path,
) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    sequential_root = tmp_path / "sequential"
    parallel_root = tmp_path / "parallel"
    sequential = StaticViewerBuilder(
        ViewerSettings(output_root=sequential_root),
        parallel_writes=False,
    ).build((result,), batch_label="stage9")
    parallel = StaticViewerBuilder(
        ViewerSettings(output_root=parallel_root),
        parallel_writes=True,
        max_write_workers=2,
    ).build((result,), batch_label="stage9")

    assert sequential == parallel
    for relative in sequential.written_paths:
        assert (sequential_root / relative).read_bytes() == (
            parallel_root / relative
        ).read_bytes()
    sequential_audit = ArtifactAuditor(tmp_path).audit(sequential_root)
    parallel_audit = ArtifactAuditor(tmp_path).audit(parallel_root)
    assert sequential_audit.file_count == parallel_audit.file_count
    assert sequential_audit.byte_count == parallel_audit.byte_count
    assert sequential_audit.artifact_hash == parallel_audit.artifact_hash


def test_parallel_builder_writes_manifest_last_and_not_after_batch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    calls: list[str] = []
    original_write = AtomicOutputRoot.write_text
    original_batch = AtomicOutputRoot.write_many_text
    original_cleanup = AtomicOutputRoot.remove_stale_files

    def batch(self, entries, **kwargs):
        calls.append("batch")
        assert all(relative.name != "build-manifest.json" for relative, _ in entries)
        return original_batch(self, entries, **kwargs)

    def single(self, relative, content):
        calls.append(relative.name)
        return original_write(self, relative, content)

    def cleanup(self, directory, **kwargs):
        calls.append("cleanup")
        return original_cleanup(self, directory, **kwargs)

    monkeypatch.setattr(AtomicOutputRoot, "write_many_text", batch)
    monkeypatch.setattr(AtomicOutputRoot, "write_text", single)
    monkeypatch.setattr(AtomicOutputRoot, "remove_stale_files", cleanup)
    StaticViewerBuilder(
        ViewerSettings(output_root=tmp_path / "success")
    ).build((result,), batch_label="stage9")

    assert calls == ["batch", "cleanup", "build-manifest.json"]

    def fail_batch(self, entries, **kwargs):
        raise RuntimeError("PRIVATE BATCH FAILURE")

    monkeypatch.setattr(AtomicOutputRoot, "write_many_text", fail_batch)
    failed_root = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="PRIVATE BATCH FAILURE"):
        StaticViewerBuilder(ViewerSettings(output_root=failed_root)).build(
            (result,),
            batch_label="stage9",
        )
    assert not (failed_root / "build-manifest.json").exists()

    cleanup_root = tmp_path / "cleanup-failed"

    def fail_cleanup(self, directory, **kwargs):
        calls.append("cleanup")
        raise ValueError("PRIVATE CLEANUP FAILURE")

    monkeypatch.setattr(AtomicOutputRoot, "write_many_text", original_batch)
    monkeypatch.setattr(AtomicOutputRoot, "remove_stale_files", fail_cleanup)
    with pytest.raises(ValueError, match="PRIVATE CLEANUP FAILURE"):
        StaticViewerBuilder(ViewerSettings(output_root=cleanup_root)).build(
            (result,),
            batch_label="stage9",
        )
    assert not (cleanup_root / "build-manifest.json").exists()


def test_viewer_build_does_not_introduce_gsap_or_change_static_frontend_contract(
    tmp_path: Path,
) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    root = tmp_path / "site"
    StaticViewerBuilder(ViewerSettings(output_root=root)).build(
        (result,),
        batch_label="stage9",
    )

    frontend = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.suffix in {".html", ".css", ".js"}
    ).casefold()
    assert "gsap" not in frontend
