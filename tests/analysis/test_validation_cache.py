from __future__ import annotations

from pathlib import Path
import json

import pytest

from zotero_arxiv_daily.analysis.validation_cache import (
    ValidationCache,
    ValidationCacheIdentity,
    build_validation_cache_identity,
)
from zotero_arxiv_daily.analysis.validator import validate_paper
from tests.analysis.stage4_factories import golden_inputs


def _identity(**updates) -> ValidationCacheIdentity:
    inputs = golden_inputs()
    identity = build_validation_cache_identity(
        inputs.candidate,
        inputs.document,
        inputs.packet,
        inputs.analysis_result.analysis,
    )
    return identity.model_copy(update=updates)


def _validated():
    result = validate_paper(*golden_inputs())
    assert result.validated is not None
    return result.validated


def _changed_value(identity: ValidationCacheIdentity, field: str):
    value = getattr(identity, field)
    if field == "validator_version":
        return "stage4-v2"
    if field in {
        "candidate_fingerprint",
        "pdf_sha256",
        "document_fingerprint",
        "packet_fingerprint",
        "analysis_generation_key",
        "analysis_fingerprint",
        "input_fingerprint",
    }:
        return "f" * 64 if value != "f" * 64 else "e" * 64
    return f"{value}-changed"


def test_round_trip_uses_complete_identity_and_preserves_result(tmp_path: Path) -> None:
    cache = ValidationCache(tmp_path / "validation")
    identity = _identity()
    validated = _validated()

    target = cache.write(identity, validated)

    assert target.is_file()
    assert cache.read(identity) == validated
    assert tuple(target.parent.glob("*.tmp")) == ()


@pytest.mark.parametrize(
    "field",
    [
        "validator_version",
        "candidate_fingerprint",
        "pdf_sha256",
        "document_schema_version",
        "document_parser",
        "parser_version",
        "mapper_version",
        "document_config_version",
        "document_fingerprint",
        "packet_schema_version",
        "evidence_builder_version",
        "packet_fingerprint",
        "analysis_schema_version",
        "analysis_generation_key",
        "analysis_fingerprint",
        "input_fingerprint",
    ],
)
def test_every_required_identity_change_causes_cache_miss(
    tmp_path: Path, field: str
) -> None:
    cache = ValidationCache(tmp_path / "validation")
    original = _identity()
    cache.write(original, _validated())
    changed = original.model_copy(update={field: _changed_value(original, field)})

    assert changed.cache_key != original.cache_key
    assert cache.read(changed) is None


def test_candidate_title_or_link_change_changes_identity() -> None:
    inputs = golden_inputs()
    original = build_validation_cache_identity(*inputs[:3], inputs.analysis_result.analysis)
    changed_candidate = inputs.candidate.model_copy(
        update={"title": "Changed synthetic title", "code_url": "https://example.test/code"}
    )

    changed = build_validation_cache_identity(
        changed_candidate,
        inputs.document,
        inputs.packet,
        inputs.analysis_result.analysis,
    )

    assert changed.candidate_fingerprint != original.candidate_fingerprint
    assert changed.cache_key != original.cache_key


def test_corrupt_or_oversized_validator_cache_is_a_miss(tmp_path: Path) -> None:
    identity = _identity()
    cache = ValidationCache(tmp_path / "validation", max_cache_bytes=128)
    target = cache.path_for(identity)
    target.parent.mkdir(parents=True)
    target.write_text("not-json", encoding="utf-8")
    assert cache.read(identity) is None

    target.write_bytes(b"x" * 129)
    assert cache.read(identity) is None


def test_cache_rejects_mismatched_envelope_and_oversized_write(tmp_path: Path) -> None:
    identity = _identity()
    validated = _validated()
    cache = ValidationCache(tmp_path / "validation", max_cache_bytes=1)

    with pytest.raises(ValueError, match="exceeds"):
        cache.write(identity, validated)
    assert not cache.path_for(identity).exists()

    wrong_identity = identity.model_copy(update={"paper_id": "arxiv:2401.99999"})
    normal = ValidationCache(tmp_path / "normal")
    with pytest.raises(ValueError, match="paper_id"):
        normal.write(wrong_identity, validated)


def test_cache_path_stays_inside_root(tmp_path: Path) -> None:
    cache = ValidationCache(tmp_path / "validation")
    target = cache.path_for(_identity())

    assert target.resolve().is_relative_to(cache.root.resolve())


@pytest.mark.parametrize("tamper", ["claim", "evidence", "report"])
def test_cache_rejects_schema_valid_payload_tampering(
    tmp_path: Path, tamper: str
) -> None:
    identity = _identity()
    cache = ValidationCache(tmp_path / "validation")
    target = cache.write(identity, _validated())
    payload = json.loads(target.read_text(encoding="utf-8"))
    if tamper == "claim":
        payload["analysis"]["insights"][0]["text_zh"] = "被篡改但仍符合 schema 的结论。"
    elif tamper == "evidence":
        payload["analysis"]["evidence_candidates"][0]["evidence_text"] = "tampered"
    else:
        payload["report"]["status"] = "partial"
        payload["report"]["publication_eligibility"] = "blocked"
    target.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.read(identity) is None
