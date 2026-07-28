import json
import weakref

import numpy as np
import pytest

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
            assert kwargs == {"normalize_embeddings": True}
            return np.asarray([[1, 2, 3]], dtype=np.float32)

    provider = SentenceTransformerEmbeddingProvider(
        model="synthetic/model", task="retrieval", prompt_name=None,
        trust_remote_code=False,
        encode_kwargs={"normalize_embeddings": True}, model_factory=lambda _: FakeEncoder(),
        implementation_version="test-version",
    )
    result = provider.encode(("alpha",))
    assert result.shape == (1, 3)
    assert provider.identity.dimension == 3
    assert provider.identity.dtype == "float32"
    assert json.loads(provider.identity.settings_json) == {
        "encode_kwargs": {"normalize_embeddings": True},
        "prompt_name": None,
        "trust_remote_code": False,
    }


def test_sentence_transformer_provider_revision_changes_cache_identity():
    class FakeEncoder:
        def get_sentence_embedding_dimension(self):
            return 3

    first = SentenceTransformerEmbeddingProvider(
        model="synthetic/model",
        revision="a" * 40,
        cache_folder="models/reranker",
        task="retrieval",
        prompt_name="document",
        trust_remote_code=False,
        encode_kwargs={},
        model_factory=lambda _: FakeEncoder(),
        implementation_version="test-version",
    )
    second = SentenceTransformerEmbeddingProvider(
        model="synthetic/model",
        revision="b" * 40,
        cache_folder="models/reranker",
        task="retrieval",
        prompt_name="document",
        trust_remote_code=False,
        encode_kwargs={},
        model_factory=lambda _: FakeEncoder(),
        implementation_version="test-version",
    )

    assert first.identity.fingerprint() != second.identity.fingerprint()
    assert json.loads(first.identity.settings_json)["revision"] == "a" * 40
    assert "cache_folder" not in json.loads(first.identity.settings_json)


def test_sentence_transformer_provider_close_releases_encoder_and_collects_once():
    collections = []
    encoder_references = []

    class FakeEncoder:
        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kwargs):
            return np.asarray([[1, 2, 3]], dtype=np.float32)

    def model_factory(_):
        encoder = FakeEncoder()
        encoder_references.append(weakref.ref(encoder))
        return encoder

    provider = SentenceTransformerEmbeddingProvider(
        model="synthetic/model",
        task="retrieval",
        prompt_name=None,
        encode_kwargs={},
        model_factory=model_factory,
        implementation_version="test-version",
        garbage_collect=lambda: collections.append("collected"),
    )

    provider.close()
    provider.close()

    assert collections == ["collected"]
    assert encoder_references[0]() is None
    with pytest.raises(RuntimeError, match="embedding provider is closed"):
        provider.encode(("alpha",))


def test_cached_embedding_provider_forwards_close_once(tmp_path):
    class ClosableProvider(CountingProvider):
        def __init__(self):
            super().__init__(identity())
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    delegate = ClosableProvider()
    provider = CachedEmbeddingProvider(delegate, FileEmbeddingCache(tmp_path))

    provider.close()
    provider.close()

    assert delegate.close_calls == 1
