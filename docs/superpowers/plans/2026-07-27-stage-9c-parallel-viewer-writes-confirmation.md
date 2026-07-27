# Stage 9C Parallel Viewer Writes Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不使用既有性能观察作最终判定的前提下，以一次预先固定的 99 对 AB/BA 测量独立确认或拒绝八 worker 并行 viewer 原子写入候选。

**Architecture:** Stage 9B 的 15 对正式观察（`80,858 ppm`）按原预声明规则判定失败；其后 31 对通过结果仅作为探索性信息，不进入确认判定。Stage 9C 在任何新数据产生前固定样本量、单次命令、双门槛、语义等价条件和失败处置；确认运行只允许执行一次，不得因结果不利而追加、删减或重跑样本。

**Tech Stack:** Python 3.13、现有 `run_stage9_paired_comparison` CLI、Pydantic 2、pytest、fixture/fake/no-send 30/15/5 benchmark。

## Global Constraints

- 确认样本固定为恰好 99 对；不得提前停止、追加样本、重跑择优或更改 worker 数。
- 顺序保持既有确定性 AB/BA 交替，每个 variant 使用 fresh isolated root，并在 timed region 外 warm up。
- cProfile 不得进入 timed region；tracemalloc 必须对两种模式对称并恢复调用者初始状态。
- 通过必须同时满足 median improvement `>= 100,000 ppm`、optimized p95 ratio `<= 1,050,000 ppm`、唯一 semantic equivalence hash 和 `pair_count == 99`。
- 输出、质量、Stage 4 eligibility、成本、缓存、重试、部分失败、manifest、artifact、隐私和 frontend 语义必须完全一致。
- 禁止网络、付费模型、真实 Zotero、真实 Feishu、GitHub dispatch、push、PR、merge、upstream 变更和 worktree 删除。
- 原始输出只写入 ignored `outputs/stage9/confirmation-99/`；tracked evidence 仅保留审计后的整数聚合和固定 hash。
- 若确认失败，候选必须标记为 rejected，production 默认恢复 sequential；不得再次测量该候选，后续只能基于原始 profile 的下一个合格目标建立新计划。

---

### Task 1: Freeze the independent protocol before data collection

**Files:**
- Create: `docs/superpowers/plans/2026-07-27-stage-9c-parallel-viewer-writes-confirmation.md`

**Interfaces:**
- Consumes: `tools/benchmarks/run_stage9_benchmark.py --comparison-output --pairs`
- Produces: immutable confirmation command and verdict contract

- [x] Record the 15-pair failure and classify the later 31-pair pass as exploratory only.
- [x] Fix `pairs=99`, one invocation, unchanged thresholds and no optional stopping before generating new data.
- [x] Record both pass and failure consequences, including sequential rollback on failure.
- [x] Commit this protocol before running the command.

### Task 2: Execute the confirmation exactly once

**Files:**
- Create ignored: `outputs/stage9/confirmation-99/result.json`
- Create ignored: `outputs/stage9/confirmation-99/runs/`

**Interfaces:**
- Consumes: committed Task 1 protocol and repository fixture identities
- Produces: one canonical `OptimizationComparison`

- [x] Confirm the protocol commit is an ancestor of HEAD and the result path does not exist.
- [x] Run exactly once:

```powershell
.venv\Scripts\python.exe tools/benchmarks/run_stage9_benchmark.py `
  --fixture-root tests/fixtures/benchmarks/stage9 `
  --daily-fixture tests/fixtures/evidence/stage4_golden.json `
  --work-root outputs/stage9/confirmation-99/runs `
  --comparison-output outputs/stage9/confirmation-99/result.json `
  --pairs 99
```

- [x] Do not invoke the confirmation command again regardless of exit code.
- [x] Validate canonical JSON with `OptimizationComparison`, exact pair count, sorted positive observations, recomputed median/p95/ppm, and one 64-character equivalence hash.
- [x] Audit exact 30/15/5, five publications, zero network/paid/send, and absence of credential, private identifier, dynamic exception, path and generated content fields.

### Task 3: Apply the predeclared verdict and record evidence

**Files:**
- Modify: `docs/benchmarks/2026-07-26-stage9-parallel-viewer-writes.json`
- Modify: `docs/benchmarks/2026-07-26-stage9-parallel-viewer-writes.md`
- Modify: `tests/benchmarks/test_stage9_optimization.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/BASELINE.md`
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/superpowers/plans/2026-07-26-stage-9b-static-viewer-builder-parallel-writes.md`
- Modify: this plan

**Interfaces:**
- Consumes: the single Task 2 result
- Produces: either confirmed production optimization or rejected candidate with sequential rollback

- [x] If and only if all confirmation gates pass, replace tracked performance verdict fields with the 99-pair result while retaining both earlier observations as historical non-verdict evidence.
- [x] Evaluate the predeclared failure branch; it is not applicable because both confirmation gates and semantic identity passed.
- [x] Update the static evidence test to require `confirmation_protocol_version`, `pair_count == 99`, exact gates and the historical 15/31 classifications.
- [ ] Run benchmark/viewer focused suites, compileall, privacy scan and `git diff --check`.
- [ ] Request independent rereview of the protocol ancestry, single-result provenance and verdict.
- [ ] Commit the observed verdict and evidence without rewriting or deleting the protocol commit.
