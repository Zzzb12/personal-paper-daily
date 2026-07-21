from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import ConfigDict, Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.schemas import StrictModel


_CHAT_ID_RE = re.compile(r"^oc_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MESSAGE_ID_RE = re.compile(r"^om_[A-Za-z0-9_-]+$")
_URL_USERINFO_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*@")
_REDACTED_CREDENTIAL_URL = "https://redacted.invalid/credential-url-rejected"


class DeliveryStrictModel(StrictModel):
    """Frozen delivery boundary that rejects input coercion as well as extras."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="before")
    @classmethod
    def redact_credential_bearing_site_url(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        site_url = value.get("site_url")
        if not isinstance(site_url, str):
            return value
        if not _URL_USERINFO_RE.match(site_url):
            return value
        sanitized = dict(value)
        sanitized["site_url"] = _REDACTED_CREDENTIAL_URL
        return sanitized


def _non_empty(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _https_url(value: str) -> str:
    normalized = _non_empty(value)
    if normalized == _REDACTED_CREDENTIAL_URL:
        raise ValueError("site_url must not include credentials")
    try:
        parsed = urlparse(normalized)
    except ValueError as exc:
        raise ValueError("site_url must be an absolute HTTPS URL") from exc
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("site_url must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("site_url must not include credentials")
    return normalized


def _chat_id(value: str) -> str:
    normalized = _non_empty(value)
    if not _CHAT_ID_RE.fullmatch(normalized):
        raise ValueError("chat_id must be a Feishu oc_ chat identifier")
    return normalized


class DigestPaper(DeliveryStrictModel):
    """The only Stage 4 output permitted in a daily delivery card."""

    paper_id: str
    english_title: str
    chinese_title: str | None = None
    site_url: str
    validation_status: Literal["valid"]
    publication_eligibility: Literal["eligible"]

    @field_validator("paper_id", "english_title")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("chinese_title")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _non_empty(value) if value is not None else None

    @field_validator("site_url")
    @classmethod
    def require_https_site_url(cls, value: str) -> str:
        return _https_url(value)


class FeishuPayload(DeliveryStrictModel):
    """Validated card input, deliberately independent of credentials."""

    chat_id: str
    papers: tuple[DigestPaper, ...] = Field(max_length=5)

    @field_validator("chat_id")
    @classmethod
    def validate_chat_id(cls, value: str) -> str:
        return _chat_id(value)


class DeliveryRequest(DeliveryStrictModel):
    """An idempotent, bounded request to send one Feishu payload."""

    payload: FeishuPayload
    idempotency_key: str
    timeout_seconds: float = Field(default=10, ge=1)
    max_retries: int = Field(default=3, ge=0)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("idempotency_key must be a lowercase SHA-256 digest")
        return value


class DeliveryReceipt(DeliveryStrictModel):
    """Non-sensitive acknowledgement returned after a successful delivery."""

    request_id: str
    message_id: str
    idempotency_key: str
    delivered_at: datetime

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("message_id")
    @classmethod
    def validate_message_id(cls, value: str) -> str:
        normalized = _non_empty(value)
        if not _MESSAGE_ID_RE.fullmatch(normalized):
            raise ValueError("message_id must be a Feishu om_ message identifier")
        return normalized

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("idempotency_key must be a lowercase SHA-256 digest")
        return value

    @field_validator("delivered_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("delivered_at must be timezone-aware")
        return value.astimezone(UTC)


class FeishuSettings(DeliveryStrictModel):
    """Non-secret Feishu configuration; credentials remain in the environment."""

    app_id_environment_name: Literal["FEISHU_APP_ID"] = "FEISHU_APP_ID"
    app_secret_environment_name: Literal["FEISHU_APP_SECRET"] = "FEISHU_APP_SECRET"
    chat_id: str
    site_url: str

    @field_validator("chat_id")
    @classmethod
    def validate_chat_id(cls, value: str) -> str:
        return _chat_id(value)

    @field_validator("site_url")
    @classmethod
    def require_https_site_url(cls, value: str) -> str:
        return _https_url(value)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> FeishuSettings:
        required = (
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            "FEISHU_CHAT_ID",
            "PAPER_DAILY_SITE_URL",
        )
        missing = tuple(name for name in required if not environment.get(name, "").strip())
        if missing:
            raise ValueError(f"missing required environment variables: {', '.join(missing)}")
        return cls(
            chat_id=environment["FEISHU_CHAT_ID"],
            site_url=environment["PAPER_DAILY_SITE_URL"],
        )
