# Stage 8 Reader Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不公开用户状态、不引入云数据库的前提下，为静态阅读页实现私有 read/favorite/irrelevant 反馈、版本化备份/导入、原子本地 store，并把受控投影安全接回 Stage 1 排名。

**Architecture:** 浏览器以 same-origin `feedback.js` 将幂等 set-command 保存在 namespaced `localStorage`，用户显式导出 bundle；Python CLI 严格验证、确定性合并并原子写入 Git 忽略的权威 store；`InterestFeedbackProjection` 在候选付费边界前 veto irrelevant，并对相同稳定 ID 的 favorite 施加有上限的小幅增益。Stage 4 资格门及 Stage 2–7 行为保持不变。

**Tech Stack:** Python 3.13、Pydantic 2、pytest、Node.js 20 离线 JS harness、原生浏览器 DOM/localStorage、现有 Stage 1–7 模块。

**Status:** Completed locally on 2026-07-26. All confirmed findings received focused
RED coverage before repair; final independent re-review reported Spec Compliance
`PASS`, Code Quality `PASS`, and no remaining Critical/Important/Minor findings.

## Global Constraints

- 每个行为变化先写失败测试并观察预期 RED，再写最小实现。
- 反馈内容、paper ID 列表、浏览器状态、bundle 和私有 store 不进入 Git、artifact、cache、manifest 或日志。
- Stage 4 仍是唯一 publication eligibility 门；反馈只能提前排除，不能提升无效论文。
- CLI 禁止参数缩写；所有 clock、文件系统和 store 边界可替换，测试零网络、零付费、零发送。
- 不自动读取 Downloads、用户目录、真实 Zotero/LLM/Feishu，不 push/merge/PR/upstream mutation。
- 每个逻辑单元完成 focused verification 后使用清晰小提交。

---

### Task 1: Define strict feedback contracts and deterministic state machine

**Files:**
- Create: `src/zotero_arxiv_daily/viewer/feedback.py`
- Create: `tests/viewer/test_feedback_schemas.py`
- Create: `tests/viewer/test_feedback_state.py`

**Interfaces:**
- `normalize_feedback_paper_id(value) -> str` accepts canonical modern/legacy arXiv IDs and strips an optional version.
- `FeedbackCommand`, `FeedbackRecord`, `FeedbackBundle`, `FeedbackStoreState`, and `InterestFeedbackProjection` are frozen `extra=forbid` models.
- `apply_feedback_commands(state, commands) -> FeedbackMergeResult` uses read/preference watermarks and produces deterministic, idempotent results independent of bundle import order.

- [x] Write RED schema tests for modern/legacy/versioned IDs, URL/path/control/UNC/drive rejection, unknown fields, naive timestamps, invalid UUID/device/sequence, unsafe digest, oversized collections, and canonical ordering.
- [x] Write RED state tests for read coexistence, favorite/irrelevant mutual exclusion, clear semantics, duplicate command no-op, stale command handling, bundle-order independence, conflict counts, and immutable inputs.
- [x] Run `uv run pytest tests/viewer/test_feedback_schemas.py tests/viewer/test_feedback_state.py -q` and confirm missing-module RED.
- [x] Implement the smallest strict models, canonical JSON/digest helpers, two-domain watermarks, deterministic merge, bounded dedupe metadata, and safe fixed result counters.
- [x] Rerun focused tests plus `tests/viewer/test_schemas.py -q`; commit `feat: define private feedback state contracts`.

### Task 2: Add safe private store and explicit import CLI

**Files:**
- Modify: `src/zotero_arxiv_daily/viewer/feedback.py`
- Create: `src/zotero_arxiv_daily/viewer/feedback_cli.py`
- Create: `tests/viewer/test_feedback_store.py`
- Create: `tests/viewer/test_feedback_cli.py`
- Modify: `.gitignore`

**Interfaces:**
- `FeedbackStore.read() -> FeedbackStoreState` returns empty only for a missing file; corruption, over-size, unknown version, digest mismatch, symlink, or unsafe boundary raises a fixed safe-domain error.
- `FeedbackStore.import_bundle(bundle, *, dry_run) -> FeedbackImportResult` validates fully before one atomic replacement.
- `python -m zotero_arxiv_daily.viewer.feedback_cli import --bundle PATH --store PATH [--dry-run]` uses `allow_abbrev=False` and prints only fixed status/count fields.

- [x] Write RED store tests for missing/corrupt/over-size/old migration/new mismatch/identity mismatch, duplicate bundle, stale/partial input, same-directory temp + flush/fsync + atomic replace, injected replace failure cleanup, symlink parents, traversal, relative escape, UNC and foreign Windows drive.
- [x] Write RED CLI tests for exact subcommand/options, forbidden abbreviations, explicit paths, dry-run zero writes, missing file fixed error, no dynamic exception/value leakage, repeated bundle idempotency, and output field allowlist.
- [x] Run the two focused files and confirm RED before implementation. The initial behavior-level RED evidence was incomplete; the user explicitly accepted that historical process deviation after current coverage and implementation were independently reviewed.
- [x] Implement bounded reads, checked local boundaries, canonical digest validation, listed v0→v1 migration fixture, injected file operations, atomic writer and safe CLI exit codes.
- [x] Add exact private feedback paths/export patterns/migration backups to `.gitignore`; prove them ignored with tests.
- [x] Rerun focused tests and commit `feat: add atomic private feedback import`.

### Task 3: Add accessible static viewer feedback UI and browser adapter

**Files:**
- Modify: `src/zotero_arxiv_daily/viewer/renderer.py`
- Modify: `src/zotero_arxiv_daily/viewer/builder.py`
- Modify: `src/zotero_arxiv_daily/viewer/schemas.py`
- Modify: `src/zotero_arxiv_daily/viewer/static/site.css`
- Create: `src/zotero_arxiv_daily/viewer/static/feedback.js`
- Modify: `tests/viewer/test_renderer.py`
- Modify: `tests/viewer/test_builder.py`
- Modify: `tests/viewer/test_styles.py`
- Create: `tests/viewer/test_feedback_browser.py`

**Interfaces:**
- Eligible cards expose canonical `data-paper-id`, three `aria-pressed` controls, filters and explicit backup import/export; paper pages can mark read without embedding private state.
- CSP permits only same-origin external scripts and continues to reject inline/eval/remote code.
- `feedback.js` exposes a small testable pure state API and a DOM adapter; tests execute it in a Node 20 fake DOM/localStorage harness with no network or npm dependency.

- [x] Write RED renderer/builder tests for canonical IDs, eligible-only controls, external script path, CSP, manifest inclusion, no state embedding, empty viewer and paper-page behavior.
- [x] Write RED Node harness tests for refresh persistence, read/favorite/irrelevant transitions, default hiding, unread/favorite/show-irrelevant filters, scoped `r/f/i` keys, input-field exclusion, corrupted/quota-disabled localStorage, deterministic export, valid backup import and transactional rejection.
- [x] Run focused viewer tests and observe expected RED.
- [x] Implement same-origin static script, safe DOM `textContent`/attributes, namespaced localStorage, Web Crypto digest, blob download/file import, accessible controls and reduced-motion/focus styles.
- [x] Rerun all `tests/viewer -q`; generate a fixture viewer and audit HTML/JS/CSP for inline code, remote URLs and embedded feedback state.
- [x] Commit `feat: add private reader feedback controls`.

### Task 4: Project feedback into Stage 1 ranking before paid work

**Files:**
- Create: `src/zotero_arxiv_daily/candidates/feedback.py`
- Modify: `src/zotero_arxiv_daily/candidates/ranking.py`
- Modify: `src/zotero_arxiv_daily/pipeline/candidates.py`
- Modify: `src/zotero_arxiv_daily/pipeline/daily.py`
- Modify: `config/base.yaml`
- Create: `tests/candidates/test_feedback.py`
- Modify: `tests/candidates/test_ranking.py`
- Modify: `tests/pipeline/test_candidates.py`
- Modify: `tests/pipeline/test_daily.py`
- Modify: `tests/pipeline/test_daily_cli.py`

**Interfaces:**
- `FeedbackProjectionLoader` converts only validated store records into ID sets and a favorite delta in `[0.0, 0.10]`; missing optional store produces an empty projection, invalid configured store fails closed.
- `CandidateRanker.rank(..., feedback=projection)` removes irrelevant IDs before embedding/provider calls and applies favorite delta before deterministic sort/clamp.
- Production daily composition accepts an explicit feedback-store config/path; offline/dry-run defaults to an injected empty projection and never scans local files.

- [x] Write RED adapter/ranking tests for empty projection, read zero effect, favorite `+0.05`, configurable bounds, score clamp, stable tie break, version-normalized match, irrelevant zero embedding/paid calls, and invalid projection rejection.
- [x] Write RED pipeline tests proving irrelevant papers never reach document/analysis/validation/viewer/Feishu, Stage 4 remains required, missing store is neutral, corrupt configured store is a safe fixed failure, and manifest/logs contain no feedback content.
- [x] Run focused candidates/pipeline tests and confirm RED.
- [x] Implement projection loader and narrow ranker/pipeline injection without changing default Stage 1 behavior when projection is empty; include feedback implementation version/config in candidate config hash.
- [x] Rerun focused tests plus Stage 4–7 related regressions; commit `feat: apply bounded feedback to candidate ranking`.

### Task 5: Harden artifact/workflow privacy and document configuration

**Files:**
- Modify: `src/zotero_arxiv_daily/pipeline/artifacts.py`
- Modify: `.github/workflows/personal-paper-daily.yml` only if the existing explicit excludes need tightening.
- Modify: `.env.example` only if an explicit feedback store variable is selected.
- Modify: `tests/pipeline/test_daily_artifacts.py`
- Modify: `tests/workflows/test_personal_paper_daily.py`
- Create: `tests/viewer/test_feedback_privacy.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/BASELINE.md`

**Interfaces:**
- Artifact audit rejects feedback store/bundle/browser-state/migration backup by basename and path segment, including nested/case variants.
- Workflow cache/upload allowlists remain explicit and cannot capture private feedback; scheduled run remains no-send and no private-state sync by default.
- Documentation records browser→export→local import, GitHub Pages limitation, exact config location, authorization basis for Hermes reuse, rollback and factual verification only.

- [x] Write RED privacy tests that seed `.env`, feedback JSON, localStorage snapshot, cache/private/Zotero/archive files beside an otherwise valid viewer and require audit rejection or exclusion.
- [x] Extend workflow static tests for feedback path absence and unchanged permissions/triggers/concurrency/timeouts/SHA pins/same CLI.
- [x] Implement minimal audit/workflow changes and update docs/config without real values or state examples containing paper IDs.
- [x] Run privacy/workflow/doc static tests; commit `docs: document private feedback workflow`.

### Task 6: Full local verification and independent whole-branch review

**Files:**
- Modify only files required by reproduced review findings.
- Finalize: `docs/BASELINE.md`, `docs/IMPLEMENTATION_PLAN.md`, this plan status/checklists.

- [x] Run `uv sync --frozen` using the configured workspace runtime (`Checked 172 packages`).
- [x] Run Stage 8 focused suites: feedback schema/state/store/CLI/browser/privacy, viewer, candidates feedback/ranking and daily integration (final expanded group: `251 passed in 15.87s`).
- [x] Run Stage 7 focused tests and Stage 6/5/4 related regression tests (final group: `417 passed in 18.37s`).
- [x] Run default `pytest -q` and start full `pytest -m "slow or not slow" -q`; final default observed `705 passed, 2 known Windows spawn failures, 1 deselected`; unfiltered full command reached the 600-second cap (exit 124) before a summary, recorded as the known uncached Hugging Face/local-reranker environment block without skipping or weakening it.
- [x] Run `python -m compileall -q src`, workflow YAML/static validation, `git diff --check`, tracked secret/private/cache/archive/large-file scans, fixture dry-run CLI, feedback import dry-run and generated artifact content audit.
- [x] Request an independent whole-branch review against `c822897`; reproduce every confirmed Critical/Important finding with a failing test before fixing. The first review reported 0 Critical, 2 Important and 2 Minor; all four findings received focused RED coverage before repair. Re-review found one additional Important floating-point boundary, also repaired after a focused RED; final re-review passed with no findings.
- [x] Rerun affected focused and full non-slow verification, record exact observed results and external operations not performed, then commit review fixes and final evidence.
- [x] Confirm final worktree clean after the final evidence commit; do not push, merge, create PR, alter upstream or delete Stage 6/7/8 worktrees.
