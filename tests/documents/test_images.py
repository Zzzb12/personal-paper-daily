from zotero_arxiv_daily.documents.images import extract_evidence_images
from tests.analysis.test_document_schemas import graph
from tests.fixtures.pdf_factory import text_pdf


def test_evidence_extractor_writes_deterministic_pngs_for_every_visual_region(tmp_path):
    pdf_path = text_pdf(tmp_path / "paper.pdf", pages=2)
    document = graph()
    document = document.model_copy(
        update={"pdf": document.pdf.model_copy(update={"local_path": pdf_path})}
    )

    first = extract_evidence_images(document, tmp_path / "evidence", scale=1.5)
    second = extract_evidence_images(document, tmp_path / "evidence", scale=1.5)

    first_paths = [region.image_path for region in first.visuals[0].regions]
    second_paths = [region.image_path for region in second.visuals[0].regions]
    assert first_paths == second_paths
    signatures = [path.read_bytes()[:8] if path is not None else None for path in first_paths]
    assert signatures == [b"\x89PNG\r\n\x1a\n", b"\x89PNG\r\n\x1a\n"], [
        (issue.code, issue.message) for issue in first.visuals[0].issues
    ]
    assert all(path.resolve().is_relative_to((tmp_path / "evidence").resolve()) for path in first_paths)


def test_evidence_extractor_returns_issue_when_pdf_cannot_be_rendered(tmp_path):
    document = graph()
    document = document.model_copy(
        update={"pdf": document.pdf.model_copy(update={"local_path": tmp_path / "missing.pdf"})}
    )
    extracted = extract_evidence_images(document, tmp_path / "evidence")
    assert all(region.image_path is None for region in extracted.visuals[0].regions)
    assert "visual_image_not_extracted" in {
        issue.code for issue in extracted.visuals[0].issues
    }
