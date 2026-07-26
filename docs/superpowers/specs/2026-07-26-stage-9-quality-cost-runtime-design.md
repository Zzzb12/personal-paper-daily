# Stage 9 Quality, Cost, and Runtime Design

**Status:** Approved direction on 2026-07-26. This specification implements the
recommended privacy-safe metrics, deterministic offline benchmark, and
measurement-selected single optimization approach.

**Base:** Stage 8 final commit
`d00b9ab665b6ad1a9240c221da1a067f1317da78`.

## Context

Stages 1–8 provide a strict, offline-testable daily pipeline with versioned
artifacts, cache identities, validation gates, static-reader output, optional
Feishu delivery, GitHub Actions automation, and private reader feedback. Existing
schemas expose counts and selected per-paper processing times, but there is no
single privacy-safe run metric, no reproducible 30/15/5 quality/cost/runtime
benchmark, and no evidence-based optimization report.

The Windows baseline still has two one-second multiprocessing-spawn failures. The
unfiltered slow suite can block while resolving an uncached Hugging Face local
reranker. Stage 9 must preserve those tests and distinguish them from new
regressions rather than weakening or skipping them.

## Chosen approach and decomposition

Stage 9 is one product stage with two evidence-ordered implementation phases:

1. **Stage 9A — measurement foundation:** strict metric schemas, injected
   collection, atomic sidecar output, deterministic quality evaluation, an
   original synthetic 30/15/5 benchmark, fixed budgets, and a versioned baseline
   report.
2. **Stage 9B — measured optimization:** profile the Stage 9A benchmark, select
   exactly one eligible local bottleneck by a deterministic rule, add a failing
   regression budget for it, implement one bounded optimization, and record
   before/after repeated-run distributions.

This ordering is binding. No optimization may be selected from intuition, and no
model/provider/parser/validator/evidence behavior may be changed to manufacture a
performance win.

Rejected alternatives:

- A real-model/PDF end-to-end benchmark is too dependent on network access,
  Hugging Face cache state, provider cost, and machine-specific startup time for
  stable CI.
- Metrics without a measured optimization would not satisfy Stage 9.
- Hard-coded current provider prices would become stale and would turn a
  reproducible offline gate into a time-dependent external assumption.

## Goals

- Make stage latency, counts, bytes, retries, cache effectiveness, model-call
  count, token estimates, and configured cost estimates observable without
  emitting private content.
- Evaluate ranking relevance, evidence precision/recall, unsupported-claim rate,
  and required-field completeness on deterministic synthetic labels.
- Establish explicit structural, quality, runtime, memory, network, and cost
  budgets for the 30/15/5 defaults.
- Select and optimize one measured local bottleneck with before/after evidence.
- Add only stable, offline regression gates to CI.

## Non-goals

- No real Zotero read, paper/model download, paid LLM call, Feishu send, GitHub
  dispatch, or Pages deployment.
- No model/provider switch, prompt change, evidence-rule relaxation, Stage 4
  bypass, or ranking-semantic change.
- No production telemetry service, database, dashboard, account, or remote
  collector.
- No hostname, username, absolute path, paper ID, title, abstract, prompt,
  response text, evidence text, dynamic exception, credential, or environment
  dump in metrics or reports.
- No claim that an offline microbenchmark predicts hosted Actions or real-PDF
  performance exactly.

## Architecture

### 1. Privacy-safe observability models

Create `src/zotero_arxiv_daily/observability/metrics.py` and package exports.
Every model is frozen and `extra="forbid"`. Integer units are preferred over
floating-point values in persisted contracts.

`StageMetric` contains:

- version and one of the six existing stage names;
- `duration_ns`;
- input/output/byte/cache/retry/partial-failure counts;
- safe, allowlisted error codes only.

`ModelUsageMetric` contains:

- a model-identity SHA-256, never a key, endpoint, prompt, or model response;
- paid-call and attempt counts;
- estimated input tokens;
- configured output-token budget;
- estimated output tokens derived locally from returned text length/tokenization;
- optional estimated cost in integer micro-US-dollars only when an explicit
  pricing policy is configured.

`RunMetrics` contains:

- schema/collector/token-estimator versions;
- run ID, trigger, config hash, and optional artifact hash;
- ordered stage metrics;
- aggregate duration, bytes, cache/retry/failure counts;
- model usage;
- peak traced Python allocation bytes;
- a strict budget evaluation made only from the fields above.

Cross-field validators recompute all aggregates. Arbitrary labels, messages,
metadata dictionaries, URLs, paths, and free-form strings are prohibited.

### 2. Collection boundary

`MetricsSession` uses injected `monotonic_ns`, peak-memory sampler, and token
estimator boundaries. Production defaults use `time.perf_counter_ns` and a
bounded `tracemalloc` sampler; tests use deterministic fakes.

The daily pipeline records each existing stage around its current callback
boundary. Metrics collection is observational:

- it cannot change a stage result, retry decision, validation eligibility,
  viewer result, or Feishu result;
- a metrics failure produces only a fixed `metrics_collection_failed` category;
- successful viewer output remains available even when metric persistence fails;
- offline/dry-run collection performs no network or paid call.

Analysis token estimates are collected in the analyzer around actual generation
attempts. The estimator receives strings in memory but returns integers only and
never persists, logs, or exposes its input. Cache hits record zero paid calls.

### 3. Atomic sidecar

Write a fixed `run-metrics.json` beside `run-manifest.json`, outside the audited
viewer directory. There is no arbitrary metrics-output CLI path.

`MetricsWriter`:

- constrains the destination to the checked run root;
- rejects traversal, UNC/foreign-drive paths, symlink/reparse ancestors, an
  unsafe existing destination, over-size payloads, and unknown versions;
- writes a same-directory temporary file, flushes and `fsync`s it, atomically
  replaces the destination, syncs the directory where supported, and cleans up
  on failure;
- validates its own canonical UTF-8 JSON before replacement.

The daily workflow may upload this exact sidecar with the private run artifact,
but it is never copied into or uploaded as the Pages viewer artifact.

The Stage 9 run manifest evolves to schema `1.1` / pipeline identity
`stage9-v1` and adds a strict `metrics` result containing only
`success|failed|skipped`, the sidecar SHA-256, and fixed error codes. The metrics
sidecar is finalized before the manifest and may reference the run/config/artifact
hashes, but never the manifest hash, so there is no digest cycle. A failed metrics
result does not change an otherwise successful content status. Existing Stage 7
counts, stage/static-site/Feishu results, send gates, and publication semantics
remain unchanged.

### 4. Pricing and structural cost

Provider prices are not embedded in source. `PricingPolicy` accepts explicit
integer micro-US-dollar rates per one million input/output tokens plus a maximum
batch estimate. A policy hash is recorded; raw configuration is not.

If no pricing policy is configured, dollar cost is `null` with
`pricing_status="not_configured"`. The run still receives a structural cost
verdict from calls, attempts, and token ceilings. Documentation must never call a
missing dollar estimate zero cost.

The default 30/15/5 structural cost budget is:

- candidate pool: exactly 30 maximum;
- LLM-rerank selection: exactly 15 maximum;
- full analysis: exactly 5 maximum;
- successful analysis calls: at most 5;
- total analysis attempts: at most 15;
- configured output-token ceiling: at most 40,960;
- offline benchmark network calls: 0;
- offline benchmark paid calls: 0.

### 5. Deterministic quality evaluation

Create `src/zotero_arxiv_daily/observability/quality.py`.

`QualityEvaluation` records only aggregate integers:

- evaluated/relevant/retrieved counts;
- precision@5, recall@15, and NDCG@5 in parts per million;
- expected, accepted, supported, and rejected evidence/claim counts;
- evidence precision and recall in parts per million;
- unsupported-claim and missing-required-field rates in parts per million;
- fixture/evaluator version hashes and a budget verdict.

The evaluator consumes labeled in-memory records with opaque synthetic IDs but
never serializes those IDs. Division-by-zero behavior is explicit and tested.
Reordering labels, duplicates, unknown IDs, and inconsistent count domains fail
closed.

### 6. Synthetic benchmark and provenance

Create `tools/benchmarks/` and `tests/fixtures/benchmarks/stage9/`.

The fixture is original synthetic content created for this repository, not copied
paper text or code. Its directory contains a provenance notice and an
`SPDX-License-Identifier: CC0-1.0` notice approved as part of this design. It has:

- 30 synthetic candidate records;
- relevance labels covering the top 15 and a five-paper analysis subset;
- five synthetic validated analysis/evidence outcomes;
- no real arXiv/Zotero identifier, author, title, abstract, URL, prompt, or model
  output.

The benchmark uses deterministic fake providers, clients, clocks, sleeps, file
boundaries, and network counters. It exercises the real ranking, validation,
viewer, metric, and quality-evaluation paths without importing a local model or
calling an external service.

### 7. Benchmark report and stable budgets

`BenchmarkReport` records:

- report/fixture/code identity hashes;
- Python major/minor and OS family only, never hostname, username, executable
  path, or environment;
- at least nine warm, alternating repetitions;
- sorted raw duration/allocation observations plus median, p95, and quartiles;
- quality, structural cost, cache, and budget results;
- the selected hotspot and later before/after comparison.

Initial stable budgets:

- fixture shape: 30/15/5 exactly;
- quality: precision@5, recall@15, NDCG@5, evidence precision, and evidence recall
  each 1,000,000 ppm for the golden synthetic fixture;
- unsupported-claim and missing-required-field rates: 0 ppm;
- steady-state median runtime: at most 2,000 ms;
- steady-state p95 runtime: at most 3,000 ms;
- peak traced Python allocation: at most 256 MiB;
- offline network and paid calls: 0;
- all structural cost ceilings in the preceding section.

The absolute runtime/memory ceilings are intentionally generous CI safety rails.
The versioned report preserves the actual distribution; documentation does not
generalize it to real PDFs or Actions hardware.

### 8. Bottleneck selection and optimization gate

Profile only warmed, steady-state project code. Exclude interpreter/import
startup, pytest, fixture loading, external libraries, sleeping, and fake boundary
overhead.

An optimization target is eligible only when it:

- is inside `zotero_arxiv_daily`;
- accounts for at least 20% of measured cumulative project time or traced
  allocation;
- can be changed without modifying model/provider, prompt, schema meaning,
  parser mapping, validation rules, renderer output, artifact eligibility, or
  public/private boundaries.

Choose the largest eligible share; ties use cumulative time and then fully
qualified symbol name. Commit the baseline report and target decision before
implementation.

The optimized implementation must:

- have a focused failing performance/regression test first;
- improve the selected target median by at least 10% over at least nine
  alternating paired repetitions, or reduce its median peak allocation by at
  least 10%;
- keep optimized p95 no worse than 105% of baseline p95;
- produce byte/structure-equivalent correctness outputs and identical quality,
  evidence, cost, cache-identity, and privacy results.

If the first candidate cannot meet these rules without semantic change, record
the evidence and evaluate the next eligible measured target. Do not weaken a
budget to claim success.

## Error handling

- Persisted/logged errors are fixed safe categories only.
- Metric or report corruption, over-size, version mismatch, aggregate mismatch,
  unsafe path, or identity mismatch fails closed.
- A benchmark-budget failure returns a nonzero benchmark command exit and leaves
  its canonical report for diagnosis.
- A production metrics failure does not turn a successful content pipeline into
  a content failure and never deletes a manifest or viewer.

## CI and workflow boundary

CI adds a small offline Stage 9 schema/quality/benchmark-budget group with a
bounded timeout. All external actions remain pinned to full commit SHAs,
permissions remain minimal, checkout credentials remain disabled, and no
environment dump or secret-bearing command is added.

Only deterministic correctness and generous absolute safety budgets gate CI.
The paired optimization distribution is generated and versioned locally; it is
not rerun as a fragile microbenchmark on every hosted runner.

## Test strategy

Test-first coverage includes:

- strict schemas, cross-field aggregates, safe error codes, finite/bounded
  numeric values, unknown fields, and privacy-marker rejection;
- injected timing, stage partial failures, cache hits, retries, token estimation,
  pricing configured/unconfigured, and metrics failure isolation;
- atomic sidecar write, cleanup, corruption/over-size/version/identity mismatch,
  traversal, symlink/reparse, UNC, foreign drive, and fixed output path;
- quality math, duplicates, empty denominators, unsupported claims, missing
  fields, and absence of IDs/text in JSON;
- exact 30/15/5 fixture provenance and zero network/paid calls;
- benchmark reproducibility, report privacy, budget pass/fail, and no viewer
  contamination;
- profile selection determinism and before/after distribution comparison;
- Stage 4 eligibility, Stage 7/8 workflow/artifact privacy, fixture daily CLI,
  and existing Stage 1–8 regression groups.

Final verification repeats frozen sync, focused Stage 9 tests, Stage 4–8
regressions, default pytest, the explicit slow/non-slow suite with a bounded
recorded outcome, compileall, workflow/YAML validation, diff check, tracked
secret/private/cache/archive/large-file scans, benchmark report audit, fixture
daily dry-run, generated artifact audit, and independent whole-branch review.

## Documentation and outputs

- Add a detailed implementation plan under `docs/superpowers/plans/`.
- Add a versioned benchmark report under `docs/benchmarks/`.
- Update `docs/ARCHITECTURE.md`, `docs/BASELINE.md`, and
  `docs/IMPLEMENTATION_PLAN.md` with actual measurements and limitations.
- Generated raw reports remain under ignored `outputs/`; only reviewed,
  privacy-audited aggregate evidence is tracked.

## Rollback

Metrics collection and sidecar writing are independently disableable without
changing pipeline semantics. Revert the selected optimization separately and
restore its prior implementation identity. Invalidate only affected benchmark or
cache identities. Preserve before/after reports and private user state; never use
Pages, Actions cache, or Git history as a private-data backup.
