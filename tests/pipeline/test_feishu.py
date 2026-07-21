from __future__ import annotations

import json
from pathlib import Path

import pytest

from zotero_arxiv_daily.pipeline import feishu


FIXTURE = Path("tests/fixtures/evidence/stage4_golden.json")


class NetworkTransportMustNotBeConstructed:
    def __init__(self) -> None:
        raise AssertionError("network transport was constructed")


def test_fixture_preview_writes_card_without_constructing_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "nested" / "feishu-preview.json"
    monkeypatch.setattr(feishu, "HttpxTransport", NetworkTransportMustNotBeConstructed)

    exit_code = feishu.main(["--offline-fixture", str(FIXTURE), "--output", str(output)])

    card = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert card["msg_type"] == "interactive"
    assert "Synthetic Offline Evidence Paper" in card["card"]["body"]["elements"][0]["content"]


def test_send_rejects_missing_settings_before_constructing_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_CHAT_ID",
        "PAPER_DAILY_SITE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(feishu, "HttpxTransport", NetworkTransportMustNotBeConstructed)

    with pytest.raises(SystemExit) as exc_info:
        feishu.main(
            [
                "--offline-fixture",
                str(FIXTURE),
                "--output",
                str(tmp_path / "preview.json"),
                "--send",
            ]
        )

    assert exc_info.value.code == 2
