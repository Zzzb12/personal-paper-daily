from .base import BaseRetriever, register_retriever
import arxiv
from arxiv import Result as ArxivResult
from ..protocol import Paper
from ..utils import extract_markdown_from_pdf, extract_tex_code_from_tar
from tempfile import TemporaryDirectory
import feedparser
import hashlib
import json
from tqdm import tqdm
import multiprocessing
import os
from pathlib import Path
from queue import Empty
from time import sleep
from typing import Any, Callable, Protocol, TypeVar
from loguru import logger
import requests
import httpx
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from collections.abc import Callable
from pydantic import Field, field_validator, model_validator

from ..analysis.schemas import CandidatePaper, StrictModel
from ..pipeline.artifacts import atomic_write_bytes

T = TypeVar("T")

DOWNLOAD_TIMEOUT = (10, 60)
PDF_EXTRACT_TIMEOUT = 180
TAR_EXTRACT_TIMEOUT = 180


class ArxivMetadataEntry(StrictModel):
    arxiv_id: str
    version: int
    title: str
    authors: tuple[str, ...]
    abstract: str
    categories: tuple[str, ...]
    primary_category: str
    published_at: datetime
    updated_at: datetime
    arxiv_url: str
    pdf_url: str
    code_url: str | None = None

    @field_validator("arxiv_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        value = value.strip().removeprefix("arxiv:")
        if re.search(r"v\d+$", value, re.IGNORECASE):
            raise ValueError("arxiv_id must not include a version suffix")
        return value

    @model_validator(mode="after")
    def validate_as_candidate(self):
        self.to_candidate()
        return self

    def to_candidate(self) -> CandidatePaper:
        return CandidatePaper(
            paper_id=f"arxiv:{self.arxiv_id}",
            arxiv_id=self.arxiv_id,
            version=self.version,
            title=self.title,
            authors=self.authors,
            abstract=self.abstract,
            categories=self.categories,
            primary_category=self.primary_category,
            published_at=self.published_at,
            updated_at=self.updated_at,
            arxiv_url=self.arxiv_url,
            pdf_url=self.pdf_url,
            code_url=self.code_url,
        )


class ArxivMetadataGateway(Protocol):
    def retrieve_entries(
        self, categories: tuple[str, ...], include_cross_list: bool
    ) -> tuple[ArxivMetadataEntry, ...]:
        raise NotImplementedError


class ArxivMetadataResult(StrictModel):
    candidates: tuple[CandidatePaper, ...]
    retrieved_count: int
    deduplicated_count: int
    invalid_count: int = 0


class ArxivMetadataRetriever:
    def __init__(
        self,
        gateway: ArxivMetadataGateway,
        *,
        categories: tuple[str, ...],
        include_cross_list: bool = False,
    ) -> None:
        self._gateway = gateway
        self._categories = categories
        self._include_cross_list = include_cross_list

    def retrieve(self) -> ArxivMetadataResult:
        entries = self._gateway.retrieve_entries(self._categories, self._include_cross_list)
        latest: dict[str, ArxivMetadataEntry] = {}
        for entry in entries:
            previous = latest.get(entry.arxiv_id)
            if previous is None or (entry.version, entry.updated_at) > (
                previous.version,
                previous.updated_at,
            ):
                latest[entry.arxiv_id] = entry
        candidates = tuple(latest[key].to_candidate() for key in sorted(latest))
        return ArxivMetadataResult(
            candidates=candidates,
            retrieved_count=getattr(self._gateway, "retrieved_count", len(entries)),
            deduplicated_count=len(candidates),
            invalid_count=getattr(self._gateway, "invalid_count", 0),
        )


class ArxivRetryPolicy(StrictModel):
    max_attempts: int = 3
    backoff_seconds: float = 1
    max_retry_after_seconds: float = 60


class _ArxivMetadataCacheEnvelope(StrictModel):
    schema_version: str
    identity_hash: str
    created_at: datetime
    retrieved_count: int = Field(ge=0)
    invalid_count: int = Field(ge=0)
    entries: tuple[ArxivMetadataEntry, ...]
    payload_hash: str

    @field_validator("created_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("metadata cache time must be timezone-aware")
        return value.astimezone(UTC)


class HttpArxivMetadataGateway:
    API_URL = "https://export.arxiv.org/api/query"
    RSS_BASE_URL = "https://rss.arxiv.org/atom"
    CACHE_SCHEMA_VERSION = "arxiv-metadata-v1"
    MAX_RESULTS = 200
    MAX_RESPONSE_BYTES = 8 * 1024 * 1024
    MAX_CACHE_BYTES = 8 * 1024 * 1024
    DEFAULT_STALE_TTL = timedelta(days=7)
    _CATEGORY_RE = re.compile(r"^[A-Za-z0-9.-]+$")
    _ID_RE = re.compile(r"(?P<base>(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))(?:v(?P<version>\d+))?$", re.I)

    def __init__(
        self,
        client: httpx.Client,
        *,
        retry_policy: ArxivRetryPolicy | None = None,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cache_root: Path | None = None,
        stale_ttl: timedelta = DEFAULT_STALE_TTL,
        owns_client: bool = False,
    ) -> None:
        if stale_ttl <= timedelta(0):
            raise ValueError("metadata cache stale TTL must be positive")
        self._client = client
        self._retry = retry_policy or ArxivRetryPolicy()
        self._sleeper = sleeper
        self._clock = clock
        self._cache_root = Path(cache_root) if cache_root is not None else None
        self._stale_ttl = stale_ttl
        self._owns_client = owns_client
        self.invalid_count = 0
        self.retrieved_count = 0

    @classmethod
    def from_defaults(
        cls,
        *,
        timeout: httpx.Timeout | None = None,
        retry_policy: ArxivRetryPolicy | None = None,
        cache_root: Path | None = Path("cache/arxiv-metadata"),
    ) -> "HttpArxivMetadataGateway":
        client = httpx.Client(
            timeout=timeout or httpx.Timeout(connect=10, read=30, write=10, pool=10),
            headers={
                "Accept": "application/atom+xml",
                "User-Agent": (
                    "personal-paper-daily/1.0 "
                    "(+https://github.com/Zzzb12/personal-paper-daily)"
                ),
            },
        )
        return cls(
            client,
            retry_policy=retry_policy,
            cache_root=cache_root,
            owns_client=True,
        )

    @staticmethod
    def _is_transient(error: Exception) -> bool:
        if isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            return status in {408, 425, 429} or status >= 500
        return False

    def _get(
        self,
        params: dict[str, str | int],
        *,
        url: str = API_URL,
        retry_rate_limits: bool = True,
    ) -> httpx.Response:
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                response = self._client.get(url, params=params or None)
                response.raise_for_status()
                if len(response.content) > self.MAX_RESPONSE_BYTES:
                    raise ValueError("arXiv metadata response exceeds the byte limit")
                return response
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    logger.warning(
                        "arXiv metadata HTTP failure: source={} status={}",
                        "api" if url == self.API_URL else "rss",
                        exc.response.status_code,
                    )
                transient = self._is_transient(exc)
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code == 429
                    and not retry_rate_limits
                ):
                    transient = False
                if not transient or attempt == self._retry.max_attempts:
                    raise
                fallback = self._retry.backoff_seconds * attempt
                raw_retry_after = exc.response.headers.get("Retry-After") if isinstance(exc, httpx.HTTPStatusError) else None
                delay = fallback
                if raw_retry_after:
                    try:
                        delay = float(raw_retry_after)
                    except ValueError:
                        try:
                            retry_at = parsedate_to_datetime(raw_retry_after)
                            if retry_at.tzinfo is None:
                                retry_at = retry_at.replace(tzinfo=UTC)
                            delay = max(0.0, (retry_at - self._clock().astimezone(UTC)).total_seconds())
                        except (TypeError, ValueError, OverflowError):
                            delay = fallback
                self._sleeper(min(max(delay, 0), self._retry.max_retry_after_seconds))
        raise RuntimeError("unreachable retry loop")

    @staticmethod
    def _canonical_json(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def _identity_hash(
        cls,
        categories: tuple[str, ...],
        include_cross_list: bool,
    ) -> str:
        identity = {
            "categories": list(categories),
            "gateway_version": cls.CACHE_SCHEMA_VERSION,
            "include_cross_list": include_cross_list,
            "max_results": cls.MAX_RESULTS,
        }
        return hashlib.sha256(cls._canonical_json(identity)).hexdigest()

    @classmethod
    def _payload_hash(
        cls,
        entries: tuple[ArxivMetadataEntry, ...],
        retrieved_count: int,
        invalid_count: int,
    ) -> str:
        payload = {
            "entries": [
                entry.model_dump(mode="json")
                for entry in entries
            ],
            "invalid_count": invalid_count,
            "retrieved_count": retrieved_count,
        }
        return hashlib.sha256(cls._canonical_json(payload)).hexdigest()

    def _cache_path(
        self,
        categories: tuple[str, ...],
        include_cross_list: bool,
    ) -> Path | None:
        if self._cache_root is None:
            return None
        return self._cache_root / (
            f"{self._identity_hash(categories, include_cross_list)}.json"
        )

    def _read_cache(
        self,
        categories: tuple[str, ...],
        include_cross_list: bool,
        *,
        allow_stale: bool,
    ) -> tuple[ArxivMetadataEntry, ...] | None:
        path = self._cache_path(categories, include_cross_list)
        if path is None:
            return None
        try:
            if path.stat().st_size > self.MAX_CACHE_BYTES:
                return None
            envelope = _ArxivMetadataCacheEnvelope.model_validate_json(
                path.read_bytes()
            )
        except (OSError, ValueError):
            return None
        identity_hash = self._identity_hash(categories, include_cross_list)
        if (
            envelope.schema_version != self.CACHE_SCHEMA_VERSION
            or envelope.identity_hash != identity_hash
            or envelope.payload_hash
            != self._payload_hash(
                envelope.entries,
                envelope.retrieved_count,
                envelope.invalid_count,
            )
        ):
            return None
        now = self._clock().astimezone(UTC)
        age = now - envelope.created_at
        if age < timedelta(0):
            return None
        if allow_stale:
            if age > self._stale_ttl:
                return None
        elif envelope.created_at.date() != now.date():
            return None
        self.retrieved_count = envelope.retrieved_count
        self.invalid_count = envelope.invalid_count
        return envelope.entries

    def _write_cache(
        self,
        categories: tuple[str, ...],
        include_cross_list: bool,
        entries: tuple[ArxivMetadataEntry, ...],
    ) -> None:
        path = self._cache_path(categories, include_cross_list)
        if path is None:
            return
        envelope = _ArxivMetadataCacheEnvelope(
            schema_version=self.CACHE_SCHEMA_VERSION,
            identity_hash=self._identity_hash(categories, include_cross_list),
            created_at=self._clock(),
            retrieved_count=self.retrieved_count,
            invalid_count=self.invalid_count,
            entries=entries,
            payload_hash=self._payload_hash(
                entries,
                self.retrieved_count,
                self.invalid_count,
            ),
        )
        encoded = (envelope.model_dump_json() + "\n").encode("utf-8")
        if len(encoded) > self.MAX_CACHE_BYTES:
            return
        try:
            atomic_write_bytes(path, encoded)
        except OSError:
            # Public metadata cache persistence must not make a valid run fail.
            return

    @classmethod
    def _parse_entry(cls, raw: Any) -> ArxivMetadataEntry:
        match = cls._ID_RE.search(str(raw.id))
        if match is None:
            raise ValueError("invalid arXiv identifier")
        base = match.group("base")
        version = int(match.group("version") or 1)
        categories = tuple(sorted({tag.term for tag in raw.get("tags", ())}))
        primary_data = raw.get("arxiv_primary_category", {})
        primary = primary_data.get("term") if hasattr(primary_data, "get") else None
        if not primary:
            primary = categories[0]
        pdf_url = next(
            (
                link.href
                for link in raw.get("links", ())
                if link.get("type") == "application/pdf" or link.get("title") == "pdf"
            ),
            f"https://arxiv.org/pdf/{base}v{version}",
        )
        return ArxivMetadataEntry(
            arxiv_id=base,
            version=version,
            title=raw.title,
            authors=tuple(author.name for author in raw.get("authors", ())),
            abstract=raw.summary,
            categories=categories,
            primary_category=primary,
            published_at=datetime.fromisoformat(raw.published.replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(raw.updated.replace("Z", "+00:00")),
            arxiv_url=f"https://arxiv.org/abs/{base}v{version}",
            pdf_url=pdf_url,
        )

    def _parse_feed(
        self,
        content: bytes,
        categories: tuple[str, ...],
        include_cross_list: bool,
        *,
        limit: int | None = None,
    ) -> tuple[ArxivMetadataEntry, ...]:
        feed = feedparser.parse(content)
        raw_entries = tuple(feed.entries)
        if limit is not None:
            raw_entries = raw_entries[:limit]
        parsed_items: list[ArxivMetadataEntry] = []
        invalid_count = 0
        for raw in raw_entries:
            try:
                parsed_items.append(self._parse_entry(raw))
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                invalid_count += 1
        parsed = tuple(parsed_items)
        self.retrieved_count = len(raw_entries)
        self.invalid_count = invalid_count
        if include_cross_list:
            return parsed
        allowed = set(categories)
        return tuple(
            entry
            for entry in parsed
            if entry.primary_category in allowed
        )

    def _retrieve_from_rss(
        self,
        categories: tuple[str, ...],
        include_cross_list: bool,
    ) -> tuple[ArxivMetadataEntry, ...]:
        if not categories or any(
            self._CATEGORY_RE.fullmatch(category) is None
            for category in categories
        ):
            raise ValueError("invalid arXiv RSS category")
        latest: dict[str, ArxivMetadataEntry] = {}
        retrieved_count = 0
        invalid_count = 0
        for category in categories:
            response = self._get(
                {},
                url=f"{self.RSS_BASE_URL}/{category}",
            )
            category_entries = self._parse_feed(
                response.content,
                (category,),
                include_cross_list=True,
            )
            retrieved_count += self.retrieved_count
            invalid_count += self.invalid_count
            for entry in category_entries:
                previous = latest.get(entry.arxiv_id)
                if previous is None or (
                    entry.version,
                    entry.updated_at,
                ) > (
                    previous.version,
                    previous.updated_at,
                ):
                    latest[entry.arxiv_id] = entry
        ordered = tuple(
            sorted(
                latest.values(),
                key=lambda entry: (
                    entry.published_at,
                    entry.updated_at,
                    entry.arxiv_id,
                ),
                reverse=True,
            )[: self.MAX_RESULTS]
        )
        self.retrieved_count = min(retrieved_count, self.MAX_RESULTS)
        self.invalid_count = min(invalid_count, self.retrieved_count)
        if include_cross_list:
            return ordered
        allowed = set(categories)
        return tuple(
            entry
            for entry in ordered
            if entry.primary_category in allowed
        )

    def retrieve_entries(
        self, categories: tuple[str, ...], include_cross_list: bool
    ) -> tuple[ArxivMetadataEntry, ...]:
        cached = self._read_cache(
            categories,
            include_cross_list,
            allow_stale=False,
        )
        if cached is not None:
            return cached
        query = " OR ".join(f"cat:{category}" for category in categories)
        primary_error: Exception | None = None
        try:
            response = self._get(
                {
                    "search_query": query,
                    "start": 0,
                    "max_results": self.MAX_RESULTS,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
                retry_rate_limits=False,
            )
            entries = self._parse_feed(
                response.content,
                categories,
                include_cross_list,
            )
        except (
            httpx.TimeoutException,
            httpx.TransportError,
            httpx.HTTPStatusError,
        ) as error:
            if not self._is_transient(error):
                raise
            primary_error = error
            try:
                entries = self._retrieve_from_rss(
                    categories,
                    include_cross_list,
                )
            except (
                httpx.TimeoutException,
                httpx.TransportError,
                httpx.HTTPStatusError,
                ValueError,
            ):
                stale = self._read_cache(
                    categories,
                    include_cross_list,
                    allow_stale=True,
                )
                if stale is not None:
                    return stale
                raise primary_error from None
        self._write_cache(
            categories,
            include_cross_list,
            entries,
        )
        return entries

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
            self._owns_client = False


def _download_file(url: str, path: str) -> None:
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        with open(path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def _run_in_subprocess(
    result_queue: Any,
    func: Callable[..., T | None],
    args: tuple[Any, ...],
) -> None:
    try:
        result_queue.put(("ok", func(*args)))
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _run_with_hard_timeout(
    func: Callable[..., T | None],
    args: tuple[Any, ...],
    *,
    timeout: float,
    operation: str,
    paper_title: str,
) -> T | None:
    start_methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("fork" if "fork" in start_methods else start_methods[0])
    result_queue = context.Queue()
    process = context.Process(target=_run_in_subprocess, args=(result_queue, func, args))
    process.start()

    try:
        status, payload = result_queue.get(timeout=timeout)
    except Empty:
        if process.is_alive():
            process.kill()
        process.join(5)
        result_queue.close()
        result_queue.join_thread()
        logger.warning(f"{operation} timed out for {paper_title} after {timeout} seconds")
        return None

    process.join(5)
    result_queue.close()
    result_queue.join_thread()

    if status == "ok":
        return payload

    logger.warning(f"{operation} failed for {paper_title}: {payload}")
    return None


def _extract_text_from_pdf_worker(pdf_url: str) -> str:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.pdf")
        _download_file(pdf_url, path)
        return extract_markdown_from_pdf(path)


def _extract_text_from_html_worker(html_url: str) -> str | None:
    import trafilatura

    downloaded = trafilatura.fetch_url(html_url)
    if downloaded is None:
        raise ValueError(f"Failed to download HTML from {html_url}")
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    if not text:
        raise ValueError(f"No text extracted from {html_url}")
    return text


def _extract_text_from_tar_worker(source_url: str, paper_id: str, paper_title: str | None = None) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.tar.gz")
        _download_file(source_url, path)
        file_contents = extract_tex_code_from_tar(path, paper_id, paper_title=paper_title)
        if not file_contents or "all" not in file_contents:
            raise ValueError("Main tex file not found.")
        return file_contents["all"]


@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")

    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        client = arxiv.Client(num_retries=10, delay_seconds=10)
        query = '+'.join(self.config.source.arxiv.category)
        include_cross_list = self.config.source.arxiv.get("include_cross_list", False)
        # Get the latest paper from arxiv rss feed
        feed = feedparser.parse(f"https://rss.arxiv.org/atom/{query}")
        if 'Feed error for query' in feed.feed.title:
            raise Exception(f"Invalid ARXIV_QUERY: {query}.")
        raw_papers = []
        allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}
        all_paper_ids = [
            i.id.removeprefix("oai:arXiv.org:")
            for i in feed.entries
            if i.get("arxiv_announce_type", "new") in allowed_announce_types
        ]
        if self.config.executor.debug:
            all_paper_ids = all_paper_ids[:10]

        # Get full information of each paper from arxiv api
        bar = tqdm(total=len(all_paper_ids))
        max_batch_retries = 5
        batch_retry_delay = 30
        for i in range(0, len(all_paper_ids), 20):
            search = arxiv.Search(id_list=all_paper_ids[i:i + 20])
            for attempt in range(max_batch_retries):
                try:
                    batch = list(client.results(search))
                    bar.update(len(batch))
                    raw_papers.extend(batch)
                    break
                except arxiv.HTTPError as exc:
                    if exc.status == 429 and attempt < max_batch_retries - 1:
                        wait = batch_retry_delay * (attempt + 1)
                        logger.warning(f"arXiv API 429 on batch {i // 20}, retry {attempt + 1}/{max_batch_retries} in {wait}s")
                        sleep(wait)
                    else:
                        raise
            if i + 20 < len(all_paper_ids):
                sleep(3)
        bar.close()

        return raw_papers

    def convert_to_paper(self, raw_paper: ArxivResult) -> Paper:
        title = raw_paper.title
        authors = [a.name for a in raw_paper.authors]
        abstract = raw_paper.summary
        pdf_url = raw_paper.pdf_url
        full_text = extract_text_from_tar(raw_paper)
        if full_text is None:
            full_text = extract_text_from_html(raw_paper)
        if full_text is None:
            full_text = extract_text_from_pdf(raw_paper)
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=raw_paper.entry_id,
            pdf_url=pdf_url,
            full_text=full_text,
        )


def extract_text_from_html(paper: ArxivResult) -> str | None:
    html_url = paper.entry_id.replace("/abs/", "/html/")
    try:
        return _extract_text_from_html_worker(html_url)
    except Exception as exc:
        logger.warning(f"HTML extraction failed for {paper.title}: {exc}")
        return None


def extract_text_from_pdf(paper: ArxivResult) -> str | None:
    if paper.pdf_url is None:
        logger.warning(f"No PDF URL available for {paper.title}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_pdf_worker,
        (paper.pdf_url,),
        timeout=PDF_EXTRACT_TIMEOUT,
        operation="PDF extraction",
        paper_title=paper.title,
    )


def extract_text_from_tar(paper: ArxivResult) -> str | None:
    source_url = paper.source_url()
    if source_url is None:
        logger.warning(f"No source URL available for {paper.title}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_tar_worker,
        (source_url, paper.entry_id, paper.title),
        timeout=TAR_EXTRACT_TIMEOUT,
        operation="Tar extraction",
        paper_title=paper.title,
    )
