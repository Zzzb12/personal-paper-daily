from __future__ import annotations

from pathlib import Path

from zotero_arxiv_daily.pipeline import viewer
from zotero_arxiv_daily.pipeline.viewer import run_offline_fixture


def test_offline_fixture_builds_a_site_without_network(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "evidence" / "stage4_golden.json"

    manifest = run_offline_fixture(fixture, output_root=tmp_path / "site", dry_run=False)

    assert manifest.published_count == 1
    assert (tmp_path / "site" / "index.html").exists()


def test_cli_loads_configured_and_explicit_evidence_roots(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "viewer.yaml"
    config.write_text("viewer_pipeline:\n  evidence_roots: [configured-evidence]\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_build(
        fixture_path: Path, *, output_root: Path, dry_run: bool, evidence_roots: tuple[Path, ...]
    ) -> object:
        captured.update(
            fixture_path=fixture_path,
            output_root=output_root,
            dry_run=dry_run,
            evidence_roots=evidence_roots,
        )
        from zotero_arxiv_daily.viewer.schemas import BuildManifest

        return BuildManifest(
            build_version="test",
            template_version="test",
            published_count=0,
            partial_count=0,
            written_paths=(),
        )

    monkeypatch.setattr(viewer, "run_offline_fixture", fake_build)

    assert viewer.main(
        [
            "--offline-fixture",
            str(tmp_path / "fixture.json"),
            "--output-root",
            str(tmp_path / "site"),
            "--config",
            str(config),
            "--evidence-root",
            str(tmp_path / "explicit-evidence"),
        ]
    ) == 0

    assert captured["evidence_roots"] == (
        Path("configured-evidence"),
        tmp_path / "explicit-evidence",
    )
