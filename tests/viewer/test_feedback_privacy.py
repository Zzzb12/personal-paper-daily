from __future__ import annotations

from pathlib import Path

from zotero_arxiv_daily.pipeline.artifacts import ArtifactAuditor
from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
from zotero_arxiv_daily.viewer.schemas import ViewerSettings


def test_public_feedback_asset_uses_browser_local_state_without_private_store_sync(
    tmp_path: Path,
) -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    site = tmp_path / "viewer"
    manifest = StaticViewerBuilder(ViewerSettings(output_root=site)).build(
        (result,), batch_label="2026-07-26"
    )

    audit = ArtifactAuditor(tmp_path).audit(site)
    feedback_source = (site / "assets" / "feedback.js").read_text(encoding="utf-8")

    assert audit.build_manifest == manifest
    assert "assets/feedback.js" in manifest.written_paths
    assert all(
        marker not in "\n".join(manifest.written_paths).lower()
        for marker in ("private", "bundle", "snapshot", "store", "cache", "zotero")
    )
    assert "localStorage" in feedback_source
    assert "data/private-feedback" not in feedback_source
    assert "fetch(" not in feedback_source
    assert "sendBeacon" not in feedback_source
