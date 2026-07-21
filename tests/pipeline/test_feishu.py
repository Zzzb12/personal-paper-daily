from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from zotero_arxiv_daily.pipeline import feishu


FIXTURE = Path("tests/fixtures/evidence/stage4_golden.json")


class NetworkTransportMustNotBeConstructed:
    def __init__(self) -> None:
        raise AssertionError("network transport was constructed")


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
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


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


@pytest.mark.parametrize("abbreviation", ("--sen", "--se"))
def test_send_abbreviations_are_unknown_without_constructing_transport(
    abbreviation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "preview.json"
    environment = {
        "FEISHU_APP_ID": "app-id",
        "FEISHU_APP_SECRET": "app-secret",
        "FEISHU_CHAT_ID": "oc_0123456789abcdef0123456789abcdef",
        "PAPER_DAILY_SITE_URL": "https://papers.example.test/daily/",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(feishu, "HttpxTransport", NetworkTransportMustNotBeConstructed)

    with pytest.raises(SystemExit) as exc_info:
        feishu.main(
            [
                "--offline-fixture",
                str(FIXTURE),
                "--output",
                str(output),
                abbreviation,
            ]
        )

    assert exc_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert not output.exists()


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


@pytest.mark.parametrize(
    ("name", "invalid_value"),
    (
        ("FEISHU_CHAT_ID", "RAW_INVALID_CHAT_VALUE"),
        ("PAPER_DAILY_SITE_URL", "https://user:RAW_SECRET@example.test/daily/"),
    ),
)
def test_send_redacts_complete_but_invalid_environment_configuration(
    name: str,
    invalid_value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment = {
        "FEISHU_APP_ID": "app-id",
        "FEISHU_APP_SECRET": "app-secret",
        "FEISHU_CHAT_ID": "oc_0123456789abcdef0123456789abcdef",
        "PAPER_DAILY_SITE_URL": "https://papers.example.test/daily/",
    }
    environment[name] = invalid_value
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
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

    stderr = capsys.readouterr().err
    assert exc_info.value.code == 2
    assert "invalid Feishu delivery configuration" in stderr
    assert invalid_value not in stderr
    assert "input_value" not in stderr


def test_send_success_sends_without_writing_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "must-not-be-written.json"
    environment = {
        "FEISHU_APP_ID": "app-id",
        "FEISHU_APP_SECRET": "app-secret",
        "FEISHU_CHAT_ID": "oc_0123456789abcdef0123456789abcdef",
        "PAPER_DAILY_SITE_URL": "https://papers.example.test/daily/",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    transport = FakeTransport(
        FakeResponse(200, {"code": 0, "tenant_access_token": "tenant-token"}),
        FakeResponse(
            200,
            {"code": 0, "data": {"message_id": "om_message-1"}},
            headers={"x-request-id": "request-1"},
        ),
    )
    monkeypatch.setattr(feishu, "HttpxTransport", lambda: transport)

    exit_code = feishu.main(
        [
            "--offline-fixture",
            str(FIXTURE),
            "--output",
            str(output),
            "--send",
        ]
    )

    assert exit_code == 0
    assert not output.exists()
    assert len(transport.calls) == 2
    assert transport.calls[1][0].endswith("/open-apis/im/v1/messages")
    assert transport.closed


def test_rejects_fixture_and_output_resolving_to_same_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "fixture.json"
    shutil.copyfile(FIXTURE, fixture)
    original = fixture.read_bytes()
    monkeypatch.setattr(feishu, "HttpxTransport", NetworkTransportMustNotBeConstructed)

    with pytest.raises(SystemExit) as exc_info:
        feishu.main(
            [
                "--offline-fixture",
                str(fixture.parent / "." / fixture.name),
                "--output",
                str(fixture),
            ]
        )

    assert exc_info.value.code == 2
    assert fixture.read_bytes() == original


def test_preview_uses_same_directory_temp_fsync_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "nested" / "preview.json"
    real_fsync = feishu.os.fsync
    real_replace = feishu.os.replace
    fsync_calls: list[int] = []
    replace_calls: list[tuple[Path, Path]] = []

    def recording_fsync(file_descriptor: int) -> None:
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        replace_calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(feishu.os, "fsync", recording_fsync)
    monkeypatch.setattr(feishu.os, "replace", recording_replace)
    monkeypatch.setattr(feishu, "HttpxTransport", NetworkTransportMustNotBeConstructed)

    exit_code = feishu.main(
        ["--offline-fixture", str(FIXTURE), "--output", str(output)]
    )

    assert exit_code == 0
    assert fsync_calls
    assert len(replace_calls) == 1
    source, destination = replace_calls[0]
    assert source.parent.resolve() == output.parent.resolve()
    assert source.resolve() != output.resolve()
    assert destination == output
    assert output.exists()
