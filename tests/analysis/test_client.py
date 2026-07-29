from types import SimpleNamespace

import pytest

from zotero_arxiv_daily.analysis.client import (
    AnalysisClientError,
    AnalysisRequest,
    OpenAICompatibleAnalysisClient,
    build_openai_compatible_client,
)


class FakeCompletions:
    def __init__(self, *, content="{}", error=None, finish_reason="stop"):
        self.content = content
        self.error = error
        self.finish_reason = finish_reason
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content),
                    finish_reason=self.finish_reason,
                )
            ]
        )


class FakeSdk:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)


class StatusError(RuntimeError):
    def __init__(self, status_code, retry_after=None):
        super().__init__("SECRET_RESPONSE_BODY")
        self.status_code = status_code
        self.response = SimpleNamespace(
            headers={"retry-after": retry_after} if retry_after is not None else {}
        )


def request():
    return AnalysisRequest(
        prompt_version="stage3-v1",
        system_prompt="system",
        user_prompt="user",
        response_schema={"type": "object", "additionalProperties": False},
        max_output_tokens=512,
    )


def adapter(
    *,
    content="{}",
    error=None,
    finish_reason="stop",
    response_max_bytes=1024,
    request_extra_body=None,
):
    completions = FakeCompletions(
        content=content, error=error, finish_reason=finish_reason
    )
    return (
        OpenAICompatibleAnalysisClient(
            sdk_client=FakeSdk(completions),
            model="fake-model",
            provider_identity="fake-provider",
            response_max_bytes=response_max_bytes,
            request_extra_body=request_extra_body,
        ),
        completions,
    )


def test_client_sends_json_request_and_returns_content():
    client, completions = adapter(content='{"ok": true}')
    assert client.generate(request()) == '{"ok": true}'
    call = completions.calls[0]
    assert call["model"] == "fake-model"
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_tokens"] == 512
    assert call["messages"][0]["content"] == "system"
    assert client.model_identity.startswith("openai-compatible:")


def test_client_sends_provider_specific_non_thinking_request_body():
    client, completions = adapter(
        content='{"ok": true}',
        request_extra_body={"thinking": {"type": "disabled"}},
    )

    assert client.generate(request()) == '{"ok": true}'
    assert completions.calls[0]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


def test_client_classifies_empty_and_truncated_responses_as_retryable():
    empty, _ = adapter(content="")
    with pytest.raises(AnalysisClientError) as captured:
        empty.generate(request())
    assert captured.value.code == "analysis_empty_response"
    assert captured.value.retryable is True

    truncated, _ = adapter(content='{"partial":', finish_reason="length")
    with pytest.raises(AnalysisClientError) as captured:
        truncated.generate(request())
    assert captured.value.code == "analysis_response_truncated"
    assert captured.value.retryable is True


def test_client_rejects_oversized_utf8_response_without_echoing_body():
    client, _ = adapter(content="中" * 40, response_max_bytes=100)
    with pytest.raises(AnalysisClientError) as captured:
        client.generate(request())
    assert captured.value.code == "analysis_response_too_large"
    assert "中" not in str(captured.value)
    assert captured.value.retryable is False


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 503])
def test_client_classifies_only_transient_statuses_as_retryable(status):
    client, _ = adapter(error=StatusError(status, retry_after="12"))
    with pytest.raises(AnalysisClientError) as captured:
        client.generate(request())
    assert captured.value.retryable is True
    assert captured.value.retry_after_seconds == 12
    assert "SECRET_RESPONSE_BODY" not in str(captured.value)


def test_client_classifies_auth_and_validation_as_permanent_and_safe():
    client, _ = adapter(error=StatusError(401))
    with pytest.raises(AnalysisClientError) as captured:
        client.generate(request())
    assert captured.value.code == "analysis_auth_failed"
    assert captured.value.retryable is False
    assert str(captured.value) == "analysis_auth_failed"


def test_client_classifies_timeout_as_retryable():
    client, _ = adapter(error=TimeoutError("SECRET_PROMPT"))
    with pytest.raises(AnalysisClientError) as captured:
        client.generate(request())
    assert captured.value.code == "analysis_timeout"
    assert captured.value.retryable is True
    assert "SECRET_PROMPT" not in str(captured.value)


def test_factory_sets_explicit_timeouts_and_disables_sdk_retries(monkeypatch):
    captured = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return FakeSdk(FakeCompletions())

    monkeypatch.setattr(
        "zotero_arxiv_daily.analysis.client.OpenAI", fake_openai
    )
    client = build_openai_compatible_client(
        api_key="not-a-real-key",
        base_url="https://llm.example.test/v1?private=query",
        model="fake-model",
        connect_timeout=3,
        read_timeout=7,
        write_timeout=5,
        pool_timeout=2,
        response_max_bytes=1000,
    )
    assert captured["max_retries"] == 0
    assert captured["timeout"].connect == 3
    assert captured["timeout"].read == 7
    assert "private" not in client.model_identity
    assert "not-a-real-key" not in client.model_identity


def test_factory_disables_thinking_for_official_deepseek_json_requests(monkeypatch):
    completions = FakeCompletions(content='{"ok": true}')

    def fake_openai(**_kwargs):
        return FakeSdk(completions)

    monkeypatch.setattr("zotero_arxiv_daily.analysis.client.OpenAI", fake_openai)
    client = build_openai_compatible_client(
        api_key="not-a-real-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        connect_timeout=3,
        read_timeout=7,
        write_timeout=5,
        pool_timeout=2,
        response_max_bytes=1000,
    )

    assert client.generate(request()) == '{"ok": true}'
    assert completions.calls[0]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
