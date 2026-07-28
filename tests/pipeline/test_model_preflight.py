from pathlib import Path

import pytest

from zotero_arxiv_daily.pipeline import model_preflight
from zotero_arxiv_daily.pipeline.model_preflight import prepare_local_reranker


class _FakeProvider:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_preflight_loads_pinned_public_reranker_without_remote_code():
    captured: list[_FakeProvider] = []

    def factory(**kwargs):
        provider = _FakeProvider(**kwargs)
        captured.append(provider)
        return provider

    prepare_local_reranker(
        Path(__file__).parents[2] / "config",
        provider_factory=factory,
    )

    assert captured[0].kwargs["model"] == "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
    assert captured[0].kwargs["revision"] == "b207367332321f8e44f96e224ef15bc607f4dbf0"
    assert captured[0].kwargs["cache_folder"] == Path("models/reranker")
    assert captured[0].kwargs["task"] == "retrieval"
    assert captured[0].kwargs["trust_remote_code"] is False
    assert captured[0].kwargs["prompt_name"] is None
    assert captured[0].kwargs["encode_kwargs"] == {"normalize_embeddings": True}
    assert captured[0].closed is True


def test_cli_reports_only_safe_preflight_error_code(capsys):
    private_detail = "private-hub-response-body"

    def fail(_config_dir):
        raise RuntimeError(private_detail)

    with pytest.raises(SystemExit) as captured:
        model_preflight.main(["--config-dir", "config"], preparer=fail)

    assert captured.value.code == 2
    stderr = capsys.readouterr().err
    assert "error_code=reranker_runtime_failed" in stderr
    assert private_detail not in stderr
