from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from zotero_arxiv_daily.documents.parser import DoclingDocumentParser
from tests.fixtures.pdf_factory import text_pdf


@dataclass
class FakeBBox:
    l: float
    t: float
    r: float
    b: float


def fake_document():
    heading = SimpleNamespace(
        self_ref="#/texts/0",
        label=SimpleNamespace(value="section_header"),
        text="1 Method",
        parent=None,
        prov=(SimpleNamespace(page_no=1, bbox=FakeBBox(72, 90, 300, 70)),),
        captions=(),
    )
    paragraph = SimpleNamespace(
        self_ref="#/texts/1",
        label=SimpleNamespace(value="text"),
        text="Method body",
        parent=SimpleNamespace(cref="#/texts/0"),
        prov=(SimpleNamespace(page_no=1, bbox=FakeBBox(72, 140, 500, 110)),),
        captions=(),
    )
    table = SimpleNamespace(
        self_ref="#/tables/0",
        label=SimpleNamespace(value="table"),
        text="",
        parent=SimpleNamespace(cref="#/texts/0"),
        prov=(
            SimpleNamespace(page_no=1, bbox=FakeBBox(72, 500, 500, 200)),
            SimpleNamespace(page_no=2, bbox=FakeBBox(72, 300, 500, 50)),
        ),
        captions=(SimpleNamespace(cref="#/texts/2"),),
    )
    return SimpleNamespace(iterate_items=lambda **kwargs: iter(((heading, 0), (paragraph, 1), (table, 1))))


class FakeConverter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def convert(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.result


def test_parser_requires_prepared_local_models_before_constructing_converter(tmp_path):
    constructed = False

    def factory(config):
        nonlocal constructed
        constructed = True
        raise AssertionError("must not construct converter")

    parser = DoclingDocumentParser(
        artifacts_path=tmp_path / "missing-models", converter_factory=factory
    )
    result = parser.parse(text_pdf(tmp_path / "paper.pdf"), max_pages=10, max_file_size=1024 * 1024)
    assert result.status == "failed"
    assert result.issues[0].code == "parser_model_unavailable"
    assert constructed is False


def test_parser_disables_ocr_and_vlm_and_preserves_docling_provenance(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    converter = FakeConverter(SimpleNamespace(status="success", document=fake_document(), errors=()))
    observed_config = None

    def factory(config):
        nonlocal observed_config
        observed_config = config
        return converter

    parser = DoclingDocumentParser(
        artifacts_path=models,
        converter_factory=factory,
        document_timeout_seconds=12,
    )
    pdf = text_pdf(tmp_path / "paper.pdf", pages=2)
    result = parser.parse(pdf, max_pages=10, max_file_size=1024 * 1024)

    assert result.status == "success"
    assert observed_config.do_ocr is False
    assert observed_config.pipeline == "standard"
    assert observed_config.allow_remote_services is False
    assert observed_config.document_timeout_seconds == 12
    assert converter.calls == [(pdf, {"raises_on_error": False, "max_num_pages": 10, "max_file_size": 1024 * 1024})]
    assert result.document.parser_version == "2.113.0"
    table = result.document.items[2]
    assert table.item_id == "#/tables/0"
    assert table.label == "table"
    assert [source.pdf_page for source in table.provenance] == [1, 2]
    assert table.caption_refs == ("#/texts/2",)


def test_parser_translates_partial_and_failed_conversion_states(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    partial = DoclingDocumentParser(
        artifacts_path=models,
        converter_factory=lambda config: FakeConverter(
            SimpleNamespace(status="partial_success", document=fake_document(), errors=("page 2",))
        ),
    ).parse(text_pdf(tmp_path / "partial.pdf"), max_pages=10, max_file_size=1024 * 1024)
    assert partial.status == "partial"
    assert partial.issues[0].code == "parser_partial"

    failed = DoclingDocumentParser(
        artifacts_path=models,
        converter_factory=lambda config: FakeConverter(
            SimpleNamespace(status="failure", document=None, errors=("conversion failed",))
        ),
    ).parse(text_pdf(tmp_path / "failed.pdf"), max_pages=10, max_file_size=1024 * 1024)
    assert failed.status == "failed"
    assert failed.document is None
    assert failed.issues[0].code == "parser_failed"

    timed_out = DoclingDocumentParser(
        artifacts_path=models,
        converter_factory=lambda config: FakeConverter(
            SimpleNamespace(status="failure", document=None, errors=("document timeout",))
        ),
    ).parse(text_pdf(tmp_path / "timeout.pdf"), max_pages=10, max_file_size=1024 * 1024)
    assert timed_out.issues[0].code == "parser_timeout"


def test_parser_rejects_urls_so_download_policy_cannot_be_bypassed(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    parser = DoclingDocumentParser(artifacts_path=models, converter_factory=lambda config: None)
    result = parser.parse(Path("https:/arxiv.org/paper.pdf"), max_pages=10, max_file_size=100)
    assert result.status == "failed"
    assert result.issues[0].code == "parser_input_not_local"
