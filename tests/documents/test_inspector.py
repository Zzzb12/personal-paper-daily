from zotero_arxiv_daily.documents.inspector import inspect_pdf
from tests.fixtures.pdf_factory import corrupt_pdf, encrypted_pdf, image_only_pdf, text_pdf


def issue_codes(inspection):
    return {issue.code for issue in inspection.issues}


def test_inspector_preserves_one_based_pages_sizes_and_text_layer(tmp_path):
    inspection = inspect_pdf(text_pdf(tmp_path / "text.pdf", pages=2), max_pages=10)
    assert inspection.status == "ready"
    assert inspection.page_count == 2
    assert [page.pdf_page for page in inspection.pages] == [1, 2]
    assert all(page.width > 0 and page.height > 0 for page in inspection.pages)
    assert all(page.text_layer_status == "present" for page in inspection.pages)
    assert not inspection.issues


def test_inspector_marks_image_only_pdf_as_scanned_without_running_ocr(tmp_path):
    inspection = inspect_pdf(image_only_pdf(tmp_path / "scan.pdf"), max_pages=10)
    assert inspection.status == "scanned"
    assert inspection.pages[0].text_layer_status == "absent"
    assert {"no_text_layer", "scanned_document"} <= issue_codes(inspection)


def test_inspector_returns_explicit_corrupt_and_encrypted_failures(tmp_path):
    corrupt = inspect_pdf(corrupt_pdf(tmp_path / "corrupt.pdf"), max_pages=10)
    assert corrupt.status == "corrupt"
    assert "corrupt_pdf" in issue_codes(corrupt)

    encrypted = inspect_pdf(encrypted_pdf(tmp_path / "encrypted.pdf"), max_pages=10)
    assert encrypted.status == "encrypted"
    assert "encrypted_pdf" in issue_codes(encrypted)


def test_inspector_enforces_page_limit_before_parsing(tmp_path):
    inspection = inspect_pdf(text_pdf(tmp_path / "long.pdf", pages=3), max_pages=2)
    assert inspection.status == "page_limit_exceeded"
    assert "page_limit_exceeded" in issue_codes(inspection)
