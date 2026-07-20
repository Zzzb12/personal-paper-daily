from __future__ import annotations

import hashlib
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from openai import OpenAI
from pydantic import Field, field_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel


_RETRYABLE_STATUSES = {408, 409, 425, 429}


class AnalysisRequest(StrictModel):
    prompt_version: str
    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any]
    max_output_tokens: int = Field(gt=0)

    @field_validator("prompt_version", "system_prompt", "user_prompt")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("analysis request text must not be blank")
        return value


class StructuredAnalysisClient(Protocol):
    model_identity: str

    def generate(self, request: AnalysisRequest) -> str:
        raise NotImplementedError


class AnalysisClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class OpenAICompatibleAnalysisClient:
    def __init__(
        self,
        *,
        sdk_client: Any,
        model: str,
        provider_identity: str,
        response_max_bytes: int,
    ) -> None:
        if response_max_bytes <= 0:
            raise ValueError("response_max_bytes must be positive")
        self.sdk_client = sdk_client
        self.model = model
        self.response_max_bytes = response_max_bytes
        identity_payload = f"{provider_identity}\0{model}".encode("utf-8")
        digest = hashlib.sha256(identity_payload).hexdigest()[:24]
        self.model_identity = f"openai-compatible:{digest}:{model}"

    def generate(self, request: AnalysisRequest) -> str:
        try:
            response = self.sdk_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=request.max_output_tokens,
            )
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise AnalysisClientError(
                    "analysis_malformed_response", retryable=False
                )
            if len(content.encode("utf-8")) > self.response_max_bytes:
                raise AnalysisClientError(
                    "analysis_response_too_large", retryable=False
                )
            return content
        except AnalysisClientError:
            raise
        except Exception as exc:
            raise _safe_client_error(exc) from None


def build_openai_compatible_client(
    *,
    api_key: str,
    base_url: str,
    model: str,
    connect_timeout: float,
    read_timeout: float,
    write_timeout: float,
    pool_timeout: float,
    response_max_bytes: int,
) -> OpenAICompatibleAnalysisClient:
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=write_timeout,
        pool=pool_timeout,
    )
    sdk_client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
    )
    return OpenAICompatibleAnalysisClient(
        sdk_client=sdk_client,
        model=model,
        provider_identity=_provider_identity(base_url),
        response_max_bytes=response_max_bytes,
    )


def _provider_identity(base_url: str) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    safe = urlunsplit((parsed.scheme.lower(), host.lower(), parsed.path.rstrip("/"), "", ""))
    return hashlib.sha256(safe.encode("utf-8")).hexdigest()


def _safe_client_error(exc: Exception) -> AnalysisClientError:
    status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return AnalysisClientError("analysis_auth_failed", retryable=False)
    if isinstance(status, int):
        retryable = status in _RETRYABLE_STATUSES or status >= 500
        return AnalysisClientError(
            "analysis_transient_error" if retryable else "analysis_permanent_error",
            retryable=retryable,
            retry_after_seconds=_retry_after_seconds(exc) if retryable else None,
        )
    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name or "connect" in name:
        return AnalysisClientError("analysis_timeout", retryable=True)
    return AnalysisClientError("analysis_permanent_error", retryable=False)


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) if response is not None else {}
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None
