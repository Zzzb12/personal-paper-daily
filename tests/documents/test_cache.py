from zotero_arxiv_daily.documents.cache import DocumentGraphCache
from tests.analysis.test_document_schemas import graph


def test_document_cache_round_trips_only_an_exact_version_match(tmp_path):
    document = graph()
    cache = DocumentGraphCache(tmp_path)
    stored = cache.write(document)

    assert cache.read(
        pdf_sha256=document.pdf.sha256,
        parser_version=document.parser_version,
        mapper_version=document.mapper_version,
        config_version=document.config_version,
    ) == document
    assert cache.read(
        pdf_sha256=document.pdf.sha256,
        parser_version="different",
        mapper_version=document.mapper_version,
        config_version=document.config_version,
    ) is None
    assert stored.path.resolve().is_relative_to(tmp_path.resolve())
    assert not tuple(tmp_path.rglob("*.tmp"))


def test_document_cache_treats_corrupt_json_as_a_miss(tmp_path):
    document = graph()
    cache = DocumentGraphCache(tmp_path)
    stored = cache.write(document)
    stored.path.write_text("not json", encoding="utf-8")

    assert cache.read(
        pdf_sha256=document.pdf.sha256,
        parser_version=document.parser_version,
        mapper_version=document.mapper_version,
        config_version=document.config_version,
    ) is None
