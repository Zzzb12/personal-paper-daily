from zotero_arxiv_daily.documents.cache import DocumentGraphCache
from tests.analysis.test_document_schemas import graph
from PIL import Image


def graph_without_images():
    document = graph()
    visuals = tuple(
        visual.model_copy(
            update={
                "regions": tuple(
                    region.model_copy(update={"image_path": None}) for region in visual.regions
                )
            }
        )
        for visual in document.visuals
    )
    return document.model_copy(update={"visuals": visuals, "evidence_root": None})


def test_document_cache_round_trips_only_an_exact_version_match(tmp_path):
    document = graph_without_images()
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
    document = graph_without_images()
    cache = DocumentGraphCache(tmp_path)
    stored = cache.write(document)
    stored.path.write_text("not json", encoding="utf-8")

    assert cache.read(
        pdf_sha256=document.pdf.sha256,
        parser_version=document.parser_version,
        mapper_version=document.mapper_version,
        config_version=document.config_version,
    ) is None


def test_document_cache_treats_missing_or_corrupt_evidence_image_as_a_miss(tmp_path):
    document = graph()
    evidence_root = tmp_path / "cache" / "evidence"
    evidence_root.mkdir(parents=True)
    bad_image = evidence_root / "bad.png"
    bad_image.write_bytes(b"not png")
    visuals = tuple(
        visual.model_copy(
            update={
                "regions": tuple(
                    region.model_copy(update={"image_path": bad_image}) for region in visual.regions
                )
            }
        )
        for visual in document.visuals
    )
    document = document.model_copy(update={"visuals": visuals, "evidence_root": evidence_root})
    cache = DocumentGraphCache(tmp_path / "cache")
    cache.write(document)
    assert cache.read(
        pdf_sha256=document.pdf.sha256,
        parser_version=document.parser_version,
        mapper_version=document.mapper_version,
        config_version=document.config_version,
    ) is None


def test_document_cache_rejects_graph_declaring_evidence_root_outside_cache(tmp_path):
    document = graph()
    external_root = tmp_path / "external"
    external_root.mkdir()
    external_png = external_root / "valid.png"
    Image.new("RGB", (2, 2), "white").save(external_png)
    visuals = tuple(
        visual.model_copy(
            update={
                "regions": tuple(
                    region.model_copy(update={"image_path": external_png})
                    for region in visual.regions
                )
            }
        )
        for visual in document.visuals
    )
    document = document.model_copy(
        update={"visuals": visuals, "evidence_root": external_root}
    )
    cache = DocumentGraphCache(tmp_path / "cache")
    cache.write(document)
    assert cache.read(
        pdf_sha256=document.pdf.sha256,
        parser_version=document.parser_version,
        mapper_version=document.mapper_version,
        config_version=document.config_version,
    ) is None
