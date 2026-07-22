from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.delivery.schemas import (
    DeliveryReceipt,
    DeliveryRequest,
    DigestPaper,
    FeishuPayload,
    FeishuSettings,
)


def _paper(index: int = 1, **changes: object) -> DigestPaper:
    values: dict[str, object] = {
        "paper_id": f"arxiv:2401.{index:05d}",
        "english_title": "Strict delivery contracts",
        "chinese_title": "严格投递契约",
        "recommendation_reason": "与研究兴趣直接相关",
        "core_insight": "以验证证据支持核心结论",
        "evidence_label": "Table 1",
        "evidence_page": 3,
        "experimental_conclusion": "实验结论经过验证",
        "site_url": "https://papers.example.test/daily/run-1.html",
        "validation_status": "valid",
        "publication_eligibility": "eligible",
    }
    values.update(changes)
    return DigestPaper(**values)


def test_digest_paper_is_strict_https_and_stage4_eligible() -> None:
    paper = _paper()

    assert paper.site_url == "https://papers.example.test/daily/run-1.html"
    assert paper.recommendation_reason == "与研究兴趣直接相关"
    assert paper.core_insight == "以验证证据支持核心结论"
    assert paper.evidence_label == "Table 1"
    assert paper.evidence_page == 3
    assert paper.experimental_conclusion == "实验结论经过验证"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DigestPaper(**paper.model_dump(), unexpected="value")
    with pytest.raises(ValidationError, match="HTTPS"):
        _paper(site_url="http://papers.example.test/daily/run-1.html")
    with pytest.raises(ValidationError, match="credentials"):
        _paper(site_url="https://user:secret@papers.example.test/daily/run-1.html")
    with pytest.raises(ValidationError, match="valid"):
        _paper(validation_status="partial")


def test_payload_rejects_malformed_chat_ids_and_more_than_five_papers() -> None:
    with pytest.raises(ValidationError, match="chat_id"):
        FeishuPayload(chat_id="not-a-chat-id", papers=(_paper(),))
    with pytest.raises(ValidationError, match="at most 5"):
        FeishuPayload(
            chat_id="oc_0123456789abcdef0123456789abcdef",
            papers=tuple(_paper(index) for index in range(1, 7)),
        )
    with pytest.raises(ValidationError):
        FeishuPayload(
            chat_id="oc_0123456789abcdef0123456789abcdef", papers=[_paper()]
        )


def test_delivery_request_validates_retry_timeout_and_idempotency_key() -> None:
    payload = FeishuPayload(
        chat_id="oc_0123456789abcdef0123456789abcdef", papers=(_paper(),)
    )

    request = DeliveryRequest(payload=payload, idempotency_key="a" * 64)

    assert request.timeout_seconds >= 1
    assert request.max_retries >= 0
    with pytest.raises(ValidationError, match="SHA-256"):
        DeliveryRequest(payload=payload, idempotency_key="not-a-digest")
    with pytest.raises(ValidationError):
        DeliveryRequest(payload=payload, idempotency_key="a" * 64, timeout_seconds=0)
    with pytest.raises(ValidationError):
        DeliveryRequest(payload=payload, idempotency_key="a" * 64, max_retries=-1)
    with pytest.raises(ValidationError):
        DeliveryRequest(payload=payload, idempotency_key="a" * 64, timeout_seconds="1")
    with pytest.raises(ValidationError):
        DeliveryRequest(payload=payload, idempotency_key="a" * 64, max_retries=True)


def test_delivery_receipt_is_frozen_and_strict() -> None:
    receipt = DeliveryReceipt(
        request_id="request-1",
        message_id="om_0123456789abcdef",
        idempotency_key="a" * 64,
        delivered_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    with pytest.raises(ValidationError, match="frozen"):
        receipt.message_id = "om_replaced"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DeliveryReceipt(**receipt.model_dump(), secret="must-not-appear")


def test_feishu_settings_reports_missing_environment_names_without_values() -> None:
    with pytest.raises(ValueError) as exc_info:
        FeishuSettings.from_environment({})

    message = str(exc_info.value)
    assert "FEISHU_APP_ID" in message
    assert "FEISHU_APP_SECRET" in message
    assert "FEISHU_CHAT_ID" in message
    assert "PAPER_DAILY_SITE_URL" in message

    settings = FeishuSettings.from_environment(
        {
            "FEISHU_APP_ID": "app-id-is-not-retained",
            "FEISHU_APP_SECRET": "secret-is-not-retained",
            "FEISHU_CHAT_ID": "oc_0123456789abcdef0123456789abcdef",
            "PAPER_DAILY_SITE_URL": "https://papers.example.test/daily/",
        }
    )

    assert settings.app_id_environment_name == "FEISHU_APP_ID"
    assert settings.app_secret_environment_name == "FEISHU_APP_SECRET"
    assert "secret-is-not-retained" not in settings.model_dump_json()


@pytest.mark.parametrize(
    ("factory", "sentinel"),
    (
        (
            lambda: _paper(
                site_url="https://user:REAL_SECRET@papers.example.test/daily/run-1.html"
            ),
            "REAL_SECRET",
        ),
        (
            lambda: FeishuSettings.from_environment(
                {
                    "FEISHU_APP_ID": "app-id",
                    "FEISHU_APP_SECRET": "app-secret",
                    "FEISHU_CHAT_ID": "oc_0123456789abcdef0123456789abcdef",
                    "PAPER_DAILY_SITE_URL": "https://user:REAL_SECRET@papers.example.test/",
                }
            ),
            "REAL_SECRET",
        ),
        (
            lambda: _paper(site_url="https://user:REAL_SECRET@[::1"),
            "REAL_SECRET",
        ),
        (
            lambda: _paper(
                site_url="  https://user:REAL_SECRET@papers.example.test/daily/run-1.html"
            ),
            "REAL_SECRET",
        ),
        (
            lambda: FeishuSettings.from_environment(
                {
                    "FEISHU_APP_ID": "app-id",
                    "FEISHU_APP_SECRET": "app-secret",
                    "FEISHU_CHAT_ID": "oc_0123456789abcdef0123456789abcdef",
                    "PAPER_DAILY_SITE_URL": "  https://user:REAL_SECRET@papers.example.test/",
                }
            ),
            "REAL_SECRET",
        ),
        (
            lambda: _paper(
                site_url="https://user:\nREAL_SECRET@papers.example.test/daily/run-1.html"
            ),
            "REAL_SECRET",
        ),
        (
            lambda: _paper(
                site_url="https:\n//user:REAL_SECRET@papers.example.test/daily/run-1.html"
            ),
            "REAL_SECRET",
        ),
        (
            lambda: _paper(
                site_url="https: //user:REAL_SECRET@papers.example.test/daily/run-1.html"
            ),
            "REAL_SECRET",
        ),
    ),
)
def test_credential_bearing_site_url_never_echoes_credentials_in_validation_errors(
    factory: Callable[[], object], sentinel: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        factory()

    assert sentinel not in str(exc_info.value)
    assert sentinel not in str(exc_info.value.errors())
