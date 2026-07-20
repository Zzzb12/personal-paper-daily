from __future__ import annotations

import re


_LABEL_RE = re.compile(
    r"^\s*(?P<kind>fig(?:ure)?\.?|table)\s+(?P<number>[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*)",
    re.IGNORECASE,
)


def extract_visual_label(caption: str | None, *, kind: str) -> str | None:
    if caption is None:
        return None
    match = _LABEL_RE.match(caption)
    if match is None:
        return None
    normalized_kind = "Table" if kind == "table" else "Figure"
    return f"{normalized_kind} {match.group('number')}"
