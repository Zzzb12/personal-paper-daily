from __future__ import annotations

from pathlib import Path


def test_styles_provide_visible_focus_and_reduced_motion() -> None:
    css = (Path(__file__).parents[2] / "src" / "zotero_arxiv_daily" / "viewer" / "static" / "site.css").read_text(
        encoding="utf-8"
    )

    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "@media (max-width: 640px)" in css
    assert "max-width: 100%" in css
    assert "overflow-x: auto" in css
