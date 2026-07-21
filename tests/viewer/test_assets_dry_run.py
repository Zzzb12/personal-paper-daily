from __future__ import annotations

from pathlib import Path

from PIL import Image
from zotero_arxiv_daily.viewer.assets import EvidenceImagePublisher


def test_dry_run_returns_content_addressed_asset_without_creating_output(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    image = evidence_root / "figure.png"
    Image.new("RGB", (1, 1), color="white").save(image, format="PNG")

    asset = EvidenceImagePublisher(tmp_path / "site", dry_run=True).publish(
        image,
        evidence_root=evidence_root,
    )

    assert asset.relative_path.as_posix().startswith("assets/evidence/")
    assert not (tmp_path / "site").exists()
