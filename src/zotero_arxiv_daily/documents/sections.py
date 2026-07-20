from __future__ import annotations


_HEADING_LABELS = {"section_header", "title", "heading"}


def is_heading(label: str) -> bool:
    return label.lower() in _HEADING_LABELS


def normalized_section_level(parser_level: int) -> int:
    return max(1, parser_level + 1)
