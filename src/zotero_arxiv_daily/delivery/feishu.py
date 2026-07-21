"""Pure, offline-safe conversion from validated analyses to Feishu cards."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx

from zotero_arxiv_daily.analysis.validation_schemas import ValidationBatchResult
from zotero_arxiv_daily.delivery.schemas import (
    DeliveryReceipt,
    DeliveryRequest,
    DigestPaper,
    FeishuPayload,
    FeishuSettings,
)


_MISSING_FACT = "论文未明确提供"
_CARD_TITLE = "个人论文日报"
_MARKDOWN_SPECIAL_CHARACTERS = frozenset(r"\\`*_{}[]()#+-.!|>~")
_FEISHU_API_ROOT = "https://open.feishu.cn"
_TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
_MESSAGE_PATH = "/open-apis/im/v1/messages"


class FeishuDeliveryError(RuntimeError):
    """Controlled delivery failure that never includes remote response content."""


class FeishuTransport(Protocol):
    """Small injected HTTP boundary used by the Feishu client."""

    def post(self, url: str, **kwargs: Any) -> Any: ...


class HttpxTransport:
    """Production HTTP transport, constructed only behind the CLI send gate."""

    def __init__(self) -> None:
        self._client = httpx.Client()

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._client.post(url, **kwargs)

    def close(self) -> None:
        self._client.close()


def _safe_https_url(value: object) -> str | None:
    """Return a display-safe HTTPS URL, never returning credentialed authorities."""

    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return quote(value.strip(), safe=":/?#[]@!$&'*+,;=%")


def _escaped_text(value: object) -> str:
    """Escape user-provided text before placing it in a Markdown card element."""

    if not isinstance(value, str) or not value.strip():
        return _MISSING_FACT
    single_line = " ".join(value.split())
    markdown_escaped = "".join(
        f"\\{character}" if character in _MARKDOWN_SPECIAL_CHARACTERS else character
        for character in single_line
    )
    return html.escape(markdown_escaped, quote=True)


def _evidence_pointer(paper: DigestPaper) -> str:
    """Render the one strongest validated visual pointer, without fabricating it."""

    label = _escaped_text(paper.evidence_label)
    page = paper.evidence_page
    safe_page = (
        page
        if isinstance(page, int) and not isinstance(page, bool) and page >= 1
        else None
    )
    if safe_page is None:
        return label
    if label == _MISSING_FACT:
        return f"第 {safe_page} 页"
    return f"{label}（第 {safe_page} 页）"


class DigestPolicy:
    """Build a bounded Feishu request exclusively from publishable Stage 4 output."""

    @staticmethod
    def build(
        batch: ValidationBatchResult, *, chat_id: str, site_url: str
    ) -> DeliveryRequest:
        safe_site_url = _safe_https_url(site_url)
        papers: list[DigestPaper] = []
        if safe_site_url is not None:
            for result in batch.results:
                report = result.report
                validated = result.validated
                if (
                    result.status != "validated"
                    or report is None
                    or report.status != "valid"
                    or report.publication_eligibility != "eligible"
                    or validated is None
                ):
                    continue
                analysis = validated.analysis
                if (
                    validated.report != report
                    or result.paper_id != analysis.paper_id
                    or result.paper_id != report.paper_id
                ):
                    continue
                strongest_visual = max(
                    analysis.supporting_visuals,
                    key=lambda visual: visual.confidence,
                    default=None,
                )
                papers.append(
                    DigestPaper(
                        paper_id=analysis.paper_id,
                        english_title=analysis.english_title,
                        chinese_title=(
                            analysis.chinese_title.text_zh
                            if analysis.chinese_title is not None
                            else None
                        ),
                        recommendation_reason=(
                            analysis.recommendation_reason.text_zh
                            if analysis.recommendation_reason is not None
                            else None
                        ),
                        core_insight=(
                            analysis.insights[0].text_zh if analysis.insights else None
                        ),
                        evidence_label=(
                            strongest_visual.label if strongest_visual is not None else None
                        ),
                        evidence_page=(
                            strongest_visual.pdf_page if strongest_visual is not None else None
                        ),
                        experimental_conclusion=(
                            analysis.experimental_conclusions[0].text_zh
                            if analysis.experimental_conclusions
                            else None
                        ),
                        site_url=safe_site_url,
                        validation_status="valid",
                        publication_eligibility="eligible",
                    )
                )
                if len(papers) == 5:
                    break

        payload = FeishuPayload(chat_id=chat_id, papers=tuple(papers))
        canonical_payload = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return DeliveryRequest(
            payload=payload,
            idempotency_key=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        )


class FeishuRenderer:
    """Render a validated payload as deterministic Feishu interactive-card JSON."""

    @staticmethod
    def render(payload: FeishuPayload) -> str:
        sections = [_CARD_TITLE]
        for index, paper in enumerate(payload.papers[:5], start=1):
            if (
                paper.validation_status != "valid"
                or paper.publication_eligibility != "eligible"
            ):
                continue
            safe_link = _safe_https_url(paper.site_url)
            sections.extend(
                (
                    f"### 论文 {index}",
                    "**中文标题**",
                    _escaped_text(paper.chinese_title),
                    "**英文标题**",
                    _escaped_text(paper.english_title),
                    "**推荐理由**",
                    _escaped_text(paper.recommendation_reason),
                    "**核心 Insight**",
                    _escaped_text(paper.core_insight),
                    "**关键证据**",
                    _evidence_pointer(paper),
                    "**实验结论**",
                    _escaped_text(paper.experimental_conclusion),
                    "**阅读链接**",
                    f"[打开完整解读]({safe_link})" if safe_link is not None else _MISSING_FACT,
                )
            )

        card = {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": _CARD_TITLE},
                },
                "body": {
                    "elements": [{"tag": "markdown", "content": "\n".join(sections)}]
                },
            },
        }
        return json.dumps(card, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class FeishuClient:
    """Send one rendered card through an injected transport with bounded retries."""

    def __init__(
        self,
        settings: FeishuSettings,
        transport: FeishuTransport,
        *,
        environment: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._environment = os.environ if environment is None else environment
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self._receipts: dict[str, DeliveryReceipt] = {}

    def send(self, request: DeliveryRequest, rendered_content: str) -> DeliveryReceipt:
        cached = self._receipts.get(request.idempotency_key)
        if cached is not None:
            return cached

        card_content = self._rendered_card_content(rendered_content)
        app_id = self._required_credential(self._settings.app_id_environment_name)
        app_secret = self._required_credential(
            self._settings.app_secret_environment_name
        )
        token_response = self._post_with_retry(
            f"{_FEISHU_API_ROOT}{_TOKEN_PATH}",
            request,
            json={"app_id": app_id, "app_secret": app_secret},
        )
        token_payload = self._response_payload(token_response)
        token = token_payload.get("tenant_access_token")
        if token_payload.get("code") != 0 or not isinstance(token, str) or not token:
            raise FeishuDeliveryError("feishu token response was invalid")

        message_response = self._post_with_retry(
            f"{_FEISHU_API_ROOT}{_MESSAGE_PATH}",
            request,
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": request.payload.chat_id,
                "msg_type": "interactive",
                "content": card_content,
                "uuid": str(
                    uuid.uuid5(uuid.NAMESPACE_URL, request.idempotency_key)
                ),
            },
        )
        message_payload = self._response_payload(message_response)
        data = message_payload.get("data")
        message_id = data.get("message_id") if isinstance(data, dict) else None
        if (
            message_payload.get("code") != 0
            or not isinstance(message_id, str)
            or not message_id.startswith("om_")
        ):
            raise FeishuDeliveryError("feishu message response was invalid")
        request_id = message_response.headers.get("x-request-id", "unavailable")
        if not isinstance(request_id, str) or not request_id.strip():
            request_id = "unavailable"
        try:
            receipt = DeliveryReceipt(
                request_id=request_id,
                message_id=message_id,
                idempotency_key=request.idempotency_key,
                delivered_at=self._clock(),
            )
        except Exception:
            raise FeishuDeliveryError("feishu message response was invalid") from None
        self._receipts[request.idempotency_key] = receipt
        return receipt

    def _required_credential(self, name: str) -> str:
        value = self._environment.get(name, "")
        if not value.strip():
            raise FeishuDeliveryError(f"missing required environment variable: {name}")
        return value

    def _post_with_retry(
        self,
        url: str,
        request: DeliveryRequest,
        **kwargs: Any,
    ) -> Any:
        for retry_index in range(request.max_retries + 1):
            try:
                response = self._transport.post(
                    url, timeout=request.timeout_seconds, **kwargs
                )
            except httpx.TimeoutException:
                raise FeishuDeliveryError("feishu request timed out") from None
            except Exception:
                raise FeishuDeliveryError("feishu transport failed") from None

            status_code = response.status_code
            if 200 <= status_code < 300:
                return response
            retryable = status_code == 429 or 500 <= status_code < 600
            if not retryable:
                raise FeishuDeliveryError(
                    f"feishu request rejected with HTTP {status_code}"
                )
            if retry_index == request.max_retries:
                raise FeishuDeliveryError("feishu transient retry limit reached")
            self._sleep(self._retry_delay(response, retry_index))
        raise FeishuDeliveryError("feishu transient retry limit reached")

    @staticmethod
    def _rendered_card_content(rendered_content: str) -> str:
        try:
            envelope = json.loads(rendered_content)
        except Exception:
            raise FeishuDeliveryError("rendered feishu card was invalid") from None
        if (
            not isinstance(envelope, dict)
            or envelope.get("msg_type") != "interactive"
            or not isinstance(envelope.get("card"), dict)
        ):
            raise FeishuDeliveryError("rendered feishu card was invalid")
        return json.dumps(
            envelope["card"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _response_payload(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            raise FeishuDeliveryError("feishu response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise FeishuDeliveryError("feishu response was not a JSON object")
        return payload

    def _retry_delay(self, response: Any, retry_index: int) -> float:
        retry_after = response.headers.get("retry-after")
        if isinstance(retry_after, str):
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = math.nan
            if math.isfinite(seconds):
                return min(max(seconds, 0.0), 60.0)
            try:
                retry_at = parsedate_to_datetime(retry_after)
                now = self._clock()
                if (
                    retry_at.tzinfo is not None
                    and retry_at.utcoffset() is not None
                    and now.tzinfo is not None
                    and now.utcoffset() is not None
                ):
                    date_delay = (
                        retry_at.astimezone(UTC) - now.astimezone(UTC)
                    ).total_seconds()
                    if math.isfinite(date_delay):
                        return min(max(date_delay, 0.0), 60.0)
            except (TypeError, ValueError, OverflowError):
                pass
        return float(min(2**retry_index, 8))
