# Stage 9A Observability and Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不记录私有论文内容、不发起真实网络或付费调用的前提下，为每日流水线增加严格、原子、可离线验证的质量/成本/运行时观测，并用原创合成 30/15/5 基准生成 Stage 9B 唯一优化目标的测量证据。

**Architecture:** 新的 `observability` 包提供冻结的严格模型、注入式 `MetricsSession`、原子 `run-metrics.json` store、确定性质量计算和预算判定。`run_daily` 只在现有六个 callback 边界周围采样，并在 manifest 前完成 sidecar；Stage 3 通过可选 usage sink 记录每次生成尝试的整数 token 估算。离线 benchmark 使用真实项目排名/验证/viewer/metrics/quality 路径及 fake 边界，生成经隐私审计的聚合报告；Stage 9B 的具体计划仅在 profile 确定目标后编写。

**Tech Stack:** Python 3.13、Pydantic 2、pytest、标准库 `time`/`tracemalloc`/`cProfile`/`statistics`、现有 Stage 1–8 pipeline 与 GitHub Actions 静态测试。

## Global Constraints

- 每个行为变化先写失败测试并观察预期 RED，再写最小实现；不得补写“事后 RED”。
- 所有持久化模型 `extra="forbid"` 且冻结；只保存整数、固定枚举、版本、计数和 SHA-256，不保存 paper ID、标题、作者、摘要、prompt、模型响应、证据文本、URL、路径、hostname、username、动态异常或环境值。
- 观测失败只能产生固定 `metrics_collection_failed`/`metrics_persistence_failed` 类别，不得改变候选、分析、Stage 4 资格、viewer、Feishu 或总体内容状态。
- 默认 dry-run/offline 必须保持零真实网络、零付费、零发送；不读取真实 Zotero、LLM、Feishu 或用户反馈。
- `run-metrics.json` 固定在 run root，绝不进入 viewer/Pages；workflow 只能将其与私有 run manifest 作为显式文件上传。
- 价格仅来自显式整数配置；未配置时必须是 `not_configured`/`null`，不得表述为零成本。
- Stage 9A 不优化生产代码。只有 baseline/profile 报告提交后，才为最大合格热点编写 Stage 9B 精确计划。
- 不删除、跳过或弱化既有 Windows spawn 与 slow reranker 测试；所有结果区分基线限制和新回归。
- 每个逻辑单元完成 focused verification 后使用清晰小提交；不 push、merge、PR、修改 upstream 或删除任何已有工作树。

---

### Task 1: Define strict privacy-safe metric and budget contracts

**Files:**
- Create: `src/zotero_arxiv_daily/observability/__init__.py`
- Create: `src/zotero_arxiv_daily/observability/metrics.py`
- Create: `tests/observability/__init__.py`
- Create: `tests/observability/test_metrics.py`

**Interfaces:**
- `StageMetric` uses stage identity `stage9-metric-v1` and contains only `name`, `duration_ns`, integer work/byte/cache/retry/partial-failure counts, and unique allowlisted safe error codes.
- `PricingPolicy` stores non-negative integer micro-USD rates per one million tokens and a maximum batch estimate; `policy_hash` is derived from canonical JSON.
- `ModelUsageMetric` stores a SHA-256 model identity, call/attempt/token counts, configured output ceiling, `pricing_status`, optional integer `estimated_cost_micro_usd`, and optional pricing-policy hash.
- `PerformanceBudget` fixes 30/15/5, 5 successful calls, 15 attempts, 40,960 configured output tokens, zero offline network/paid calls, 2,000 ms median, 3,000 ms p95, and 256 MiB peak allocation.
- `BudgetEvaluation` stores only fixed boolean/limit/observed integer fields.
- `RunMetrics` validates ordered stage metrics and recomputes every aggregate from its children.

- [x] Write RED tests for valid minimal/full models, frozen instances, unknown fields, booleans masquerading as integers, negative/oversized values, unsafe/duplicate errors, invalid hashes, wrong stage order, aggregate mismatch, configured/unconfigured pricing, rounding, and canonical JSON determinism.
- [x] Add a recursive serialization privacy test rejecting keys or values matching credential, prompt, response, paper text/ID, URL, path, hostname, username, and dynamic exception markers.
- [x] Run `uv run pytest tests/observability/test_metrics.py -q` and confirm collection fails because the module does not exist.
- [x] Implement `StrictModel` subclasses, safe validators, canonical hash helpers, cost calculation, budget constants, and aggregate cross-field validation without free-form metadata.
- [x] Rerun the focused test plus `tests/pipeline/test_daily_schemas.py -q`; commit `feat: define privacy-safe run metrics`.

### Task 2: Add deterministic collection and atomic fixed-root persistence

**Files:**
- Create: `src/zotero_arxiv_daily/observability/collector.py`
- Create: `src/zotero_arxiv_daily/observability/store.py`
- Create: `tests/observability/test_collector.py`
- Create: `tests/observability/test_store.py`

**Interfaces:**
- `BoundedTokenEstimator.estimate(text: str) -> int` returns a deterministic bounded integer and retains no text.
- `MetricsSession` accepts injected `monotonic_ns`, `peak_memory_sampler`, `token_estimator`, and pricing policy; `stage(name, ...)` records exactly one metric even when the wrapped callback raises.
- `MetricsSession.record_model_attempt(...)` accepts text only transiently, hashes the supplied model identity, and persists integers only; cache hits do not increment paid calls.
- `MetricsSession.finalize(...) -> RunMetrics` freezes collection and rejects duplicate/missing stages or aggregate mismatch.
- `MetricsWriter(run_root)` always writes `run-metrics.json`; `write(metrics) -> MetricsWriteResult` validates canonical UTF-8 JSON and returns its SHA-256.

- [x] Write RED collector tests with fake clocks/samplers for success, exception timing, partial failures, cache/retry accounting, one successful call after retry, cache-hit zero calls, estimator bounds, pricing configured/unconfigured, finalize-once, and no retained input text.
- [x] Write RED store tests for exact filename, canonical round trip/hash, size/version/identity mismatch, corrupt existing destination, same-directory temp, flush/fsync, atomic replace, directory sync where supported, cleanup after replace failure, traversal impossibility, unsafe symlink/junction/reparse ancestor/destination, UNC, and foreign Windows drive.
- [x] Run both files and confirm missing-module RED.
- [x] Implement the minimal injected collector and reuse `pipeline.artifacts.resolve_within` plus `atomic_write_bytes`; extend the atomic helper only if the directory-sync test proves a shared change is necessary.
- [x] Rerun both focused files and `tests/pipeline/test_daily_artifacts.py -q`; commit `feat: collect and atomically store run metrics`.

### Task 3: Instrument Stage 3 generation without changing analysis semantics

**Files:**
- Modify: `src/zotero_arxiv_daily/analysis/analyzer.py`
- Modify: `src/zotero_arxiv_daily/analysis/client.py`
- Modify: `tests/analysis/test_analyzer.py`
- Modify: `tests/analysis/test_client.py`
- Modify: `tests/pipeline/test_analysis.py`

**Interfaces:**
- `AnalysisDependencies` gains injectable `monotonic: Callable[[], float]` and optional `usage_sink: AnalysisUsageSink`; existing callers receive behavior-preserving defaults.
- `AnalysisUsageSink.record_attempt(request, response, *, model_identity, succeeded)` is invoked once per real client attempt and never on a cache hit.
- `_generate_with_bounded_retry` reports unsuccessful and successful attempts while preserving exact existing backoff, exception translation, cache identity, expensive-call count, and response validation.
- All `processing_seconds` calculations use the injected monotonic function, including fixed-error results.

- [x] Write RED tests proving deterministic processing time, retry attempt sequence, successful/failed response accounting, cache-hit zero usage, sink failure isolation, and absence of prompt/response in the sink’s serialized result.
- [x] Run the three focused files and confirm failures at the missing injection/observer behavior.
- [x] Add the narrow protocol/default fields and observational calls; catch sink exceptions at the boundary and expose only fixed, aggregate-only data to `MetricsSession`.
- [x] Rerun focused tests and the full `tests/analysis tests/pipeline/test_analysis.py -q`; commit `feat: observe bounded analysis usage`.

### Task 4: Integrate metrics sidecar and manifest schema 1.1

**Files:**
- Modify: `src/zotero_arxiv_daily/pipeline/daily_schemas.py`
- Modify: `src/zotero_arxiv_daily/pipeline/daily.py`
- Modify: `tests/pipeline/test_daily_schemas.py`
- Modify: `tests/pipeline/test_daily.py`
- Modify: `tests/pipeline/test_daily_cli.py`
- Modify: `tests/pipeline/test_daily_artifacts.py`

**Interfaces:**
- `MetricsRunResult` has status `success|failed|skipped`, optional sidecar SHA-256, and fixed safe error codes with strict cross-field rules.
- `RunManifest` becomes schema `1.1`, pipeline `stage9-v1`, adds `metrics`, and preserves all Stage 7 status/count/static-site/Feishu invariants.
- `DailyDependencies` gains a `MetricsSession` and `MetricsWriter` boundary; all offline/fixture/production factories construct them explicitly.
- `run_daily` times all six existing stages. `_finish` finalizes/writes metrics first, then writes the manifest; metrics failure leaves content status and viewer intact and records only a fixed result.
- CLI has no arbitrary metrics path and always uses `<run-root>/run-metrics.json`.

- [x] Write RED schema tests for success/failure/skipped metrics contracts, wrong digest/status, schema/pipeline version, privacy, and unchanged content-status derivation.
- [x] Extend daily tests for complete/empty/partial paths, Stage 4 blocking, viewer success + Feishu failure, viewer failure + Feishu skipped, dry-run, duplicate delivery, metrics collection failure, metrics persistence failure, and exact six-stage timing.
- [x] Extend CLI/artifact tests for fixed sidecar path, zero network/paid/send in fixture mode, safe summary, no sidecar in viewer, atomic manifest after sidecar, and sidecar hash agreement.
- [x] Run the four focused files and confirm expected RED.
- [x] Implement the schema evolution and one common `_finish` collection path; do not duplicate early-return logic or change `_derive_run_status`.
- [x] Rerun all `tests/pipeline/test_daily*.py tests/observability -q`; commit `feat: integrate stage 9 run metrics`.

### Task 5: Implement deterministic aggregate quality evaluation

**Files:**
- Create: `src/zotero_arxiv_daily/observability/quality.py`
- Create: `tests/observability/test_quality.py`

**Interfaces:**
- Private input records use opaque in-memory synthetic IDs; `QualityEvaluation` serializes aggregate integers and fixture/evaluator hashes only.
- `evaluate_quality(ranking_labels, evidence_labels, required_fields, budget) -> QualityEvaluation` computes precision@5, recall@15, NDCG@5, evidence precision/recall, unsupported-claim rate, and missing-required-field rate in parts per million.
- Duplicate/unknown IDs, inconsistent domains, non-canonical ordering, and invalid counts fail closed.
- Empty denominator rules are explicit: a rate whose failure set is empty is perfect only when its complete expected domain is valid; otherwise construction fails.

- [x] Write RED tests using hand-computed perfect/imperfect cases, rank ties/order changes, duplicate/unknown IDs, empty valid domains, inconsistent claims/evidence/fields, exact ppm rounding, strict privacy serialization, and each quality budget boundary.
- [x] Run `uv run pytest tests/observability/test_quality.py -q` and confirm missing-module RED.
- [x] Implement integer/rational metric math and deterministic validators without numpy/pandas or serialized IDs.
- [x] Rerun focused tests; commit `feat: evaluate aggregate pipeline quality`.

### Task 6: Add original synthetic 30/15/5 benchmark fixture

**Files:**
- Create: `tests/fixtures/benchmarks/stage9/PROVENANCE.md`
- Create: `tests/fixtures/benchmarks/stage9/LICENSE.txt`
- Create: `tests/fixtures/benchmarks/stage9/candidates.json`
- Create: `tests/fixtures/benchmarks/stage9/quality-labels.json`
- Create: `tests/fixtures/benchmarks/stage9/analyses.json`
- Create: `tests/benchmarks/__init__.py`
- Create: `tests/benchmarks/test_stage9_fixture.py`

**Interfaces:**
- Fixture contains exactly 30 synthetic candidates, 15 ordered ranking labels, and 5 validated analysis/evidence outcomes.
- IDs use a repository-specific synthetic namespace and cannot parse as arXiv/Zotero identifiers or URLs.
- `PROVENANCE.md` states the fixture was independently created for this repository; `LICENSE.txt` contains the approved `SPDX-License-Identifier: CC0-1.0` notice.
- Fixture audit rejects real-looking authors, titles, abstracts, URLs, prompts, responses, secrets, private paths, and unbounded text.

- [ ] Write RED shape/provenance/license/privacy tests before adding fixture payloads.
- [ ] Run `uv run pytest tests/benchmarks/test_stage9_fixture.py -q` and confirm missing-fixture RED.
- [ ] Add the smallest original synthetic records needed to exercise real ranking, validation and viewer paths; use neutral tokens rather than copied paper prose.
- [ ] Rerun focused fixture and existing schema/validator/viewer tests; commit `test: add original stage 9 benchmark fixture`.

### Task 7: Build the reproducible offline benchmark and budget gate

**Files:**
- Create: `tools/benchmarks/__init__.py`
- Create: `tools/benchmarks/run_stage9_benchmark.py`
- Create: `src/zotero_arxiv_daily/observability/benchmark.py`
- Create: `tests/benchmarks/test_stage9_benchmark.py`
- Modify: `.gitignore`

**Interfaces:**
- `BenchmarkReport` stores report/fixture/code hashes, Python major/minor, OS family, at least nine sorted warm observations, median/p95/quartiles, peak allocation, quality, structural cost, cache results, network/paid-call counts, and budget verdict.
- `run_benchmark(...)` uses deterministic fake providers, fake sleep/network counters, isolated temporary output, real project ranking/validation/viewer/metrics/quality code, and alternating warmed repetitions.
- CLI `python -m tools.benchmarks.run_stage9_benchmark --output outputs/stage9/baseline.json` uses `allow_abbrev=False`, returns nonzero on a failed budget, and prints only fixed status/count/duration fields.
- Raw generated reports remain ignored; the benchmark never imports a local embedding model or constructs a real HTTP/LLM/Feishu client.

- [ ] Write RED tests for exact 30/15/5, at least nine observations, deterministic non-timing fields, percentile/quartile math, generous runtime/memory thresholds, zero network/paid calls, configured output ceiling ≤40,960, attempts ≤15, calls ≤5, budget nonzero exit, report privacy, and no viewer contamination.
- [ ] Add monkeypatch tripwires for `httpx.Client`, OpenAI construction, socket creation, real sleep, and Feishu send.
- [ ] Run `uv run pytest tests/benchmarks/test_stage9_benchmark.py -q` and confirm missing-module RED.
- [ ] Implement the benchmark runner, strict report model, budget evaluator and safe CLI; add only generated `outputs/stage9/` to `.gitignore`.
- [ ] Rerun all `tests/benchmarks tests/observability -q`; commit `feat: add offline stage 9 benchmark gate`.

### Task 8: Keep workflow uploads private and add a bounded CI gate

**Files:**
- Modify: `.github/workflows/personal-paper-daily.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/workflows/test_personal_paper_daily.py`
- Create: `tests/workflows/test_stage9_ci.py`

**Interfaces:**
- Daily workflow uploads only the two exact private files `outputs/daily/run-manifest.json` and `outputs/daily/run-metrics.json` in its run artifact.
- Pages upload remains exactly `outputs/daily/viewer`; the metrics file can never satisfy or enter `pages_ready`.
- CI runs deterministic Stage 9 schema/quality/benchmark-budget tests with an explicit timeout and retains minimal permissions, concurrency, full-SHA pins, and `persist-credentials: false`.

- [ ] Write RED static tests for exact private upload allowlist, no directory-wide run upload, viewer-only Pages path, metrics hash verification, no metrics cache, no secrets/environment dump, trigger/concurrency/timeouts/SHA pins, and bounded Stage 9 CI command.
- [ ] Run `uv run pytest tests/workflows -q` and confirm the expected missing Stage 9 workflow behavior.
- [ ] Make the minimal YAML changes without floating actions, writes to upstream, secret-bearing command output, `set -x`, `printenv`, or schedule send-default changes.
- [ ] Rerun workflow tests and parse both YAML files through the existing static loader; commit `ci: validate private stage 9 metrics`.

### Task 9: Generate, audit, and commit the Stage 9A baseline/profile decision

**Files:**
- Create: `docs/benchmarks/2026-07-26-stage9-baseline.json`
- Create: `docs/benchmarks/2026-07-26-stage9-baseline.md`
- Create: `tests/benchmarks/test_stage9_profile.py`
- Modify: `src/zotero_arxiv_daily/observability/benchmark.py`

**Interfaces:**
- `select_optimization_target(profile_rows) -> ProfileTargetDecision` accepts project-only aggregate rows, excludes startup/pytest/fixture/external/sleep/fake-boundary frames, requires ≥20% time or allocation share, and sorts by largest share, cumulative time, then fully qualified symbol.
- Tracked JSON contains only reviewed aggregates/hashes and the selected symbol/file-relative path; no absolute path, raw profiler dump, environment, ID, or text.
- Markdown records the exact command, repetition count, distribution, budgets, limitations, and evidence-based Stage 9B target.

- [ ] Write RED selection tests for eligibility threshold, exclusions, tie-breaks, no eligible target, unsafe path/symbol rejection, and deterministic canonical output.
- [ ] Run the profile test and confirm missing selector RED.
- [ ] Implement only selector/report contracts, then rerun focused tests.
- [ ] Run the offline benchmark with at least nine warm repetitions under `cProfile` and `tracemalloc`; keep raw profiler/report files under ignored `outputs/stage9/`.
- [ ] Audit the generated aggregate report for forbidden keys/values, absolute paths, credentials, private IDs/text, network/paid calls, exact 30/15/5 shape, quality 1,000,000 ppm/0 ppm rates, and all budget verdicts.
- [ ] Copy only canonical reviewed aggregates into the two tracked report files; run their static tests and `git diff --check`.
- [ ] Commit `perf: record stage 9 baseline and target`; stop Stage 9A production changes here.

### Task 10: Review Stage 9A and create the exact Stage 9B optimization plan

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/BASELINE.md`
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: this plan’s status/checklists
- Create after target selection: `docs/superpowers/plans/2026-07-26-stage-9b-<safe-target-name>.md`

**Required Stage 9B plan content:**
- Exact selected symbol and source/test files from the committed profile decision.
- A focused correctness-equivalence test and a failing performance/allocation regression test before implementation.
- At least nine alternating paired repetitions, ≥10% median time or allocation improvement, optimized p95 ≤105% of baseline, and byte/structure-equivalent outputs.
- Explicit prohibitions on model/provider/prompt/schema/parser/validator/renderer/cache/privacy semantic changes.
- One optimization only, its implementation identity/version impact, rollback commit boundary, and full final verification.

- [ ] Run Stage 9A focused tests, Stage 4–8 regressions, default pytest, compileall, workflow validation, diff check, and tracked secret/private/cache/archive/large-file scans.
- [ ] Run fixture daily dry-run, audit viewer plus the exact manifest/metrics artifacts, and confirm zero network/paid/send.
- [ ] Request an independent whole-branch Stage 9A review against `d00b9ab`; reproduce every confirmed Critical/Important finding with a failing focused test before repair.
- [ ] Update architecture/baseline/implementation docs with only observed results, known Windows/slow-suite limitations, rollback, and external operations not performed.
- [ ] Write and self-review the target-specific Stage 9B plan against the committed baseline; require exact files/symbols/commands, measured target selection, and no semantic shortcut.
- [ ] Commit Stage 9A review/documentation and the Stage 9B plan as separate logical commits, confirm a clean worktree, then execute Stage 9B directly under `superpowers:executing-plans`.

## Stage 9 final verification (executed after Stage 9B)

- [ ] `uv sync --frozen`
- [ ] Stage 9 focused schema/collector/store/quality/benchmark/profile/optimization tests
- [ ] Stage 8/7/6/5/4 related regressions
- [ ] Default `pytest -q`
- [ ] Explicit `pytest -m "slow or not slow" -q` under a recorded bounded timeout
- [ ] `python -m compileall -q src`
- [ ] Workflow YAML/static safety validation
- [ ] `git diff --check`
- [ ] Tracked secret/private/cache/archive/large-file scan
- [ ] Dry-run fixture daily CLI and exact sidecar hash audit
- [ ] Generated viewer/run artifact content audit
- [ ] Benchmark privacy/budget audit and paired before/after verification
- [ ] Independent whole-branch final review, focused RED repairs for confirmed findings, and clean-worktree confirmation
