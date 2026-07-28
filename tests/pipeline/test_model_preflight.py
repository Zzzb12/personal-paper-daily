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


def test_preflight_loads_pinned_public_reranker_without_credentials():
    captured: list[_FakeProvider] = []

    def factory(**kwargs):
        provider = _FakeProvider(**kwargs)
        captured.append(provider)
        return provider

    prepare_local_reranker(
        Path(__file__).parents[2] / "config",
        provider_factory=factory,
    )

    assert captured[0].kwargs["model"] == "jinaai/jina-embeddings-v5-text-nano-retrieval"
    assert captured[0].kwargs["revision"] == "ac5d898c8d382b17167c33e5c8af644a3519b47d"
    assert captured[0].kwargs["cache_folder"] == Path("models/reranker")
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
