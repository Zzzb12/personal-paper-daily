from pathlib import Path

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
