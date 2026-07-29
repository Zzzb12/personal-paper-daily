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


def test_feedback_styles_use_a_restrained_compositor_only_observatory_entrance() -> None:
    css = (Path(__file__).parents[2] / "src" / "zotero_arxiv_daily" / "viewer" / "static" / "site.css").read_text(
        encoding="utf-8"
    )

    assert "@keyframes observatory-enter" in css
    assert "transform:" in css
    assert "opacity:" in css
    assert "will-change: transform, opacity" in css
    assert "will-change: auto" in css


def test_white_blue_research_observatory_has_depth_and_clear_section_hierarchy() -> None:
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
    assert ".orbital-display" in css
    assert ".telemetry-line" in css
    assert "position: sticky" in css
    assert "radial-gradient" in css
