from __future__ import annotations

from pathlib import Path

from zotero_arxiv_daily.pipeline.viewer import run_offline_fixture


def test_offline_fixture_builds_a_site_without_network(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "evidence" / "stage4_golden.json"

    manifest = run_offline_fixture(fixture, output_root=tmp_path / "site", dry_run=False)

    assert manifest.published_count == 1
    assert (tmp_path / "site" / "index.html").exists()
