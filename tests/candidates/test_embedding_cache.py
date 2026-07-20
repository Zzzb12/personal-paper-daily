import numpy as np

from zotero_arxiv_daily.candidates.ranking import (
    CachedEmbeddingProvider,
    EmbeddingIdentity,
    FileEmbeddingCache,
)


class CountingProvider:
    def __init__(self, identity):
        self.identity = identity
        self.calls = []

    def encode(self, texts):
        self.calls.append(texts)
        return np.asarray([[len(text), 1.0] for text in texts], dtype=np.float32)


def identity(model="v1"):
    return EmbeddingIdentity(provider="fake", model=model, task="retrieval")


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
