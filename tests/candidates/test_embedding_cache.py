import json

import numpy as np

from zotero_arxiv_daily.candidates.ranking import (
    CachedEmbeddingProvider,
    EmbeddingIdentity,
    FileEmbeddingCache,
    SentenceTransformerEmbeddingProvider,
)


class CountingProvider:
    def __init__(self, identity):
        self.identity = identity
        self.calls = []

    def encode(self, texts):
        self.calls.append(texts)
        return np.asarray(
            [[len(text), *range(1, self.identity.dimension)] for text in texts],
            dtype=self.identity.dtype,
        )


def identity(model="v1", *, settings=None, dimension=2, dtype="float32"):
    return EmbeddingIdentity.from_settings(
        provider="fake", implementation_version="1", model=model, task="retrieval",
        settings=settings or {"prompt_name": "document"}, dimension=dimension, dtype=dtype,
    )


def test_cached_provider_reuses_embeddings(tmp_path):
    delegate = CountingProvider(identity())
    provider = CachedEmbeddingProvider(delegate, FileEmbeddingCache(tmp_path))
    first = provider.encode(("alpha", "beta"))
    second = provider.encode(("alpha", "beta"))
    np.testing.assert_array_equal(first, second)
    assert delegate.calls == [("alpha", "beta")]


def test_cache_identity_change_invalidates_entry(tmp_path):
    first = CountingProvider(identity("v1"))
    second = CountingProvider(identity("v2"))
    CachedEmbeddingProvider(first, FileEmbeddingCache(tmp_path)).encode(("alpha",))
    CachedEmbeddingProvider(second, FileEmbeddingCache(tmp_path)).encode(("alpha",))
    assert first.calls == [("alpha",)]
    assert second.calls == [("alpha",)]


def test_corrupt_cache_entry_becomes_miss(tmp_path):
    delegate = CountingProvider(identity())
    cache = FileEmbeddingCache(tmp_path)
    provider = CachedEmbeddingProvider(delegate, cache)
    provider.encode(("alpha",))
    next(tmp_path.glob("*.npy")).write_bytes(b"not-numpy")
    provider.encode(("alpha",))
    assert delegate.calls == [("alpha",), ("alpha",)]


def test_prompt_or_encode_settings_change_invalidates_entry(tmp_path):
    first = CountingProvider(identity(settings={"prompt_name": "document", "normalize": False}))
    second = CountingProvider(identity(settings={"prompt_name": "query", "normalize": False}))
    CachedEmbeddingProvider(first, FileEmbeddingCache(tmp_path)).encode(("alpha",))
    CachedEmbeddingProvider(second, FileEmbeddingCache(tmp_path)).encode(("alpha",))
    assert first.calls == [("alpha",)]
    assert second.calls == [("alpha",)]


def test_dimension_and_dtype_changes_invalidate_entries(tmp_path):
    providers = [
        CountingProvider(identity(dimension=2, dtype="float32")),
        CountingProvider(identity(dimension=3, dtype="float32")),
        CountingProvider(identity(dimension=3, dtype="float64")),
    ]
    for provider in providers:
        CachedEmbeddingProvider(provider, FileEmbeddingCache(tmp_path)).encode(("alpha",))
    assert [provider.calls for provider in providers] == [[("alpha",)], [("alpha",)], [("alpha",)]]


def test_corrupt_manifest_becomes_cache_miss(tmp_path):
    delegate = CountingProvider(identity())
    provider = CachedEmbeddingProvider(delegate, FileEmbeddingCache(tmp_path))
    provider.encode(("alpha",))
    next(tmp_path.glob("*.json")).write_text("{}", encoding="utf-8")
    provider.encode(("alpha",))
    assert delegate.calls == [("alpha",), ("alpha",)]
    manifest = next(tmp_path.glob("*.json"))
    assert "text_sha256" in manifest.read_text(encoding="utf-8")


def test_sentence_transformer_provider_records_settings_without_network():
    class FakeEncoder:
        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kwargs):
            assert texts == ["alpha"]
            assert kwargs == {
                "task": "retrieval", "prompt_name": "document", "normalize_embeddings": True
            }
            return np.asarray([[1, 2, 3]], dtype=np.float32)

    provider = SentenceTransformerEmbeddingProvider(
        model="synthetic/model", task="retrieval", prompt_name="document",
        encode_kwargs={"normalize_embeddings": True}, model_factory=lambda _: FakeEncoder(),
        implementation_version="test-version",
    )
    result = provider.encode(("alpha",))
    assert result.shape == (1, 3)
    assert provider.identity.dimension == 3
    assert provider.identity.dtype == "float32"
    assert json.loads(provider.identity.settings_json) == {
        "encode_kwargs": {"normalize_embeddings": True}, "prompt_name": "document"
    }
