# Stage 7 GitHub Actions Daily Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一个默认离线/no-send 的版本化 CLI 安全串联 Stage 1–6，并通过最小权限 GitHub Actions 每日或手动执行、审核和可选部署静态站。

**Architecture:** `daily.py` 通过注入 runner 保持域逻辑不变；严格 schema 记录运行状态，cache/artifact 模块封装恢复与文件系统安全，持久 ledger 提供跨运行飞书幂等。Stage 4 固定在 viewer/Feishu 之前，两个交付目标独立结算。

**Tech Stack:** Python 3.13、Pydantic 2、PyYAML、pytest、GitHub Actions、现有 Stage 1–6 模块。

**Status:** Completed locally on 2026-07-22. Final commands, baseline failures,
review fixes, external operations not performed, and rollback are recorded in
`docs/BASELINE.md`.

## Global Constraints

- 默认 `dry-run` 和 `no-send`；fixture 验收零网络、零付费、零发送。
- 不保存 prompt、全文、凭据、Zotero 私有字段、动态异常原文或未验证公开输出。
- 所有 action 固定完整 SHA；workflow 无 push、visibility 修改或 secret/config 输出。
- Stage 4 是唯一发布资格边界；旧缓存不能绕过验证。
- 所有行为变化按 RED→GREEN，逻辑单元完成后小提交。

---

### Task 1: Define strict run and cache contracts

**Files:**
- Create: `src/zotero_arxiv_daily/pipeline/daily_schemas.py`
- Test: `tests/pipeline/test_daily_schemas.py`

**Interfaces:**
- Produces `RunManifest`, `StageRunResult`, `RunCounts`, `StaticSiteResult`, `FeishuRunResult`, and `WorkflowCacheIdentity`.
- All error fields are `tuple[SafeErrorCode, ...]`; no free-form exception field exists.

- [ ] Write failing schema tests for unknown fields, unsafe run/config/hash values, count/status inconsistency, and forbidden manifest field names.
- [ ] Run `uv run pytest tests/pipeline/test_daily_schemas.py -q` and confirm import failure.
- [ ] Implement frozen strict models with `schema_version="1.0"`, `pipeline_version="stage7-v1"`, deterministic UTC serialization, and cross-field invariants.
- [ ] Rerun the focused test and commit `test: define stage 7 run contracts`.

### Task 2: Add safe cache, paths, atomic manifest, and artifact audit

**Files:**
- Create: `src/zotero_arxiv_daily/pipeline/cache.py`
- Create: `src/zotero_arxiv_daily/pipeline/artifacts.py`
- Test: `tests/pipeline/test_daily_cache.py`
- Test: `tests/pipeline/test_daily_artifacts.py`

**Interfaces:**
- `WorkflowCache.key(identity) -> str` binds every implementation identity.
- `WorkflowCache.read(expected_identity, now) -> Mapping | None` treats corrupt, oversized, stale, or mismatched entries as misses.
- `ManifestStore.write(manifest) -> Path` performs same-directory fsync + atomic replace.
- `ArtifactAuditor.audit(viewer_root) -> ArtifactAudit` rejects symlinks/private/cache/archive/feedback paths and returns a canonical SHA-256.

- [ ] Write failing cache tests for identity coverage, corruption, over-size, expiry, mismatch, write failure cleanup, and atomic replace observation.
- [ ] Write failing path/artifact tests for `..`, symlink, UNC, foreign Windows drive, `.env`, cache, Zotero, feedback, PDF/archive/database, missing build files, and valid viewer content.
- [ ] Run both focused files and confirm RED for missing modules.
- [ ] Implement canonical JSON hashing, strict envelope validation, safe containment, byte/file limits, audit allowlist, and atomic writers.
- [ ] Run focused tests and commit `feat: add safe stage 7 cache and artifacts`.

### Task 3: Orchestrate Stage 1–6 with partial failure isolation

**Files:**
- Create: `src/zotero_arxiv_daily/delivery/ledger.py`
- Create: `src/zotero_arxiv_daily/pipeline/daily.py`
- Test: `tests/pipeline/test_daily.py`

**Interfaces:**
- `DailyDependencies` injects candidate/document/analysis/validation/viewer/delivery runners, manifest store, auditor, clock, sleep, and ledger.
- `run_daily(settings, dependencies) -> RunManifest` always writes one safe final manifest when the output boundary is available.
- `DeliveryLedger.claim(idempotency_key)` locks check/send/record across processes;
  the workflow restores and appends safe ledger snapshots with unique cache keys.

- [ ] Write RED tests for full success, empty candidates, one-paper partial, Stage 4 blocked publication, viewer success/Feishu failure, viewer failure/Feishu skipped, and same idempotency key duplicate.
- [ ] Run `uv run pytest tests/pipeline/test_daily.py -q` and confirm missing imports.
- [ ] Implement sequential typed composition, per-stage fixed-code exception conversion, independent viewer/delivery outcomes, aggregate counts, final status, and persistent atomic ledger.
- [ ] Run focused plus `tests/analysis/test_validator_rules.py tests/viewer tests/delivery -q` and commit `feat: orchestrate the versioned daily pipeline`.

### Task 4: Add default-offline CLI and production adapters

**Files:**
- Modify: `src/zotero_arxiv_daily/pipeline/daily.py`
- Create: `tests/pipeline/test_daily_cli.py`

**Interfaces:**
- `main(argv, *, environ=os.environ, dependency_factory=...) -> int` uses `allow_abbrev=False`.
- Exact modes: `--mode dry-run|live`; only `--send-feishu` may enable Stage 6 network delivery.
- Dry-run requires `--offline-fixture` and never constructs production clients.

- [ ] Write RED CLI tests for scheduled/manual/local, forbidden abbreviations, default dry-run, zero network/paid/send calls, live/send gating, missing environment names without values, and safe manifest output.
- [ ] Implement offline fixture composition over the existing Stage 4 golden fixture and production adapters over Stage 1–6 public functions; ensure all close callbacks run.
- [ ] Run CLI tests and `uv run python -m zotero_arxiv_daily.pipeline.daily --mode dry-run --trigger local --offline-fixture tests/fixtures/evidence/stage4_golden.json --run-root outputs/stage7-fixture`.
- [ ] Parse/audit the generated manifest/artifact and commit `feat: add safe daily pipeline cli`.

### Task 5: Replace unsafe workflows with pinned daily automation

**Files:**
- Create: `.github/workflows/personal-paper-daily.yml`
- Modify: `.github/workflows/ci.yml`
- Delete: `.github/workflows/main.yml`
- Delete: `.github/workflows/test.yml`
- Delete: `.github/workflows/keep-alive.yml`
- Create: `tests/workflows/test_personal_paper_daily.py`

**Interfaces:**
- schedule and workflow_dispatch share one daily CLI invocation.
- Build job has `contents: read`; deploy job alone has `pages: write` and `id-token: write`.
- General cache allowlist is exactly `cache/embeddings`, `cache/documents`, and
  `models/docling`; a separate cache path is exactly the non-sensitive
  `cache/workflow/delivery-ledger.json`.

- [ ] Write RED YAML/static tests for triggers, permissions, concurrency, timeouts, SHA pins, checkout credentials, identical CLI, exact gates, safe cache/artifact paths, forbidden commands, no push/visibility mutation, and Pages conditions.
- [ ] Add the workflow with verified action SHAs, frozen sync, safe mode selection, artifact audit/upload, and optional Pages deployment; update CI while preserving its pytest coverage.
- [ ] Remove legacy workflows that print generated config or write Git history.
- [ ] Run `uv run pytest tests/workflows -q` and commit `ci: add safe daily paper automation`.

### Task 6: Document configuration and real completion evidence

**Files:**
- Modify: `.env.example`
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/BASELINE.md`

**Interfaces:**
- Secrets: `ZOTERO_ID`, `ZOTERO_KEY`, `LLM_API_KEY`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_CHAT_ID`.
- Variables: `LLM_BASE_URL`, `LLM_MODEL`, `PAPER_DAILY_SITE_URL`, exact live/send/deploy acknowledgements.

- [ ] Write/extend static tests proving `.env.example` has names/empty placeholders only and documentation distinguishes Secrets/Variables.
- [ ] Update configuration, Stage 7 status, exact observed commands/results, known baseline failures, external operations not run, limits, and rollback.
- [ ] Commit `docs: document stage 7 automation and recovery`.

### Task 7: Full verification, independent review, and handoff

**Files:**
- Modify only files required by reproduced review findings.
- Finalize: `docs/BASELINE.md`, `docs/IMPLEMENTATION_PLAN.md`.

- [ ] Run `uv sync --frozen`, Stage 7 focused tests, Stage 6/5/4 regressions, default pytest, `pytest -m "slow or not slow" -q`, compileall, workflow validation, diff check, tracked secret/private/cache/archive/large-file scans, dry-run fixture CLI, and artifact content audit.
- [ ] Request an independent whole-branch review against `16beba2`; reproduce every confirmed Critical/Important finding with a failing test before fixing.
- [ ] Rerun affected and full non-slow verification, record factual results, and commit review fixes plus `docs: record stage 7 final review`.
- [ ] Confirm `git status --short` is empty; do not push, merge, create PR, alter upstream, or delete any worktree.
