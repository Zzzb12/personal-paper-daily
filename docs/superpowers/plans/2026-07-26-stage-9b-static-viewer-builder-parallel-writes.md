# Stage 9B StaticViewerBuilder Parallel Writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对 profile 选中的 `zotero_arxiv_daily.viewer.builder:build:19` 实施一个且仅一个优化：并发执行 viewer 中互不依赖的原子文本写入，使九对交替测量的完整 30/15/5 benchmark median 至少改善 10%，optimized p95 不超过 baseline p95 的 105%，同时保持全部输出、hash、质量、成本、缓存与隐私语义不变。

**Architecture:** `AtomicOutputRoot.write_many_text` 先串行验证所有目标、拒绝重复并建立受控父目录，再通过可注入的有界 executor 并发调用现有 same-directory temporary→flush→fsync→`os.replace` 单文件原语。`StaticViewerBuilder` 先完整渲染页面与静态资源，在一个 batch 中写入非 manifest 文件；只有 batch 全部成功后才顺序写最后的 `build-manifest.json` 并清理 stale files。保留显式 sequential reference 仅供 paired benchmark，production 默认使用 parallel。Stage 4、renderer、HTML/CSS/JS、GSAP、artifact audit 和 Pages 边界均不改变。

**Tech Stack:** Python 3.13、`concurrent.futures.ThreadPoolExecutor`、Pydantic 2、pytest、现有 viewer/daily/benchmark。Frontend Design、GSAP Core 与 GSAP Performance 已用于边界核对；由于无视觉或动效变化，不新增 GSAP runtime。

**Measured status:** 原预声明 15-pair 正式结果仅改善 `80,858 ppm`，因此本计划的性能 verdict 为 failed；31-pair 结果只作 exploratory non-verdict。最终是否接受同一实现由在新数据前独立提交的 Stage 9C 99-pair confirmation 计划判定，不追溯修改本计划的失败结论。

## Global Constraints

- 只允许这一项 viewer write scheduling 优化；不得顺带修改 renderer、模板、CSS、JS、反馈行为、模型、prompt、schema、validator 或 artifact hash 算法。
- 每个行为变化必须先观察 focused RED。
- 每个目标仍使用同目录临时文件、flush、fsync、atomic replace 和失败清理；不得以关闭 durability 换性能。
- 在创建任何 worker 前串行完成相对路径、Windows/UNC/drive、symlink/reparse、重复目标和父目录检查。
- executor、worker 数和单文件 write boundary 可注入；测试不依赖真实线程时序。
- 任一 worker 失败必须等待/收集全部已启动 future，清理各自临时文件，不写 manifest，不删除上一版已发布文件，不持久化动态异常。
- 页面、静态资源、build manifest、viewer artifact hash、RunManifest、RunMetrics、QualityEvaluation 和 cache identity 必须与 sequential reference 相同。
- paired 测量至少九对，顺序按 AB/BA 交替，fresh isolated run roots；raw 结果留在 ignored `outputs/stage9/`。
- 若 median 改善不足 10% 或 p95 超过 105%，记录证据并停止，不弱化门槛；回到下一个合格 profile 目标。

---

### Task 1: Add a validated bounded batch-write primitive

**Files:**
- Modify: `src/zotero_arxiv_daily/viewer/filesystem.py`
- Modify: `tests/viewer/test_filesystem.py`

**Interfaces:**
- `AtomicOutputRoot.write_many_text(entries, *, max_workers=4, executor_factory=ThreadPoolExecutor) -> tuple[Path, ...]`
- `entries` is an ordered tuple of `(PurePosixPath, str)`; returned paths preserve input order.
- `write_text` and batch workers share one `_write_target_atomic(target, content)` implementation.

- [x] Write RED tests for exact ordered outputs, one/many/empty input, duplicate target, traversal/UNC/drive/link targets, max worker bounds, prevalidation before executor creation, executor injection, and byte equality with sequential `write_text`.
- [x] Write RED failure tests proving all submitted futures are joined, no manifest-like last entry is partially published by the caller, every temporary is cleaned, and dynamic worker exceptions are not serialized.
- [x] Run `uv run pytest tests/viewer/test_filesystem.py -q` and confirm missing batch API RED.
- [x] Implement prevalidation and bounded concurrent scheduling while preserving the existing per-file atomic primitive unchanged.
- [x] Rerun filesystem tests plus `tests/viewer/test_builder.py -q`; commit `perf: batch independent atomic viewer writes`.

### Task 2: Batch non-manifest viewer writes with a sequential reference

**Files:**
- Modify: `src/zotero_arxiv_daily/viewer/builder.py`
- Modify: `tests/viewer/test_builder.py`
- Modify: `tests/viewer/test_renderer.py`
- Modify: `tests/pipeline/test_daily_artifacts.py`

**Interfaces:**
- `StaticViewerBuilder(..., parallel_writes: bool = True, executor_factory=ThreadPoolExecutor)` controls scheduling only.
- Builder renders all page/index/static strings first, calls one batch write for non-manifest text, then writes `build-manifest.json` last.
- `parallel_writes=False` loops through the same ordered entries with `write_text` and is used only as the paired reference.

- [x] Write RED tests that capture call order and prove manifest-last, no manifest after a failed batch, bounded worker propagation, empty viewer behavior, stale cleanup after success only, and sequential/parallel byte-for-byte directory equality.
- [x] Extend renderer/browser/static tests to assert HTML/CSS/JS and CSP remain identical and no GSAP dependency/script is introduced.
- [x] Extend artifact tests to require identical `BuildManifest`, file count, byte count and artifact hash for both modes.
- [x] Run the focused viewer/artifact group and observe RED.
- [x] Implement one ordered render batch and the narrow scheduling flag without changing any rendered string or public schema.
- [x] Rerun all `tests/viewer tests/pipeline/test_daily_artifacts.py -q`; commit `perf: parallelize viewer publication writes`.

### Task 3: Add deterministic paired comparison contracts

**Files:**
- Modify: `src/zotero_arxiv_daily/observability/benchmark.py`
- Modify: `tools/benchmarks/run_stage9_benchmark.py`
- Create: `tests/benchmarks/test_stage9_optimization.py`

**Interfaces:**
- `OptimizationComparison` stores sorted baseline/optimized observations, median/p95, improvement ppm, p95 ratio ppm, output-equivalence hashes and verdict only.
- `run_stage9_paired_comparison(..., pairs=9)` alternates baseline-first and optimized-first, uses fresh roots, and verifies every pair’s viewer bytes/structure, quality, metrics, cost and artifact hash before accepting timing.
- CLI subcommand/flag writes a canonical ignored comparison and exits nonzero unless median improvement ≥100,000 ppm and optimized p95 ratio ≤1,050,000 ppm.

- [x] Write RED math tests for exactly nine pairs, sorting, half-up ppm, 9.9999% failure, 10% pass, p95 105% pass/one-unit failure, and invalid/mismatched observations.
- [x] Write RED fake-clock ordering tests for AB/BA alternation and fresh roots.
- [x] Write RED equivalence tests that reject any HTML/JS/CSS/build-manifest/artifact/quality/cost/cache/privacy difference before computing a performance verdict.
- [x] Run the focused optimization file and confirm missing comparison API RED.
- [x] Implement the strict comparison model, paired runner and safe CLI output without adding the relative gate to hosted CI.
- [x] Rerun all benchmark/observability tests; commit `test: gate measured viewer optimization`.

### Task 4: Measure the single optimization and record evidence

**Files:**
- Create after measurement: `docs/benchmarks/2026-07-26-stage9-parallel-viewer-writes.json`
- Create after measurement: `docs/benchmarks/2026-07-26-stage9-parallel-viewer-writes.md`
- Modify: `tests/benchmarks/test_stage9_optimization.py`

- [x] Run at least nine warm alternating pairs with cProfile disabled during timed regions and tracemalloc applied identically to both modes.
- [x] Audit raw comparison for exact 30/15/5, zero network/paid/send, identical quality/cache/metrics/viewer bytes and hashes, and absence of private/dynamic fields.
- [x] Require median improvement ≥10% and optimized p95 ≤105%; the formal 15-pair result failed, so this plan does not claim a passing performance verdict.
- [x] Profile optimized steady state outside timed paired runs and confirm no new project hotspot or allocation regression invalidates the change.
- [x] Copy only reviewed aggregate evidence into tracked JSON/Markdown; add a static privacy/identity test.
- [x] Commit the observed Stage 9B evidence; classify the later 31-pair pass as exploratory only after independent review.

### Task 5: Full verification, independent review, and documentation

**Files:**
- Modify only for confirmed review findings: implementation/tests above.
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/BASELINE.md`
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/superpowers/plans/2026-07-26-stage-9-quality-cost-runtime.md`
- Modify: this plan status/checklists.

- [x] Run frozen sync, all Stage 9 tests, Stage 4–8 regressions, default pytest, and bounded explicit slow/non-slow suite without weakening known failures.
- [x] Run compileall, YAML/workflow safety tests, diff check, tracked secret/private/cache/archive/large-file scan, fixture daily dry-run, run sidecar hash audit, viewer artifact audit, and benchmark privacy/budget audit.
- [x] Request independent whole-branch review against `d00b9ab`; reproduce every confirmed Critical/Important finding with a focused failing test before repair.
- [x] Document actual before/after distributions, Frontend Design/GSAP boundary decision, known Windows/slow-suite limitations, rollback and external operations not performed.
- [x] Commit review fixes and final evidence in separate logical commits; confirm clean worktree and do not push, merge, create PR, alter upstream or delete any worktree.
