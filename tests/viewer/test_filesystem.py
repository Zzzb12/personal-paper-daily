from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from zotero_arxiv_daily.viewer.filesystem import AtomicOutputRoot


def test_writes_a_relative_text_file_beneath_output_root(tmp_path: Path) -> None:
    output = AtomicOutputRoot(tmp_path / "site")

    path = output.write_text(PurePosixPath("papers", "paper.html"), "<main>阅读</main>")

    assert path == tmp_path / "site" / "papers" / "paper.html"
    assert path.read_text(encoding="utf-8") == "<main>阅读</main>"


@pytest.mark.parametrize("relative", [PurePosixPath("..", "escape.html"), PurePosixPath("/absolute.html")])
def test_rejects_output_path_escape(tmp_path: Path, relative: PurePosixPath) -> None:
    with pytest.raises(ValueError, match="relative"):
        AtomicOutputRoot(tmp_path / "site").write_text(relative, "blocked")


def test_dry_run_creates_no_output(tmp_path: Path) -> None:
    path = AtomicOutputRoot(tmp_path / "site", dry_run=True).write_text(PurePosixPath("index.html"), "x")

    assert path == tmp_path / "site" / "index.html"
    assert not (tmp_path / "site").exists()


def test_rejects_existing_symlink_inside_output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "site"
    link = root / "papers"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda value: value == link or original(value))

    with pytest.raises(ValueError, match="symbolic link"):
        AtomicOutputRoot(root).write_text(PurePosixPath("papers", "paper.html"), "blocked")


@pytest.mark.parametrize("unsafe_name", (r"\\Windows\\Temp\\escape.html", r"C:\\escape.html", r"C:escape.html"))
def test_rejects_windows_absolute_or_drive_qualified_output_component(
    tmp_path: Path, unsafe_name: str
) -> None:
    output = AtomicOutputRoot(tmp_path / "site", dry_run=True)

    with pytest.raises(ValueError, match="Windows path"):
        output.write_text(PurePosixPath("papers", unsafe_name), "unsafe")
