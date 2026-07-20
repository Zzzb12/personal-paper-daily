# Stage 1 Zotero Interest and Candidate Batch Design

**Date:** 2026-07-20
**Roadmap stage:** Stage 1 — Zotero interest reading and candidate paper JSON
**Status:** Design approach approved in conversation; written-spec review pending
**Scope owner:** `personal-paper-daily`

## Goal

Implement a private, testable, metadata-only recommendation boundary that:

1. reads the approved Zotero collection tree;
2. retrieves current arXiv metadata from `cs.CV`, `cs.LG`, and `cs.AI`;
3. ranks candidates against the interest corpus with cached embeddings;
4. records deterministic top-30, top-15, and top-5 selection decisions; and
5. persists a strictly validated candidate batch without downloading or parsing a PDF and without calling an LLM.

Stage 1 is complete only when its acceptance path runs with injected fake clients, no credentials, no network, and no paid service.

## Fixed product decisions

The following decisions come from `AGENTS.md`, `docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`, and `docs/IMPLEMENTATION_PLAN.md` and are not reopened here.

- Recursively include:
  - `PaperDaily/00-Seeds/**`
  - `PaperDaily/03-Read/**`
  - `PaperDaily/04-Favorite/**`
- Recursively exclude `PaperDaily/99-Exclude/**`; exclusion always wins.
- Default arXiv categories are `cs.CV`, `cs.LG`, and `cs.AI`.
- `candidate_pool_size = 30`.
- `llm_rerank_limit = 15`.
- `full_analysis_limit = 5`.
- Stage 1 never calls an LLM; `llm_score` remains `null`.
- Stage 1 never downloads tar sources, HTML full text, or PDFs.
- Runtime candidates, private Zotero-derived data, and embedding caches remain ignored by Git.

## Non-goals

- PDF download, parsing, page mapping, captions, figures, tables, or evidence.
- Chinese Insight/Method/Ablation analysis.
- Actual LLM reranking.
- Feishu, email changes, static viewer, feedback, or GitHub Actions work.
- bioRxiv or medRxiv ingestion.
- Replacing the existing `Executor`, `Paper`, `CorpusPaper`, retriever registry, reranker registry, or email workflow.
- Fixing the known upstream Windows one-second multiprocessing tests.
- Introducing a general cache framework for later PDF/LLM stages.

## Approaches considered

### A. Backward-compatible adapters — selected

Create new strict Stage 1 modules around the existing code, extract only a pure scoring helper for shared use, and leave the legacy `Executor` path operational.

Advantages:

- enforces the metadata/PDF cost boundary structurally;
- supports injected offline fakes at every network and embedding edge;
- minimizes regression risk to the upstream email workflow;
- allows strict Pydantic records without changing legacy dataclasses; and
- gives later stages a stable candidate contract.

Cost: several small adapter modules and compatibility tests are required.

### B. Extend `Paper` and `Executor` in place — rejected

This uses fewer files but couples metadata-only candidates to fields and behavior that currently generate full text, TLDR, affiliations, and email. A configuration mistake could cross the Stage 1 cost boundary, and strict candidate persistence would remain entangled with mutable dataclasses.

### C. Independent sidecar pipeline — rejected

A completely separate implementation would isolate Stage 1, but it would duplicate collection resolution, arXiv normalization, and ranking behavior instead of adapting the audited upstream core.

## Package layout

```text
src/zotero_arxiv_daily/
├── analysis/
│   ├── __init__.py
│   └── schemas.py
├── interest/
│   ├── __init__.py
│   ├── base.py
│   └── zotero.py
├── candidates/
│   ├── __init__.py
│   ├── ranking.py
│   └── store.py
├── pipeline/
│   ├── __init__.py
│   └── candidates.py
├── reranker/base.py                  # pure scoring helper, legacy API preserved
└── retriever/arxiv_retriever.py      # metadata adapter, legacy conversion preserved
```

Tests mirror these packages under `tests/analysis`, `tests/interest`, `tests/candidates`, and `tests/pipeline`.

## Dependency direction

```text
schemas
  ↑
interest provider     arXiv metadata adapter     embedding provider/cache
          \                  |                   /
           \                 |                  /
                    candidate pipeline
                            |
                     candidate store
```

- Schema modules import no network, persistence, or orchestration code.
- Providers depend on schemas and injected gateways only.
- The candidate store depends on schemas and a filesystem abstraction only.
- The pipeline composes providers; providers never import the pipeline.
- Neither the new modules nor their tests import PDF extraction or LLM clients.

## Strict Stage 1 records

Pydantic becomes a direct project dependency. Persisted models use `extra="forbid"`, timezone-aware UTC datetimes, explicit optional fields, and deterministic JSON serialization. The initial persisted schema version is `1.0`.

### `InterestPaper`

- `paper_id`: stable private Zotero item identity used only inside the private interest boundary.
- `title`: normalized non-empty title.
- `abstract`: normalized non-empty abstract.
- `collection_paths`: sorted unique resolved paths.
- `added_at`: timezone-aware Zotero added date.
- `feedback_weight`: fixed `1.0` in Stage 1.

Items with a missing title, blank abstract, invalid date, missing collection reference, or cyclic collection ancestry are isolated and counted as safe issues. They do not enter ranking. Items belonging to no collection do not match the include rules and are excluded.

### `CandidatePaper`

- `paper_id`: `arxiv:<base-id>` stable across arXiv versions.
- `arxiv_id`: normalized base arXiv ID without `vN`.
- `version`: positive integer parsed from the source ID, defaulting to `1` when absent.
- `title`, `authors`, `abstract`.
- `categories`: sorted unique category identifiers.
- `primary_category`.
- `published_at`, `updated_at`.
- `arxiv_url`, `pdf_url`, optional `code_url`.

The PDF URL is metadata only. No code path dereferences it in Stage 1.

### `RankingRecord`

- `paper_id`.
- `embedding_score`.
- `llm_score`: always `null` in Stage 1.
- `final_score`: equal to `embedding_score` in Stage 1.
- `rank`: one-based stable rank.
- `reason`: deterministic selection summary.
- `model_versions`: embedding provider identity, model, task/prompt settings, and scorer version.

### `CandidateBatch`

- `schema_version`.
- `run_id`.
- `created_at` and `retrieved_at`.
- default/actual categories.
- configuration hash and privacy-safe interest-corpus fingerprint.
- ranked `candidates`, capped at 30.
- one `RankingRecord` for every retained candidate.
- `selected_for_llm`: ordered subset capped at 15.
- `selected_for_full_analysis`: ordered subset capped at five.
- safe counts for retrieved, deduplicated, invalid, and excluded candidates.

Cross-record validators require unique IDs, identical candidate/ranking ID sets, consecutive ranks, and ordered selection lists that are subsets of retained candidates. The 30/15/5 bounds are enforced by validation, not only orchestration.

## Zotero interest provider

### Gateway boundary

`ZoteroGateway` exposes collection and eligible-item pagination without exposing credentials to domain code. `PyzoteroGateway` is the production adapter.

The installed pyzotero version accepts an injected `httpx.Client`. The production adapter therefore supplies explicit connect/read/write/pool timeouts and closes the client it owns. A bounded retry wrapper covers timeout/transport failures, HTTP 408/425/429, and 5xx responses, respects `Retry-After`, and never retries authentication or other permanent 4xx failures.

Tests use a fake gateway and never construct pyzotero or read environment values.

### Collection resolution

- Resolve collection paths iteratively with memoization.
- Track visited collection keys for every ancestry walk.
- Missing parents and cycles produce safe issues; affected paths are not invented.
- Normalize path separators to `/` without changing collection names.
- Preserve sorted unique paths on `InterestPaper`.

### Filtering and privacy

Existing `glob_match` behavior is reused. Inclusion is evaluated first; exclusion is then evaluated independently and always removes a matching paper.

The candidate batch never stores `InterestPaper` records, Zotero item keys, collection names, titles, or abstracts. It stores only a corpus fingerprint computed from canonicalized eligible interest records plus the include/exclude configuration hash. Runtime/private caches remain under ignored paths.

## arXiv metadata-only retrieval

The existing `ArxivRetriever.convert_to_paper` remains unchanged for the legacy email workflow. Stage 1 adds a separately callable metadata adapter in the same retriever module so metadata retrieval cannot fall through to `extract_text_from_tar`, `extract_text_from_html`, or `extract_text_from_pdf`.

`ArxivMetadataGateway` is injected. Its production implementation uses an HTTP client with explicit timeouts to retrieve Atom/RSS metadata and a bounded retry policy for transient failures. Tests provide canned parsed entries and spies that fail immediately if any full-text helper is invoked.

Normalization rules:

- accept only configured categories and the configured cross-list policy;
- normalize modern and legacy arXiv identifiers;
- deduplicate by base arXiv ID;
- retain the greatest explicit version, then the newest `updated_at` as a deterministic fallback;
- sort category and author representations deterministically;
- isolate malformed entries instead of failing the complete batch.

No source URL, HTML URL, or PDF URL is opened.

## Ranking and embedding cache

### Shared scoring core

Extract the weighted-similarity calculation from `BaseReranker.rerank` into a pure helper. The legacy method calls the helper and retains its current mutation/sort contract. Focused compatibility tests prove its existing scores and ordering are unchanged.

The Stage 1 structured ranker receives an injected `EmbeddingProvider` and never instantiates an API client itself. Acceptance tests use deterministic vectors. A production local provider may wrap SentenceTransformer, but no acceptance or regression command downloads a model.

### Cache

`EmbeddingCache` stores numeric arrays and a JSON manifest under `cache/embeddings/`, which remains ignored. Entries are keyed by:

- normalized text SHA-256;
- provider and model identity;
- task/prompt/encode settings;
- embedding dimension/dtype; and
- cache/schema version.

Array loading forbids pickle. A corrupt or shape-mismatched entry is quarantined as a miss. Writes use a temporary sibling followed by atomic replacement.

### Stable ordering

Final ordering uses descending score followed by ascending stable `paper_id`. This makes ties independent of gateway return order. Selection then takes ordered prefixes of 30, 15, and five. `llm_score` remains `null` and never affects Stage 1 ordering.

## Candidate store

The filesystem store writes validated JSON under ignored run data, defaulting to `data/candidates/<run-id>.json`.

Write flow:

1. validate the in-memory `CandidateBatch`;
2. serialize deterministic UTF-8 JSON with stable field/list ordering;
3. write and flush a temporary sibling file;
4. parse and validate the temporary file;
5. atomically replace the target; and
6. return the target path and content hash.

Reads validate schema version and the full payload. Corrupt or incompatible files are rejected and never partially loaded. Tests use pytest temporary directories, never the repository runtime path.

## Pipeline and dry-run

`build_candidate_batch(settings, dependencies, clock)` is the primary orchestration function. Dependencies contain the interest provider, arXiv metadata provider, embedding provider/cache, and optional store. The clock and run-ID factory are injected for deterministic tests.

The production CLI resolves configuration and credentials only at the process edge. An offline fixture mode supplies fake gateways and deterministic embeddings. `--dry-run` builds and validates the batch but performs no persistent write. A separate explicitly supplied output path may be used in tests or local fixture validation.

The offline acceptance command must succeed with all relevant credential environment variables absent. It must not import or call OpenAI, PDF extraction, SMTP, Feishu, or browser code.

## Configuration

`config/base.yaml` gains product defaults for:

- Zotero include/exclude paths;
- arXiv categories and cross-list behavior;
- 30/15/5 limits;
- timeout/retry policy;
- embedding model/task/cache identity; and
- candidate output path.

`config/custom.yaml` continues to resolve credentials from environment variables and contains no literal credential. Existing upstream keys remain available for the legacy Executor. Stage 1 configuration is additive and must not change the legacy default entry point.

## Error handling

- Zotero authentication failure stops the new pipeline without deleting a prior valid batch.
- A missing/cyclic collection or malformed Zotero item is isolated and counted.
- A transient arXiv request retries within the configured bound; a permanent query error fails retrieval clearly.
- A malformed arXiv entry is isolated and counted.
- An empty eligible interest corpus fails before embedding work with a safe diagnostic.
- Invalid vector shape, NaN, or non-finite score fails ranking rather than persisting a misleading order.
- Atomic-write failure leaves the previous valid candidate batch untouched.
- Logs contain counts, stable public arXiv IDs, and error categories, not credentials or private Zotero text.

## Test strategy

Every behavior begins with a focused failing test and follows red-green-refactor.

### Schema tests

- valid round trips and deterministic JSON;
- unknown fields, malformed IDs, naive datetimes, invalid categories, duplicate IDs, non-consecutive ranks, and invalid subsets;
- enforced 30/15/5 bounds;
- `llm_score` remains `null` in Stage 1.

### Zotero provider tests

- nested recursive paths and memoization;
- all three include paths;
- Exclude precedence;
- pagination;
- empty collections and zero-collection items;
- missing title/abstract/date;
- missing parent and parent cycle;
- timeout, retryable status, `Retry-After`, authentication failure, and retry exhaustion;
- candidate batch contains no private Zotero record.

### arXiv tests

- default categories and cross-list policy;
- modern/legacy ID and version normalization;
- duplicate/version resolution and deterministic ordering;
- malformed-entry isolation;
- bounded retry and permanent failure;
- spies proving tar/HTML/PDF helpers are never called.

### Ranking/cache tests

- exact weighted score compatibility with the existing reranker;
- deterministic tie ordering;
- empty corpus and invalid vectors;
- 30/15/5 selections;
- cache hit, miss, corrupt entry, shape mismatch, and version invalidation;
- fake provider call counts proving cache reuse.

### Store/pipeline tests

- atomic write and preservation of the prior file on failure;
- invalid/corrupt read rejection;
- complete offline fake-client run;
- dry-run performs no write;
- no credential, private Zotero payload, PDF/full-text path, or LLM result in output;
- compatibility regression for the existing Executor/reranker/retriever entry points.

## Acceptance verification

Before Stage 1 is marked complete:

1. run all new focused suites;
2. run the existing default pytest suite and distinguish the two documented Windows baseline failures from new failures;
3. run the complete configured coverage command if practical, recording the known network-dependent slow-test result honestly;
4. run the offline Stage 1 dry-run with credential variables absent;
5. scan the diff for Stage 2+ modules and calls;
6. scan tracked/pending files for credentials, PDFs, Zotero private data, candidate data, and caches;
7. verify representative runtime paths remain ignored;
8. review the complete diff independently and resolve confirmed findings; and
9. add an explicit Stage 1 completion status to `docs/IMPLEMENTATION_PLAN.md` only after verification succeeds.

## Rollback

The legacy `Executor` remains the default path. Rollback disables/removes the Stage 1 candidate CLI and new packages, restores the small tested adapters in retriever/reranker/config, and deletes only ignored candidate/cache artifacts. No Stage 2 schema or data exists to migrate.
