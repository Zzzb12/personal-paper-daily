import json

import pytest

from zotero_arxiv_daily.analysis.cache import (
    AnalysisCache,
    AnalysisCacheEnvelope,
    AnalysisCacheIdentity,
    build_analysis_cache_identity,
)
from tests.analysis.test_paper_schemas import analysis
from tests.analysis.test_prompt import candidate, packet
from tests.documents.test_evidence import document_graph


def identity(**updates):
    base = AnalysisCacheIdentity(
        paper_id="arxiv:2401.00001",
        pdf_sha256="a" * 64,
        document_schema_version="1.0",
        document_parser="docling",
        parser_version="2.113.0",
        mapper_version="1",
        document_config_version="1",
        content_fingerprint="b" * 64,
        packet_schema_version="1.0",
        evidence_builder_version="1",
        packet_fingerprint="c" * 64,
        prompt_version="stage3-v1",
        analysis_schema_version="1.0",
        model_identity="fake:model",
        generation_identity="d" * 64,
    )
    return base.model_copy(update=updates)


def cached_analysis(cache_identity=None):
    cache_identity = cache_identity or identity()
    current = analysis()
    generation = current.generation.model_copy(
        update={"cache_key": cache_identity.cache_key}
    )
    return current.model_copy(update={"generation": generation})


CHANGED = {
    "paper_id": "arxiv:2401.99999",
    "pdf_sha256": "f" * 64,
    "document_schema_version": "1.1",
    "document_parser": "other-parser",
    "parser_version": "2.114.0",
    "mapper_version": "2",
    "document_config_version": "2",
    "content_fingerprint": "e" * 64,
    "packet_schema_version": "1.1",
    "evidence_builder_version": "2",
    "packet_fingerprint": "9" * 64,
    "prompt_version": "stage3-v2",
    "analysis_schema_version": "1.1",
    "model_identity": "fake:other-model",
    "generation_identity": "8" * 64,
}


@pytest.mark.parametrize("field", tuple(CHANGED))
def test_cache_key_changes_for_every_required_identity(field):
    assert identity().cache_key != identity(**{field: CHANGED[field]}).cache_key


def test_build_identity_uses_document_packet_and_generation_versions():
    built = build_analysis_cache_identity(
        candidate(),
        document_graph(),
        packet(),
        prompt_version="stage3-v1",
        analysis_schema_version="1.0",
        model_identity="fake:model",
        generation_identity="d" * 64,
    )
    assert built.pdf_sha256 == document_graph().pdf.sha256
    assert built.content_fingerprint == document_graph().content_fingerprint
    assert built.packet_fingerprint == packet().packet_fingerprint
    assert built.parser_version == "2.113.0"


def test_cache_round_trip_is_atomic_validated_and_contained(tmp_path):
    cache = AnalysisCache(tmp_path / "cache")
    target = cache.write(identity(), cached_analysis())
    assert target.resolve().is_relative_to(cache.root.resolve())
    assert not tuple(target.parent.glob("*.tmp"))
    assert cache.read(identity()) == cached_analysis()


def test_cache_treats_corrupt_json_as_miss(tmp_path):
    cache = AnalysisCache(tmp_path / "cache")
    target = cache.path_for(identity())
    target.parent.mkdir(parents=True)
    target.write_text("not json", encoding="utf-8")
    assert cache.read(identity()) is None


def test_cache_rejects_envelope_stored_under_wrong_identity_key(tmp_path):
    cache = AnalysisCache(tmp_path / "cache")
    expected = identity()
    wrong = identity(prompt_version="stage3-v2")
    target = cache.path_for(expected)
    target.parent.mkdir(parents=True)
    envelope = AnalysisCacheEnvelope(identity=wrong, analysis=cached_analysis(wrong))
    target.write_text(envelope.model_dump_json(), encoding="utf-8")
    assert cache.read(expected) is None


def test_cache_rejects_tampered_analysis_payload(tmp_path):
    cache = AnalysisCache(tmp_path / "cache")
    target = cache.write(identity(), cached_analysis())
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["analysis"]["generation"]["cache_key"] = "0" * 64
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert cache.read(identity()) is None
