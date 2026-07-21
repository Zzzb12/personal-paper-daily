from __future__ import annotations

from pathlib import Path

import pytest

from zotero_arxiv_daily.viewer.assets import EvidenceImagePublisher


def test_publishes_evidence_using_a_content_addressed_path(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    image = evidence_root / "figure.png"
    image.write_bytes(b"synthetic-image")

    asset = EvidenceImagePublisher(tmp_path / "site").publish(image, evidence_root=evidence_root)

    assert asset.relative_path.as_posix().startswith("assets/evidence/")
    assert (tmp_path / "site" / asset.relative_path).read_bytes() == b"synthetic-image"


def test_rejects_evidence_outside_approved_root(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not-approved")

    with pytest.raises(ValueError, match="evidence root"):
        EvidenceImagePublisher(tmp_path / "site").publish(outside, evidence_root=evidence_root)


def test_equal_content_does_not_create_colliding_duplicate_assets(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    first = evidence_root / "first.png"
    second = evidence_root / "second.png"
    first.write_bytes(b"same-content")
    second.write_bytes(b"same-content")

    publisher = EvidenceImagePublisher(tmp_path / "site")
    assert publisher.publish(first, evidence_root=evidence_root) == publisher.publish(
        second, evidence_root=evidence_root
    )


def test_rejects_symlinked_output_asset_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    image = evidence_root / "figure.png"
    image.write_bytes(b"synthetic-image")
    output_directory = tmp_path / "site" / "assets" / "evidence"
    original_is_symlink = Path.is_symlink

    def simulated_is_symlink(path: Path) -> bool:
        return path == output_directory or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", simulated_is_symlink)

    with pytest.raises(ValueError, match="symbolic link"):
        EvidenceImagePublisher(tmp_path / "site").publish(image, evidence_root=evidence_root)
