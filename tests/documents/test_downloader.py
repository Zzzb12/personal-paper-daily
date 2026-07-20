from pathlib import Path

import httpx
import pytest

from zotero_arxiv_daily.documents.downloader import (
    PdfDownloadError,
    PdfDownloadPolicy,
    SafePdfDownloader,
)


PDF_BYTES = b"%PDF-1.4\nsynthetic fixture\n%%EOF\n"
URL = "https://arxiv.org/pdf/2401.00001?token=secret-value"


def downloader(tmp_path: Path, handler, **policy_overrides) -> SafePdfDownloader:
    policy_values = dict(
        connect_timeout=1,
        read_timeout=2,
        write_timeout=3,
        pool_timeout=4,
        max_attempts=3,
        backoff_seconds=0,
        max_bytes=1024,
    )
    policy_values.update(policy_overrides)
    policy = PdfDownloadPolicy(**policy_values)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SafePdfDownloader(tmp_path, policy=policy, client=client, sleep=lambda _: None)


def test_download_passes_explicit_timeout_and_writes_content_addressed_pdf(tmp_path):
    observed_timeout = {}

    def handler(request):
        observed_timeout.update(request.extensions["timeout"])
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=PDF_BYTES)

    result = downloader(tmp_path, handler).download(URL)

    assert observed_timeout == {"connect": 1, "read": 2, "write": 3, "pool": 4}
    assert result.path.read_bytes() == PDF_BYTES
    assert result.path.name == f"{result.sha256}.pdf"
    assert result.source_url == "https://arxiv.org/pdf/2401.00001"
    assert result.cache_hit is False
    assert not tuple(tmp_path.rglob("*.tmp"))


def test_download_retries_recoverable_status_a_finite_number_of_times(tmp_path):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, request=request)
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=PDF_BYTES)

    result = downloader(tmp_path, handler).download(URL)
    assert result.path.exists()
    assert attempts == 3


def test_download_does_not_retry_a_non_recoverable_client_error(tmp_path):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, request=request)

    with pytest.raises(PdfDownloadError) as failure:
        downloader(tmp_path, handler).download(URL)
    assert failure.value.code == "download_http_error"
    assert attempts == 1
    assert "secret-value" not in str(failure.value)


@pytest.mark.parametrize(
    ("content_type", "content", "expected_code"),
    [
        ("text/html", PDF_BYTES, "invalid_content_type"),
        ("application/pdf", b"not a pdf", "invalid_pdf_signature"),
    ],
)
def test_download_rejects_untrusted_response_content(
    tmp_path, content_type, content, expected_code
):
    def handler(request):
        return httpx.Response(200, headers={"content-type": content_type}, content=content)

    with pytest.raises(PdfDownloadError) as failure:
        downloader(tmp_path, handler).download(URL)
    assert failure.value.code == expected_code
    assert not tuple(tmp_path.rglob("*.pdf"))
    assert not tuple(tmp_path.rglob("*.tmp"))


def test_download_enforces_declared_and_streamed_size_limits(tmp_path):
    def declared_handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf", "content-length": "2048"},
            content=PDF_BYTES,
        )

    with pytest.raises(PdfDownloadError) as declared_failure:
        downloader(tmp_path / "declared", declared_handler).download(URL)
    assert declared_failure.value.code == "size_limit_exceeded"

    def streamed_handler(request):
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=PDF_BYTES + b"x" * 100
        )

    with pytest.raises(PdfDownloadError) as streamed_failure:
        downloader(tmp_path / "streamed", streamed_handler, max_bytes=32).download(URL)
    assert streamed_failure.value.code == "size_limit_exceeded"
    assert not tuple(tmp_path.rglob("*.tmp"))


def test_download_uses_validated_cache_without_a_second_request(tmp_path):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=PDF_BYTES)

    service = downloader(tmp_path, handler)
    first = service.download(URL)
    second = service.download(URL)

    assert attempts == 1
    assert first.path == second.path
    assert second.cache_hit is True
