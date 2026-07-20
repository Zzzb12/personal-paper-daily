from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import Field

from zotero_arxiv_daily.analysis.schemas import StrictModel


_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_PDF_CONTENT_TYPES = {"application/pdf", "application/octet-stream", "binary/octet-stream"}


class PdfDownloadPolicy(StrictModel):
    connect_timeout: float = Field(default=10, gt=0)
    read_timeout: float = Field(default=30, gt=0)
    write_timeout: float = Field(default=10, gt=0)
    pool_timeout: float = Field(default=10, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=5)
    backoff_seconds: float = Field(default=1, ge=0, le=60)
    max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)

    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )


@dataclass(frozen=True)
class DownloadedPdf:
    source_url: str
    path: Path
    sha256: str
    byte_size: int
    content_type: str
    cache_hit: bool


class PdfDownloadError(RuntimeError):
    def __init__(self, code: str, message: str, *, source_url: str) -> None:
        self.code = code
        self.source_url = source_url
        super().__init__(f"{code}: {message} ({source_url})")


class SafePdfDownloader:
    def __init__(
        self,
        root: Path,
        *,
        policy: PdfDownloadPolicy | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.root = Path(root)
        self.policy = policy or PdfDownloadPolicy()
        self.client = client or httpx.Client(follow_redirects=True)
        self._owns_client = client is None
        self.sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> SafePdfDownloader:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def download(self, url: str) -> DownloadedPdf:
        source_url = _sanitize_url(url)
        cached = self._read_cache(source_url)
        if cached is not None:
            return cached

        last_retryable_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return self._download_once(url, source_url)
            except PdfDownloadError as exc:
                if exc.code != "download_retryable_http_error":
                    raise
                last_retryable_error = exc
            except httpx.TransportError as exc:
                last_retryable_error = exc
            if attempt < self.policy.max_attempts:
                self.sleep(self.policy.backoff_seconds * attempt)

        raise PdfDownloadError(
            "download_retry_exhausted",
            f"download failed after {self.policy.max_attempts} attempts: "
            f"{type(last_retryable_error).__name__}",
            source_url=source_url,
        )

    def _download_once(self, url: str, source_url: str) -> DownloadedPdf:
        temporary_root = self.root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        temporary = temporary_root / f"{uuid.uuid4().hex}.tmp"
        try:
            with self.client.stream("GET", url, timeout=self.policy.timeout()) as response:
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    raise PdfDownloadError(
                        "download_retryable_http_error",
                        f"HTTP {response.status_code}",
                        source_url=source_url,
                    )
                if response.is_error:
                    raise PdfDownloadError(
                        "download_http_error",
                        f"HTTP {response.status_code}",
                        source_url=source_url,
                    )
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in _PDF_CONTENT_TYPES:
                    raise PdfDownloadError(
                        "invalid_content_type",
                        f"unexpected response type {content_type or 'missing'}",
                        source_url=source_url,
                    )
                declared_size = _content_length(response.headers.get("content-length"))
                if declared_size is not None and declared_size > self.policy.max_bytes:
                    raise PdfDownloadError(
                        "size_limit_exceeded",
                        "declared response size exceeds the configured limit",
                        source_url=source_url,
                    )

                digest = hashlib.sha256()
                byte_size = 0
                with temporary.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        byte_size += len(chunk)
                        if byte_size > self.policy.max_bytes:
                            raise PdfDownloadError(
                                "size_limit_exceeded",
                                "streamed response exceeds the configured limit",
                                source_url=source_url,
                            )
                        digest.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())

            if temporary.read_bytes()[:5] != b"%PDF-":
                raise PdfDownloadError(
                    "invalid_pdf_signature",
                    "response does not start with the PDF signature",
                    source_url=source_url,
                )
            sha256 = digest.hexdigest()
            target = self.root / "pdf" / sha256[:2] / f"{sha256}.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, target)
            result = DownloadedPdf(
                source_url=source_url,
                path=target,
                sha256=sha256,
                byte_size=byte_size,
                content_type=content_type,
                cache_hit=False,
            )
            self._write_cache(result)
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def _cache_index_path(self, source_url: str) -> Path:
        key = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
        return self.root / "index" / f"{key}.json"

    def _read_cache(self, source_url: str) -> DownloadedPdf | None:
        index_path = self._cache_index_path(source_url)
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            sha256 = str(data["sha256"])
            if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
                return None
            path = self.root / "pdf" / sha256[:2] / f"{sha256}.pdf"
            content = path.read_bytes()
            if not content.startswith(b"%PDF-") or hashlib.sha256(content).hexdigest() != sha256:
                return None
            return DownloadedPdf(
                source_url=source_url,
                path=path,
                sha256=sha256,
                byte_size=len(content),
                content_type=str(data["content_type"]),
                cache_hit=True,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, result: DownloadedPdf) -> None:
        index_path = self._cache_index_path(result.source_url)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = index_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        payload = json.dumps(
            {"sha256": result.sha256, "content_type": result.content_type},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, index_path)
        finally:
            temporary.unlink(missing_ok=True)


def _sanitize_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError:
        return None
    return length if length >= 0 else None
