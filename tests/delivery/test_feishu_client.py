from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from zotero_arxiv_daily.delivery.feishu import FeishuClient, FeishuDeliveryError
from zotero_arxiv_daily.delivery.schemas import (
    DeliveryRequest,
    FeishuPayload,
    FeishuSettings,
)


APP_ID = "cli_app_id"
APP_SECRET = "SENTINEL_APP_SECRET"
CHAT_ID = "oc_0123456789abcdef0123456789abcdef"
SITE_URL = "https://papers.example.test/daily/"
NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeTransport:
    def __init__(self, *outcomes: FakeResponse | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _settings() -> FeishuSettings:
    return FeishuSettings(chat_id=CHAT_ID, site_url=SITE_URL)


def _request(*, max_retries: int = 3) -> DeliveryRequest:
    return DeliveryRequest(
        payload=FeishuPayload(chat_id=CHAT_ID, papers=()),
        idempotency_key="a" * 64,
        timeout_seconds=7,
        max_retries=max_retries,
    )


def _environment() -> dict[str, str]:
    return {"FEISHU_APP_ID": APP_ID, "FEISHU_APP_SECRET": APP_SECRET}


def _rendered() -> str:
    return json.dumps({"msg_type": "interactive", "card": {"schema": "2.0"}})


def _client(transport: FakeTransport, *, sleeps: list[float] | None = None) -> FeishuClient:
    recorded_sleeps = sleeps if sleeps is not None else []
    return FeishuClient(
        _settings(),
        transport,
        environment=_environment(),
        clock=lambda: NOW,
        sleep=recorded_sleeps.append,
    )


def test_client_fetches_token_and_sends_rendered_card() -> None:
    transport = FakeTransport(
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(
            200,
            {"code": 0, "data": {"message_id": "om_message-1"}},
            headers={"x-request-id": "request-1"},
        ),
    )

    rendered = json.dumps(
        {"msg_type": "interactive", "card": {"schema": "2.0", "body": {}}}
    )

    receipt = _client(transport).send(_request(), rendered)

    assert receipt.request_id == "request-1"
    assert receipt.message_id == "om_message-1"
    assert receipt.idempotency_key == "a" * 64
    assert receipt.delivered_at == NOW
    token_url, token_call = transport.calls[0]
    send_url, send_call = transport.calls[1]
    assert token_url.endswith("/open-apis/auth/v3/tenant_access_token/internal")
    assert token_call["json"] == {"app_id": APP_ID, "app_secret": APP_SECRET}
    assert token_call["timeout"] == 7
    assert send_url.endswith("/open-apis/im/v1/messages")
    assert send_call["params"] == {"receive_id_type": "chat_id"}
    assert send_call["headers"] == {"Authorization": "Bearer tenant-token"}
    assert send_call["json"] == {
        "receive_id": CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(
            {"schema": "2.0", "body": {}},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "a" * 64)),
    }
    assert send_call["timeout"] == 7


def test_message_retry_reuses_stable_legal_uuid_derived_from_idempotency_key() -> None:
    transport = FakeTransport(
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(503, {"code": 503}),
        FakeResponse(
            200,
            {"code": 0, "data": {"message_id": "om_message-1"}},
            headers={"x-request-id": "request-1"},
        ),
    )
    request = _request(max_retries=1)

    _client(transport).send(request, _rendered())

    message_uuids = [call[1]["json"]["uuid"] for call in transport.calls[1:]]
    expected_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, request.idempotency_key))
    assert message_uuids == [expected_uuid, expected_uuid]
    assert len(expected_uuid) <= 50
    assert uuid.UUID(expected_uuid).version == 5


def test_client_returns_cached_receipt_without_duplicate_send() -> None:
    transport = FakeTransport(
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(
            200,
            {"code": 0, "data": {"message_id": "om_message-1"}},
            headers={"x-request-id": "request-1"},
        ),
    )
    client = _client(transport)

    first = client.send(_request(), _rendered())
    second = client.send(_request(), "different content is ignored for the same key")

    assert second is first
    assert len(transport.calls) == 2


def test_client_does_not_retry_401_or_echo_secret_response_text() -> None:
    transport = FakeTransport(
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(401, {"code": 999, "msg": APP_SECRET}),
    )

    with pytest.raises(FeishuDeliveryError) as exc_info:
        _client(transport).send(_request(max_retries=3), _rendered())

    assert len(transport.calls) == 2
    assert "401" in str(exc_info.value)
    assert APP_SECRET not in str(exc_info.value)


@pytest.mark.parametrize("status_code", (400, 403, 404, 422))
def test_client_does_not_retry_permanent_http_4xx(status_code: int) -> None:
    transport = FakeTransport(
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(status_code, {"code": status_code}),
    )

    with pytest.raises(FeishuDeliveryError, match=str(status_code)):
        _client(transport).send(_request(max_retries=3), _rendered())

    assert len(transport.calls) == 2


def test_client_does_not_retry_non_http_5xx_status() -> None:
    transport = FakeTransport(
        FakeResponse(600, {"code": 600}),
    )

    with pytest.raises(FeishuDeliveryError, match="600"):
        _client(transport).send(_request(max_retries=3), _rendered())

    assert len(transport.calls) == 1


@pytest.mark.parametrize("status_code", (429, 503))
def test_client_retries_only_bounded_transient_responses(status_code: int) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(status_code, {"code": status_code}, headers={"retry-after": "0"}),
        FakeResponse(status_code, {"code": status_code}),
        FakeResponse(status_code, {"code": status_code}),
    )

    with pytest.raises(FeishuDeliveryError, match="retry limit"):
        _client(transport, sleeps=sleeps).send(_request(max_retries=2), _rendered())

    assert len(transport.calls) == 4
    assert len(sleeps) == 2


def test_client_redacts_timeout_details() -> None:
    transport = FakeTransport(
        httpx.ReadTimeout(f"request timed out with {APP_SECRET}"),
    )

    with pytest.raises(FeishuDeliveryError, match="timed out") as exc_info:
        _client(transport).send(_request(), _rendered())

    assert APP_SECRET not in str(exc_info.value)
    assert len(transport.calls) == 1


def test_client_respects_retry_after_http_date() -> None:
    sleeps: list[float] = []
    retry_at = datetime(2026, 7, 21, 8, 0, 17, tzinfo=UTC)
    transport = FakeTransport(
        FakeResponse(
            429,
            {"code": 429},
            headers={"retry-after": format_datetime(retry_at, usegmt=True)},
        ),
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(
            200,
            {"code": 0, "data": {"message_id": "om_message-1"}},
            headers={"x-request-id": "request-1"},
        ),
    )

    _client(transport, sleeps=sleeps).send(_request(max_retries=1), _rendered())

    assert sleeps == [17.0]


@pytest.mark.parametrize("retry_after", ("nan", "inf", "+inf", "-inf"))
def test_client_rejects_non_finite_retry_after_seconds(retry_after: str) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        FakeResponse(429, {"code": 429}, headers={"retry-after": retry_after}),
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(
            200,
            {"code": 0, "data": {"message_id": "om_message-1"}},
            headers={"x-request-id": "request-1"},
        ),
    )

    _client(transport, sleeps=sleeps).send(_request(max_retries=1), _rendered())

    assert sleeps == [1.0]
    assert all(math.isfinite(delay) for delay in sleeps)
