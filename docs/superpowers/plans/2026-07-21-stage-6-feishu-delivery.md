# Stage 6 Feishu Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 Stage 4 已验证结果生成安全的飞书卡片预览，并提供显式、可测试、幂等的飞书发送入口。

**Architecture:** 纯渲染与策略模块只读取 `ValidationBatchResult`，网络客户端通过注入 transport 隔离。CLI 默认从人工 fixture 写预览；仅同时具备 `--send` 和完整飞书环境变量时组合真实客户端。

**Tech Stack:** Python 3.13、Pydantic 2、httpx、pytest、现有 Stage 4 fixture。

## Global Constraints

- 只实现 Stage 6；不实现自动化、反馈、浏览器存储或真实模型调用。
- 详细卡只接受 `valid` + `eligible` 的 Stage 4 结果，最多五篇。
- 密钥仅来自环境，不进入模型、卡片、输出、异常、日志或 Git。
- 默认 preview 与全部单元测试零网络；真实发送需要显式 `--send`。
- HTTP 调用必须有 timeout、有限重试和受控错误分类。

---

### Task 1: Define strict digest and delivery contracts

**Files:**
- Create: `src/zotero_arxiv_daily/delivery/schemas.py`
- Create: `src/zotero_arxiv_daily/delivery/__init__.py`
- Test: `tests/delivery/test_schemas.py`

**Interfaces:**
- Produces `DigestPaper`, `FeishuPayload`, `DeliveryRequest`, `DeliveryReceipt`, and `FeishuSettings` strict Pydantic models.
- `FeishuSettings.from_environment(environment: Mapping[str, str]) -> FeishuSettings` accepts names only and raises controlled missing-variable errors.

- [ ] **Step 1: Write failing contract tests** for unknown fields, invalid non-HTTPS site URLs, more-than-five papers, malformed chat IDs, and missing environment names.
- [ ] **Step 2: Run RED** with `uv run pytest tests/delivery/test_schemas.py -q` and confirm import failure.
- [ ] **Step 3: Implement frozen strict schemas** with lower-bounded timeout/retry values and SHA-256 idempotency key validation.
- [ ] **Step 4: Run GREEN** with the same command.
- [ ] **Step 5: Commit** `test: define stage 6 delivery contracts`.

### Task 2: Render validated Chinese Feishu cards

**Files:**
- Create: `src/zotero_arxiv_daily/delivery/feishu.py`
- Test: `tests/delivery/test_feishu_renderer.py`

**Interfaces:**
- `DigestPolicy.build(batch: ValidationBatchResult, *, site_url: str) -> DeliveryRequest`
- `FeishuRenderer.render(request: DeliveryRequest) -> FeishuPayload`

- [ ] **Step 1: Write failing renderer tests** using `tests.analysis.stage4_factories.golden_inputs()` plus `validate_paper`: valid eligible content appears in required Chinese order, invalid/partial content is excluded, six valid inputs cap at five, missing values show “论文未明确提供”, and credential-bearing/non-HTTPS links are omitted.
- [ ] **Step 2: Run RED** with `uv run pytest tests/delivery/test_feishu_renderer.py -q`.
- [ ] **Step 3: Implement the pure policy and interactive-card renderer** using escaped text and a deterministic JSON shape; include site link only when HTTPS and no authority credentials.
- [ ] **Step 4: Run GREEN** and Stage 4 regression: `uv run pytest tests/delivery/test_feishu_renderer.py tests/analysis/test_validator_rules.py -q`.
- [ ] **Step 5: Commit** `feat: render validated feishu digest`.

### Task 3: Add injected client and no-send CLI preview

**Files:**
- Modify: `src/zotero_arxiv_daily/delivery/feishu.py`
- Create: `src/zotero_arxiv_daily/pipeline/feishu.py`
- Test: `tests/delivery/test_feishu_client.py`
- Test: `tests/pipeline/test_feishu.py`

**Interfaces:**
- `FeishuClient(settings, transport).send(request, payload) -> DeliveryReceipt`
- `main(argv)` writes preview unless `--send` was supplied; preview never constructs a network transport.

- [ ] **Step 1: Write failing client tests** for token/send success, no duplicate send for one idempotency key, 401 no-retry, 429/5xx bounded retry, timeout redaction, and no secret in exception text.
- [ ] **Step 2: Write failing CLI tests** proving fixture preview creates a JSON file without network and `--send` rejects missing Feishu settings before any request.
- [ ] **Step 3: Run RED** with `uv run pytest tests/delivery/test_feishu_client.py tests/pipeline/test_feishu.py -q`.
- [ ] **Step 4: Implement minimal httpx transport, controlled retry policy, in-process idempotency ledger, fixture preview, and send gate.**
- [ ] **Step 5: Run GREEN** plus `uv run python -m zotero_arxiv_daily.pipeline.feishu --offline-fixture tests/fixtures/evidence/stage4_golden.json --output outputs/feishu-preview.json`.
- [ ] **Step 6: Commit** `feat: add safe feishu preview and client`.

### Task 4: Stage verification and handoff

**Files:**
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/BASELINE.md`

- [ ] **Step 1: Run focused Stage 6/4 tests, `uv sync --frozen`, compileall, default pytest, configured slow pytest, diff check, and hygiene scans.**
- [ ] **Step 2: Run local preview; do not use `--send`.**
- [ ] **Step 3: Request an independent review and reproduce/fix all confirmed Critical or Important issues with tests.**
- [ ] **Step 4: Record only observed results, credential state, no-send status, and rollback instructions.**
- [ ] **Step 5: Commit** `docs: record stage 6 verification`.
