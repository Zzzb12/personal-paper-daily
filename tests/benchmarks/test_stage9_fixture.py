from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1] / "fixtures" / "benchmarks" / "stage9"


def _load(name: str) -> object:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_fixture_has_exact_original_30_15_5_shape() -> None:
    candidates = _load("candidates.json")
    quality = _load("quality-labels.json")
    analyses = _load("analyses.json")

    assert candidates["schema_version"] == "stage9-synthetic-candidates-v1"
    assert len(candidates["records"]) == 30
    assert len({item["synthetic_id"] for item in candidates["records"]}) == 30
    assert quality["ranked_ids"] == [
        item["synthetic_id"] for item in candidates["records"][:15]
    ]
    assert len(quality["ranked_ids"]) == 15
    assert len(quality["analysis_ids"]) == 5
    assert quality["analysis_ids"] == quality["ranked_ids"][:5]
    assert len(analyses["records"]) == 5
    assert [item["synthetic_id"] for item in analyses["records"]] == quality["analysis_ids"]


def test_fixture_provenance_and_approved_cc0_notice_are_explicit() -> None:
    provenance = (ROOT / "PROVENANCE.md").read_text(encoding="utf-8")
    license_text = (ROOT / "LICENSE.txt").read_text(encoding="utf-8")

    assert "independently created for this repository" in provenance
    assert "not copied from any paper, dataset, or reference implementation" in provenance
    assert "SPDX-License-Identifier: CC0-1.0" in provenance
    assert "SPDX-License-Identifier: CC0-1.0" in license_text


def test_fixture_contains_only_bounded_synthetic_tokens_and_no_private_surface() -> None:
    texts = [
        path.read_text(encoding="utf-8")
        for path in sorted(ROOT.iterdir())
        if path.is_file()
    ]
    joined = "\n".join(texts)
    lowered = joined.casefold()

    assert len(joined.encode("utf-8")) < 64 * 1024
    assert not re.search(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", joined)
    assert not re.search(r"\b(?:arxiv|zotero):", lowered)
    assert not re.search(r"https?://", lowered)
    assert not re.search(r"\b(?:author|abstract|prompt|response|api[_ -]?key)\b", lowered)
    assert not re.search(r"(?:[a-z]:\\|\\\\|/home/|/users/)", lowered)

    for name in ("candidates.json", "quality-labels.json", "analyses.json"):
        payload = _load(name)
        serialized = json.dumps(payload)
        assert len(serialized) < 32 * 1024
        assert set(payload).issubset(
            {"schema_version", "records", "ranked_ids", "analysis_ids"}
        )


def test_candidate_and_analysis_records_have_exact_allowlisted_fields() -> None:
    candidates = _load("candidates.json")["records"]
    analyses = _load("analyses.json")["records"]

    assert all(
        set(item) == {"synthetic_id", "feature_x", "feature_y", "relevant"}
        for item in candidates
    )
    assert all(
        set(item)
        == {
            "synthetic_id",
            "expected_evidence_count",
            "accepted_evidence_count",
            "supported_claim_count",
            "accepted_claim_count",
            "required_field_count",
            "present_required_field_count",
        }
        for item in analyses
    )
    assert all(
        isinstance(value, (int, bool))
        for item in (*candidates, *analyses)
        for key, value in item.items()
        if key != "synthetic_id"
    )
