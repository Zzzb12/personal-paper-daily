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


def test_feedback_styles_keep_native_actions_visible_and_status_readable() -> None:
    css = (Path(__file__).parents[2] / "src" / "zotero_arxiv_daily" / "viewer" / "static" / "site.css").read_text(
        encoding="utf-8"
    )

    assert ".feedback-toolbar" in css
    assert ".feedback-actions" in css
    assert ".feedback-status" in css
    assert "button:focus-visible" in css
    assert "[aria-pressed=\"true\"]" in css
    assert "[hidden]" in css


def test_card_entrance_only_uses_transform_and_opacity() -> None:
    css = (Path(__file__).parents[2] / "src" / "zotero_arxiv_daily" / "viewer" / "static" / "site.css").read_text(
        encoding="utf-8"
    )

    assert "@keyframes observatory-enter" in css
    assert "transform:" in css
    assert "opacity:" in css
    assert "animation: none !important" in css


def test_reading_layout_provides_compact_controls_and_responsive_navigation() -> None:
    css = (Path(__file__).parents[2] / "src" / "zotero_arxiv_daily" / "viewer" / "static" / "site.css").read_text(
        encoding="utf-8"
    )

    assert "--space-ink:" in css
    assert "--electric-blue:" in css
    assert "--science-cyan:" in css
    assert "--night:" not in css
    assert "--signal:" not in css
    assert ".detail-shell" in css
    assert ".reading-rail" in css
    assert ".story-section" in css
    assert ".insight-card" in css
    assert ".coverage-panel" in css
    assert ".paper-search:focus-within" in css
    assert ".feedback-tools" in css
    assert ".paper-toc { max-width: 100%; overflow-x: auto;" in css
    assert "position: sticky" in css
