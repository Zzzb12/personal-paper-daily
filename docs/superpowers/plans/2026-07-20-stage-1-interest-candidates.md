# Stage 1 Interest and Candidate Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a strict, offline-testable Stage 1 pipeline that reads approved Zotero interests, retrieves arXiv metadata only, ranks candidates with cached embeddings, and persists a validated 30/15/5 candidate batch.

**Architecture:** Add Pydantic records and backward-compatible adapters around upstream Zotero, arXiv, and reranker behavior. All network, clock, embedding, and filesystem boundaries are injected; the legacy `Executor` stays the default path. Candidate JSON contains public arXiv metadata and privacy-safe fingerprints, never raw Zotero records, full text, PDFs, or LLM output.

**Tech Stack:** Python 3.13, uv, Pydantic 2, httpx, pyzotero, feedparser, NumPy, Hydra/OmegaConf, pytest.

## Global Constraints

- Execute Stage 1 only; do not add PDF, evidence, LLM, viewer, Feishu, feedback, email, or Actions behavior.
- Preserve `Executor`, `Paper`, `CorpusPaper`, retriever/reranker registries, and the legacy email entry point.
- Default includes are `PaperDaily/00-Seeds/**`, `PaperDaily/03-Read/**`, and `PaperDaily/04-Favorite/**`; `PaperDaily/99-Exclude/**` always wins.
- Default categories are exactly `cs.CV`, `cs.LG`, and `cs.AI`.
- Enforce `candidate_pool_size=30`, `llm_rerank_limit=15`, and `full_analysis_limit=5` in schemas and orchestration.
- Stage 1 keeps `RankingRecord.llm_score=None` and calls no LLM or paid API.
- Every HTTP client has explicit connect/read/write/pool timeouts and bounded transient retry.
- Runtime candidate files, Zotero-derived data, and embedding caches remain ignored and untracked.
- Every production change begins with a focused failing test; never weaken upstream tests.
- Use `F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe` when global `uv` is unavailable.

---

### Task 1: Add strict Stage 1 schemas

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/zotero_arxiv_daily/analysis/__init__.py`
- Create: `src/zotero_arxiv_daily/analysis/schemas.py`
- Create: `tests/analysis/__init__.py`
- Create: `tests/analysis/test_stage1_schemas.py`

**Interfaces:**
- Consumes: Pydantic 2.
- Produces: `InterestPaper`, `CandidatePaper`, `RankingModelVersions`, `RankingRecord`, `CandidateCounts`, and `CandidateBatch`.

- [ ] **Step 1: Add direct dependencies**

Run:

```powershell
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe add "pydantic>=2.12,<3" "httpx>=0.28,<1"
```

Expected: `pyproject.toml` and `uv.lock` add direct Pydantic/httpx requirements without changing Python 3.13.

- [ ] **Step 2: Write failing schema tests**

Create tests that construct a two-paper batch and assert deterministic round-trip, forbidden unknown fields, timezone-aware dates, unique IDs, consecutive ranks, subset/prefix rules, `llm_score is None`, and 30/15/5 limits. Core test shape:

```python
def test_candidate_batch_rejects_non_prefix_selection(candidate_batch):
    payload = candidate_batch.model_dump(mode="json")
    payload["selected_for_full_analysis"] = [payload["candidates"][1]["paper_id"]]
    with pytest.raises(ValidationError, match="ordered prefix"):
        CandidateBatch.model_validate(payload)


def test_candidate_batch_json_is_deterministic(candidate_batch):
    first = candidate_batch.to_deterministic_json()
    second = CandidateBatch.model_validate_json(first).to_deterministic_json()
    assert first == second
    assert '"llm_score": null' in first
```

- [ ] **Step 3: Confirm red**

Run:

```powershell
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run pytest tests/analysis/test_stage1_schemas.py -q
```

Expected: collection/import failure because `analysis.schemas` does not exist.

- [ ] **Step 4: Implement minimal strict models**

Implement `StrictModel` with `ConfigDict(extra="forbid", frozen=True)`, UTC-aware datetime validators, normalized arXiv IDs/categories, and this batch invariant:

```python
class CandidateBatch(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime
    retrieved_at: datetime
    categories: tuple[str, ...]
    config_hash: str
    interest_corpus_fingerprint: str
    candidates: tuple[CandidatePaper, ...]
    rankings: tuple[RankingRecord, ...]
    selected_for_llm: tuple[str, ...]
    selected_for_full_analysis: tuple[str, ...]
    counts: CandidateCounts

    @model_validator(mode="after")
    def validate_relationships(self) -> "CandidateBatch":
        ids = tuple(p.paper_id for p in self.candidates)
        if len(ids) > 30 or len(set(ids)) != len(ids):
            raise ValueError("candidates must be unique and capped at 30")
        if tuple(r.paper_id for r in self.rankings) != ids:
            raise ValueError("rankings must follow candidate order")
        if tuple(r.rank for r in self.rankings) != tuple(range(1, len(ids) + 1)):
            raise ValueError("ranks must be consecutive")
        if self.selected_for_llm != ids[: min(15, len(ids))]:
            raise ValueError("selected_for_llm must be an ordered prefix")
        if self.selected_for_full_analysis != ids[: min(5, len(ids))]:
            raise ValueError("selected_for_full_analysis must be an ordered prefix")
        if any(r.llm_score is not None for r in self.rankings):
            raise ValueError("Stage 1 llm_score must be null")
        return self

    def to_deterministic_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"
```

- [ ] **Step 5: Confirm green and regression**

Run:

```powershell
F:\Yan_0\Video_generaton\PaperDaily\.stage0-tools\Scripts\uv.exe run pytest tests/analysis/test_stage1_schemas.py tests/test_protocol.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml uv.lock src/zotero_arxiv_daily/analysis tests/analysis
git commit -m "test: define stage 1 candidate schemas"
```

---

### Task 2: Implement the Zotero interest provider

**Files:**
- Create: `src/zotero_arxiv_daily/interest/__init__.py`
- Create: `src/zotero_arxiv_daily/interest/base.py`
- Create: `src/zotero_arxiv_daily/interest/zotero.py`
- Create: `tests/interest/__init__.py`
- Create: `tests/interest/test_zotero_provider.py`

**Interfaces:**
- Consumes: `InterestPaper`, existing `glob_match`, injected `ZoteroGateway`.
- Produces: `InterestReadResult`, `ZoteroCollection`, `ZoteroItem`, `ZoteroInterestProvider.read()`.

- [ ] **Step 1: Write failing provider tests**

Use an in-memory gateway and assert nested paths, all include roots, exclusion precedence, pagination-equivalent aggregation, zero-collection exclusion, missing abstract/date, missing parent, and cycles:

```python
def test_exclude_path_wins(fake_gateway):
    fake_gateway.add_item(
        key="item-1",
        title="Caching DiT",
        abstract="abstract",
        collection_keys=("favorite", "excluded"),
    )
    result = provider(fake_gateway).read()
    assert result.papers == ()
    assert result.excluded_count == 1


def test_parent_cycle_is_isolated(fake_gateway):
    fake_gateway.add_collection("a", "A", "b")
    fake_gateway.add_collection("b", "B", "a")
    fake_gateway.add_item(collection_keys=("a",))
    result = provider(fake_gateway).read()
    assert result.papers == ()
    assert {issue.code for issue in result.issues} == {"collection_cycle"}
```

- [ ] **Step 2: Confirm red**

Run `uv run pytest tests/interest/test_zotero_provider.py -q`.

Expected: import failure because the interest package is absent.

- [ ] **Step 3: Define strict gateway records and protocol**

Implement in `interest/base.py`:

```python
class ZoteroGateway(Protocol):
    def list_collections(self) -> tuple[ZoteroCollection, ...]:
        raise NotImplementedError

    def list_items(self) -> tuple[ZoteroItem, ...]:
        raise NotImplementedError


class InterestReadResult(StrictModel):
    papers: tuple[InterestPaper, ...]
    corpus_fingerprint: str
    eligible_count: int
    excluded_count: int
    invalid_count: int
    issues: tuple[InterestIssue, ...]
```

- [ ] **Step 4: Implement deterministic path resolution/filtering**

`ZoteroInterestProvider` accepts include/exclude tuples and resolves parents with memoization plus a per-walk `visited` set. Reuse `glob_match`, sort papers by `paper_id`, and compute SHA-256 over canonical eligible records and pattern configuration. Never include record text in an issue message.

```python
def _resolve_path(self, key: str, collections: Mapping[str, ZoteroCollection]) -> str:
    names: list[str] = []
    visited: set[str] = set()
    current: str | None = key
    while current:
        if current in visited:
            raise CollectionPathError("collection_cycle")
        visited.add(current)
        collection = collections.get(current)
        if collection is None:
            raise CollectionPathError("missing_collection")
        names.append(collection.name)
        current = collection.parent_key
    return "/".join(reversed(names))
```

- [ ] **Step 5: Add production pyzotero gateway tests and implementation**

Inject a fake callable/client and verify retry decisions without network:

```python
@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_gateway_retries_transient_status(status, fake_transport):
    fake_transport.responses = [response(status), response(200, json=[])]
    gateway = PyzoteroGateway(
        library_id="1",
        api_key="test-key",
        client=fake_transport.client,
        sleeper=lambda _: None,
    )
    assert gateway.list_collections() == ()
    assert fake_transport.call_count == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_gateway_does_not_retry_permanent_status(status, fake_transport):
    fake_transport.responses = [response(status)]
    gateway = PyzoteroGateway(
        library_id="1",
        api_key="test-key",
        client=fake_transport.client,
        sleeper=lambda _: None,
    )
    with pytest.raises(httpx.HTTPStatusError):
        gateway.list_collections()
    assert fake_transport.call_count == 1
```

Production construction injects `httpx.Client(timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10))` into pyzotero and retries at most three attempts, honoring a bounded `Retry-After`.

- [ ] **Step 6: Confirm green and upstream compatibility**

Run:

```powershell
uv run pytest tests/interest tests/test_executor.py tests/test_utils.py -q
```

Expected: new tests pass; existing filtering/glob tests retain baseline behavior.

- [ ] **Step 7: Commit**

```powershell
git add src/zotero_arxiv_daily/interest tests/interest
git commit -m "feat: add Zotero interest provider"
```

---

### Task 3: Add metadata-only arXiv retrieval

**Files:**
- Modify: `src/zotero_arxiv_daily/retriever/arxiv_retriever.py`
- Create: `tests/retriever/test_arxiv_metadata.py`

**Interfaces:**
- Consumes: `CandidatePaper`, injected `ArxivMetadataGateway`.
- Produces: `ArxivMetadataRetriever.retrieve() -> ArxivMetadataResult` while preserving `ArxivRetriever.convert_to_paper()`.

- [ ] **Step 1: Write failing metadata and no-full-text tests**

```python
def test_metadata_retrieval_never_calls_full_text(monkeypatch, fake_gateway):
    for name in ("extract_text_from_tar", "extract_text_from_html", "extract_text_from_pdf"):
        monkeypatch.setattr(arxiv_retriever, name, Mock(side_effect=AssertionError(name)))
    result = ArxivMetadataRetriever(fake_gateway, categories=("cs.CV", "cs.LG", "cs.AI")).retrieve()
    assert len(result.candidates) == 1
    assert result.candidates[0].pdf_url.endswith(".pdf")


def test_duplicate_arxiv_versions_keep_latest(fake_gateway):
    fake_gateway.entries = (entry("2401.00001v1"), entry("2401.00001v3"))
    result = metadata_retriever(fake_gateway).retrieve()
    assert [(p.arxiv_id, p.version) for p in result.candidates] == [("2401.00001", 3)]
```

- [ ] **Step 2: Confirm red**

Run `uv run pytest tests/retriever/test_arxiv_metadata.py -q`.

Expected: `ArxivMetadataRetriever` is missing.

- [ ] **Step 3: Implement entry normalization and deduplication**

Add strict `ArxivMetadataEntry`, `ArxivMetadataIssue`, and `ArxivMetadataResult`. Normalize URLs such as `https://arxiv.org/abs/2401.00001v2`, legacy IDs, authors, categories, URLs, and UTC datetimes. Deduplicate by base ID using `(version, updated_at)` and sort output by `paper_id` before ranking.

```python
_ARXIV_ID_RE = re.compile(r"(?:arxiv:|/abs/)?(?P<base>[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v(?P<version>\d+))?$", re.I)


def choose_latest(entries: Iterable[ArxivMetadataEntry]) -> tuple[ArxivMetadataEntry, ...]:
    latest: dict[str, ArxivMetadataEntry] = {}
    for entry in entries:
        previous = latest.get(entry.arxiv_id)
        if previous is None or (entry.version, entry.updated_at) > (previous.version, previous.updated_at):
            latest[entry.arxiv_id] = entry
    return tuple(latest[key] for key in sorted(latest))
```

- [ ] **Step 4: Implement bounded HTTP gateway**

Use injected `httpx.Client` to request RSS/Atom bytes with explicit timeouts. Retry only `httpx.TimeoutException`, `httpx.TransportError`, 408/425/429, and 5xx; honor bounded `Retry-After`; parse with `feedparser.parse(response.content)`. Do not call any source/HTML/PDF URL.

```python
def _get_atom(self, url: str) -> feedparser.FeedParserDict:
    for attempt in range(1, self._retry.max_attempts + 1):
        response: httpx.Response | None = None
        try:
            response = self._client.get(url)
            if response.status_code not in {408, 425, 429} and response.status_code < 500:
                response.raise_for_status()
                return feedparser.parse(response.content)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
            transient = isinstance(exc, (httpx.TimeoutException, httpx.TransportError))
            transient = transient or response is not None and (
                response.status_code in {408, 425, 429} or response.status_code >= 500
            )
            if attempt == self._retry.max_attempts or not transient:
                raise
            self._sleeper(self._retry.delay_for(attempt, response))
    raise RuntimeError("unreachable retry loop")
```

- [ ] **Step 5: Test retry and malformed-entry isolation**

Add cases for transient success, permanent 4xx, retry exhaustion, cross-list disabled/enabled, malformed ID/date/link, and deterministic issue counts.

- [ ] **Step 6: Confirm green and legacy regression**

Run:

```powershell
uv run pytest tests/retriever/test_arxiv_metadata.py tests/retriever/test_arxiv_retriever.py tests/retriever/test_base_retriever.py -q
```

Expected: metadata tests pass; the two documented Windows hard-timeout tests may retain their exact baseline failure, with no new failure.

- [ ] **Step 7: Commit**

```powershell
git add src/zotero_arxiv_daily/retriever/arxiv_retriever.py tests/retriever/test_arxiv_metadata.py
git commit -m "feat: retrieve metadata-only arXiv candidates"
```

---

### Task 4: Add deterministic ranking and embedding cache

**Files:**
- Modify: `src/zotero_arxiv_daily/reranker/base.py`
- Create: `src/zotero_arxiv_daily/candidates/__init__.py`
- Create: `src/zotero_arxiv_daily/candidates/ranking.py`
- Create: `tests/candidates/__init__.py`
- Create: `tests/candidates/test_ranking.py`
- Create: `tests/candidates/test_embedding_cache.py`

**Interfaces:**
- Consumes: `InterestPaper`, `CandidatePaper`, injected `EmbeddingProvider`.
- Produces: `weighted_similarity_scores`, `EmbeddingIdentity`, `EmbeddingCache`, `CachedEmbeddingProvider`, `SentenceTransformerEmbeddingProvider`, `RankedCandidates`, and `CandidateRanker.rank()`.

- [ ] **Step 1: Write failing scoring compatibility tests**

```python
def test_weighted_scores_match_legacy_formula():
    similarity = np.array([[1.0, 0.5], [0.2, 0.9]])
    actual = weighted_similarity_scores(similarity)
    weights = 1 / (1 + np.log10(np.arange(2) + 1))
    expected = (similarity * (weights / weights.sum())).sum(axis=1) * 10
    np.testing.assert_allclose(actual, expected)


def test_rank_ties_by_stable_paper_id(fake_embeddings):
    ranked = ranker(fake_embeddings.equal_scores()).rank(candidates_reversed(), interests())
    assert [r.paper_id for r in ranked.rankings] == ["arxiv:2401.00001", "arxiv:2401.00002"]
```

- [ ] **Step 2: Confirm red**

Run `uv run pytest tests/candidates/test_ranking.py -q`.

Expected: candidates package/helper missing.

- [ ] **Step 3: Extract pure legacy scoring helper**

```python
def weighted_similarity_scores(similarity: np.ndarray) -> np.ndarray:
    if similarity.ndim != 2 or similarity.shape[1] == 0:
        raise ValueError("similarity must have a non-empty corpus axis")
    if not np.isfinite(similarity).all():
        raise ValueError("similarity contains non-finite values")
    weights = 1 / (1 + np.log10(np.arange(similarity.shape[1]) + 1))
    weights = weights / weights.sum()
    return (similarity * weights).sum(axis=1) * 10
```

Make `BaseReranker.rerank()` call this helper while preserving mutation and descending stable behavior expected by existing tests.

- [ ] **Step 4: Implement structured ranker**

`EmbeddingProvider.encode(texts: tuple[str, ...]) -> np.ndarray` is a protocol. Normalize candidate/interest text as `title + "\n\n" + abstract`, validate matrix dimensions, normalize vectors, compute cosine similarity, call the shared helper, and sort by `(-score, paper_id)`. Return capped candidates/rankings and 15/5 prefixes with `llm_score=None`.

```python
class EmbeddingProvider(Protocol):
    identity: EmbeddingIdentity

    def encode(self, texts: tuple[str, ...]) -> np.ndarray:
        raise NotImplementedError


def cosine_matrix(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    if left.ndim != 2 or right.ndim != 2 or left.shape[1] != right.shape[1]:
        raise ValueError("embedding matrices must share one feature dimension")
    left_norm = np.linalg.norm(left, axis=1, keepdims=True)
    right_norm = np.linalg.norm(right, axis=1, keepdims=True)
    if np.any(left_norm == 0) or np.any(right_norm == 0):
        raise ValueError("zero embedding vector")
    return (left / left_norm) @ (right / right_norm).T
```

The production local provider imports SentenceTransformer only inside `encode`, exposes an `EmbeddingIdentity` built from configuration, and converts the result to a finite two-dimensional NumPy array. Unit and acceptance tests inject a deterministic provider and never construct this class.

```python
class SentenceTransformerEmbeddingProvider:
    def __init__(self, identity: EmbeddingIdentity, encode_kwargs: Mapping[str, object]):
        self.identity = identity
        self._encode_kwargs = dict(encode_kwargs)

    def encode(self, texts: tuple[str, ...]) -> np.ndarray:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(self.identity.model, trust_remote_code=True)
        vectors = np.asarray(model.encode(list(texts), **self._encode_kwargs), dtype=np.float32)
        if vectors.ndim != 2 or not np.isfinite(vectors).all():
            raise ValueError("embedding provider returned an invalid matrix")
        return vectors
```

- [ ] **Step 5: Write cache tests, confirm red, implement cache**

Test hit/miss, duplicate text, changed model/settings/schema, corrupt manifest, shape mismatch, no-pickle array loading, and atomic replacement. Use pytest `tmp_path`. Cache key is SHA-256 over canonical JSON containing text hash/provider/model/task settings/schema version.

```python
def cache_key(text: str, identity: EmbeddingIdentity) -> str:
    payload = {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "identity": identity.model_dump(mode="json"),
        "cache_version": "1",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 6: Confirm green and reranker regression**

Run:

```powershell
uv run pytest tests/candidates tests/reranker/test_base_reranker.py tests/reranker/test_api_reranker.py -q
```

Expected: all selected tests pass without model download or API calls.

- [ ] **Step 7: Commit**

```powershell
git add src/zotero_arxiv_daily/reranker/base.py src/zotero_arxiv_daily/candidates tests/candidates
git commit -m "feat: rank candidates with versioned embedding cache"
```

---

### Task 5: Persist candidate batches atomically

**Files:**
- Create: `src/zotero_arxiv_daily/candidates/store.py`
- Create: `tests/candidates/test_store.py`

**Interfaces:**
- Consumes: validated `CandidateBatch`.
- Produces: `StoredCandidateBatch`, `CandidateStore.write(batch) -> StoredCandidateBatch`, and `read(run_id) -> CandidateBatch`.

- [ ] **Step 1: Write failing store tests**

```python
def test_write_round_trips_validated_batch(tmp_path, candidate_batch):
    stored = CandidateStore(tmp_path).write(candidate_batch)
    assert stored.sha256 == hashlib.sha256(stored.path.read_bytes()).hexdigest()
    assert CandidateStore(tmp_path).read(candidate_batch.run_id) == candidate_batch


def test_failed_replace_preserves_previous_file(tmp_path, candidate_batch, monkeypatch):
    store = CandidateStore(tmp_path)
    original = store.write(candidate_batch).path.read_bytes()
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        store.write(updated_batch(candidate_batch))
    assert store.path_for(candidate_batch.run_id).read_bytes() == original
```

- [ ] **Step 2: Confirm red**

Run `uv run pytest tests/candidates/test_store.py -q`.

Expected: `CandidateStore` missing.

- [ ] **Step 3: Implement validated atomic writes**

Write deterministic UTF-8 JSON to a same-directory temporary file, flush and `os.fsync`, re-read with `CandidateBatch.model_validate_json`, then `os.replace`. Always remove an abandoned temporary file in `finally`. Reject path separators in `run_id`. Reads validate schema and never return partial data.

```python
def write(self, batch: CandidateBatch) -> StoredCandidateBatch:
    target = self.path_for(batch.run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    try:
        data = batch.to_deterministic_json().encode("utf-8")
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        CandidateBatch.model_validate_json(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, target)
        return StoredCandidateBatch(path=target, sha256=hashlib.sha256(data).hexdigest())
    finally:
        temporary.unlink(missing_ok=True)
```

- [ ] **Step 4: Confirm green**

Run `uv run pytest tests/candidates/test_store.py -q`.

Expected: all store tests pass and create files only under pytest temporary directories.

- [ ] **Step 5: Commit**

```powershell
git add src/zotero_arxiv_daily/candidates/store.py tests/candidates/test_store.py
git commit -m "feat: persist validated candidate batches"
```

---

### Task 6: Compose the offline-testable candidate pipeline and configuration

**Files:**
- Create: `src/zotero_arxiv_daily/pipeline/__init__.py`
- Create: `src/zotero_arxiv_daily/pipeline/candidates.py`
- Modify: `config/base.yaml`
- Modify: `config/custom.yaml`
- Create: `tests/pipeline/__init__.py`
- Create: `tests/pipeline/test_candidates.py`
- Create: `tests/fixtures/stage1_offline.json`

**Interfaces:**
- Consumes: provider/retriever/ranker/store interfaces from Tasks 2–5.
- Produces: `CandidatePipelineSettings`, `CandidatePipelineDependencies`, `build_candidate_batch()`, and CLI `main()`.

- [ ] **Step 1: Write failing offline integration tests**

```python
def test_offline_pipeline_enforces_limits_and_privacy(fake_dependencies, frozen_clock):
    batch = build_candidate_batch(default_settings(), fake_dependencies, frozen_clock)
    assert len(batch.candidates) == 30
    assert len(batch.selected_for_llm) == 15
    assert len(batch.selected_for_full_analysis) == 5
    serialized = batch.to_deterministic_json()
    assert "zotero-item-key" not in serialized
    assert "PaperDaily/" not in serialized
    assert '"llm_score": null' in serialized


def test_dry_run_does_not_write(fake_dependencies, frozen_clock):
    fake_dependencies.store = Mock()
    build_candidate_batch(default_settings(dry_run=True), fake_dependencies, frozen_clock)
    fake_dependencies.store.write.assert_not_called()
```

Add spies that raise if OpenAI, PDF/full-text helpers, SMTP, or network clients are constructed.

- [ ] **Step 2: Confirm red**

Run `uv run pytest tests/pipeline/test_candidates.py -q`.

Expected: pipeline module missing.

- [ ] **Step 3: Implement orchestration**

Define immutable settings/dependencies. Flow is `interest.read → metadata.retrieve → rank → CandidateBatch validation → optional store.write`. Use an injected UTC clock and run-ID factory. Fail before embeddings when the interest corpus is empty. Preserve safe retrieved/deduplicated/invalid/excluded counts.

```python
def build_candidate_batch(
    settings: CandidatePipelineSettings,
    dependencies: CandidatePipelineDependencies,
    clock: Callable[[], datetime],
) -> CandidateBatch:
    interests = dependencies.interest_provider.read()
    if not interests.papers:
        raise EmptyInterestCorpusError("no eligible Zotero interest papers")
    metadata = dependencies.arxiv_retriever.retrieve()
    ranked = dependencies.ranker.rank(metadata.candidates, interests.papers, settings)
    batch = _assemble_batch(settings, interests, metadata, ranked, clock())
    if not settings.dry_run:
        dependencies.store.write(batch)
    return batch
```

- [ ] **Step 4: Add additive config**

Add exact defaults under `candidate_pipeline` and set Zotero/arXiv product defaults:

```yaml
zotero:
  include_path:
    - PaperDaily/00-Seeds/**
    - PaperDaily/03-Read/**
    - PaperDaily/04-Favorite/**
  ignore_path:
    - PaperDaily/99-Exclude/**

source:
  arxiv:
    category: [cs.CV, cs.LG, cs.AI]
    include_cross_list: false

candidate_pipeline:
  candidate_pool_size: 30
  llm_rerank_limit: 15
  full_analysis_limit: 5
  output_dir: data/candidates
  embedding_cache_dir: cache/embeddings
  request_timeout: {connect: 10, read: 30, write: 10, pool: 10}
  retry: {max_attempts: 3, backoff_seconds: 1, max_retry_after_seconds: 60}
```

Keep credentials as environment references in `config/custom.yaml`; remove `cs.CL` from the personal default without changing the legacy entry point.

- [ ] **Step 5: Implement safe offline CLI fixture mode**

Support:

```powershell
uv run python -m zotero_arxiv_daily.pipeline.candidates --dry-run --offline-fixture tests/fixtures/stage1_offline.json
```

The fixture contains only synthetic collections/items/arXiv metadata and deterministic embeddings. Dry-run validates the batch, writes nothing, and prints counts/schema version only.

- [ ] **Step 6: Confirm green and run no-secret acceptance**

Run the pipeline tests and the command above with credential variables absent. Expected: exit 0, no network, no candidate/cache file, no secret value in output.

- [ ] **Step 7: Run related regression tests**

```powershell
uv run pytest tests/analysis tests/interest tests/candidates tests/pipeline tests/test_executor.py tests/test_main.py tests/reranker/test_base_reranker.py tests/retriever/test_arxiv_metadata.py -q
```

Expected: all Stage 1 tests pass; no model/API/PDF/LLM call.

- [ ] **Step 8: Commit**

```powershell
git add config/base.yaml config/custom.yaml src/zotero_arxiv_daily/pipeline tests/pipeline tests/fixtures/stage1_offline.json
git commit -m "feat: build offline candidate batch pipeline"
```

---

### Task 7: Verify Stage 1, review, and record completion

**Files:**
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/BASELINE.md` only if new factual environment/test findings require it.

**Interfaces:**
- Consumes: complete Stage 1 diff and verification evidence.
- Produces: factual Stage 1 completion status and reviewed commit sequence.

- [ ] **Step 1: Run all Stage 1 tests**

```powershell
uv run pytest tests/analysis tests/interest tests/candidates tests/pipeline tests/retriever/test_arxiv_metadata.py -q
```

Expected: all Stage 1 tests pass.

- [ ] **Step 2: Run default regression suite**

```powershell
uv run pytest
```

Expected: no new failure. The two documented Windows spawn-timeout tests may remain failed and must be reported exactly.

- [ ] **Step 3: Run complete configured suite**

```powershell
uv run pytest -m "" --cov=src/zotero_arxiv_daily --cov-report=term-missing
```

Expected: record exact result; the slow embedding test may require Hugging Face and may retain its documented network failure. Do not claim pass if it fails.

- [ ] **Step 4: Verify stage boundary and ignored artifacts**

Fail if the diff adds `documents/`, analyzer/validator, viewer, delivery, feedback, workflow, PDF, cache, `.env`, or private runtime data. Verify `data/candidates/example.json` and `cache/embeddings/example.npy` are ignored while `.env.example` remains trackable.

- [ ] **Step 5: Scan for secrets and large/private files**

Scan tracked/pending text without printing matched values. Fail on private keys, secret-shaped assignments, tracked PDFs/ZIPs/Zotero exports/caches, or files over 5 MiB.

- [ ] **Step 6: Invoke `superpowers:verification-before-completion`**

Run its required fresh checks and retain exact output evidence.

- [ ] **Step 7: Invoke `superpowers:requesting-code-review`**

Request an independent read-only review against the approved design, roadmap, AGENTS rules, full diff, and test evidence. Resolve every confirmed finding using TDD/systematic debugging as applicable, then reverify.

- [ ] **Step 8: Mark Stage 1 complete only after verification/review**

Add directly below the Stage 1 heading:

```markdown
**Status:** Completed on 2026-07-20; verification results and known baseline failures are recorded in the Stage 1 completion notes and Git history.
```

- [ ] **Step 9: Commit completion documentation**

```powershell
git add docs/IMPLEMENTATION_PLAN.md docs/BASELINE.md
git commit -m "docs: record stage 1 verification"
```

- [ ] **Step 10: Invoke `superpowers:finishing-a-development-branch`**

Do not merge, create a PR, delete work, or push upstream. Since the user prohibited PR creation, preserve the reviewed feature branch; push only if a verified private `origin` exists.
