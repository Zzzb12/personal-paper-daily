# Stage 5 Static Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 Stage 4 验证封装离线、确定性地生成安全、可访问的每日论文静态阅读站点。

**Architecture:** `viewer` 包以冻结 Pydantic page/build model 隔离输入、发布策略、资源复制和模板渲染。构建器只接受 `ValidationBatchResult` 中的 `ValidatedPaperAnalysis` 与 `ValidationReport`；在受限输出根原子写入 HTML/CSS/manifest，并将真实证据图安全复制为内容寻址资源。

**Tech Stack:** Python 3.13、Pydantic 2.12、pytest、标准库 HTML/JSON/pathlib/hashlib、内置 HTML 模板、CSS、Playwright CLI。

## Global Constraints

- 基线为 `2fb1d09b18d6f66ed808c1e41567bae4c14e1407`；只在 `feat/stage-5-static-viewer` worktree 修改。
- 仅消费 Stage 4 `ValidatedPaperAnalysis`、`ValidationReport`、`ValidationBatchResult`；不得读取裸 `PaperAnalysis` 发布。
- `valid + eligible` 完整发布；`partial` 仅在 `allow_partial=true` 时带 error/warning issue 发布；`invalid/failed/skipped` 不进入正常阅读输出。
- 缺失内容显示“论文未明确提供”；不得生成论文结论、链接、参数、图表或数值。
- 输出默认为忽略的 `outputs/viewer/`；模板默认转义，外链仅 https，证据资源必须位于真实 evidence root 内。
- 不引入 Node/React/CDN/在线字体/网络访问，不实现 Stage 6/7/8 或任何 localStorage 状态。
- Hermes 当前缺许可证文件；仅借鉴理念，不复制或实质改编其代码或数据。
- 每项遵循 RED → 最小实现 → GREEN → 相关回归 → commit；无 skip、无弱化断言。

---

### Task 1: Define strict viewer contracts and publication policy

**Files:**
- Create: `src/zotero_arxiv_daily/viewer/__init__.py`
- Create: `src/zotero_arxiv_daily/viewer/schemas.py`
- Create: `src/zotero_arxiv_daily/viewer/publication.py`
- Test: `tests/viewer/test_schemas.py`
- Test: `tests/viewer/test_publication.py`

**Interfaces:** `PublicationPolicy.decide(result: ValidationPaperResult) -> PublicationDecision`; `ViewerSettings(output_root: Path, allow_partial: bool, ...)`; `PaperPageModel`, `IndexPageModel`, `ViewerBuildIssue`, `BuildManifest` use `StrictModel` and `extra="forbid"`.

- [ ] **Step 1: Write RED tests**
```python
def test_only_eligible_valid_analysis_is_full_publication():
    assert PublicationPolicy(allow_partial=False).decide(valid_result()).kind == "full"
    assert PublicationPolicy(allow_partial=True).decide(partial_result()).kind == "partial"
    assert PublicationPolicy(allow_partial=True).decide(invalid_result()).kind == "excluded"
```
- [ ] **Step 2: Run RED** — `uv run pytest tests/viewer/test_publication.py -q`; expect import failure.
- [ ] **Step 3: Implement minimum policy** — inspect `ValidationPaperResult.status`, report status and `publication_eligibility`; preserve all public-safe issues and reject an absent validated wrapper.
- [ ] **Step 4: Run GREEN** — `uv run pytest tests/viewer/test_schemas.py tests/viewer/test_publication.py -q`; expect pass.
- [ ] **Step 5: Commit** — `git add src/zotero_arxiv_daily/viewer tests/viewer && git commit -m "feat: define static viewer publication contracts"`.

### Task 2: Implement safe input, path, asset, and atomic-output boundaries

**Files:**
- Create: `src/zotero_arxiv_daily/viewer/assets.py`
- Create: `src/zotero_arxiv_daily/viewer/filesystem.py`
- Test: `tests/viewer/test_assets.py`
- Test: `tests/viewer/test_filesystem.py`

**Interfaces:** `EvidenceImagePublisher.publish(source: Path, *, evidence_root: Path) -> PublishedAsset`; `AtomicOutputRoot.write_text(relative: PurePosixPath, content: str) -> Path`; both accept injected roots only.

- [ ] **Step 1: Write RED security tests**
```python
@pytest.mark.parametrize("source", ["../outside.png", "escape-link.png"])
def test_rejects_path_or_symlink_escape(tmp_path, source):
    with pytest.raises(ValueError, match="evidence root"):
        publisher(tmp_path).publish(tmp_path / source, evidence_root=tmp_path / "evidence")

def test_identical_assets_use_content_addressed_name_without_overwrite(tmp_path):
    assert publisher(tmp_path).publish(png_a, evidence_root=root).relative_path == publisher(tmp_path).publish(png_b, evidence_root=root).relative_path
```
- [ ] **Step 2: Run RED** — `uv run pytest tests/viewer/test_assets.py tests/viewer/test_filesystem.py -q`; expect missing modules.
- [ ] **Step 3: Implement minimum boundaries** — resolve roots with `Path.resolve(strict=True)`, require `relative_to`, reject symlinks/outside files/size/type failures, hash bytes with SHA-256, write temp sibling then fsync + `os.replace`; `dry_run` performs no mkdir/write.
- [ ] **Step 4: Run GREEN** — focused suites pass, including missing/corrupt image and output traversal cases.
- [ ] **Step 5: Commit** — `git commit -m "feat: add safe static viewer asset publishing"`.

### Task 3: Build escaped semantic page renderer and visual system

**Files:**
- Create: `src/zotero_arxiv_daily/viewer/renderer.py`
- Create: `src/zotero_arxiv_daily/viewer/templates.py`
- Create: `src/zotero_arxiv_daily/viewer/static/site.css`
- Test: `tests/viewer/test_renderer.py`

**Interfaces:** `TemplateRenderer.render_index(IndexPageModel) -> str`; `render_paper(PaperPageModel) -> str`; content is escaped through `html.escape(..., quote=True)` before interpolation.

- [ ] **Step 1: Write RED semantic/content tests**
```python
def test_paper_page_escapes_analysis_text_and_marks_inference():
    html = render(paper_with_text("<script>x</script>", inferred=True))
    assert "&lt;script&gt;" in html and "系统推断，非作者直接陈述" in html
    assert "<main" in html and "<article" in html and "论文未明确提供" in html

def test_figure_precedes_method_and_has_figcaption_provenance():
    html = render(valid_paper())
    assert html.index("证据图表") < html.index("Method")
    assert "<figure" in html and "<figcaption" in html and "PDF page" in html
```
- [ ] **Step 2: Run RED** — `uv run pytest tests/viewer/test_renderer.py -q`; expect import failure.
- [ ] **Step 3: Implement minimum templates/CSS** — generate semantic index/detail HTML, fixed narrative order, text+icon status labels, safe `https` links with meaningful text and `rel`, partial issue list, image alt/fallback, viewport/CSP, focus-visible and reduced-motion CSS.
- [ ] **Step 4: Run GREEN** — renderer tests pass; add heading/table/alt/no-JS assertions.
- [ ] **Step 5: Commit** — `git commit -m "feat: render accessible static paper pages"`.

### Task 4: Compose deterministic builder, offline CLI, and integration fixtures

**Files:**
- Create: `src/zotero_arxiv_daily/viewer/builder.py`
- Create: `src/zotero_arxiv_daily/pipeline/viewer.py`
- Modify: `config/base.yaml`
- Modify: `.gitignore`
- Create: `tests/viewer/factories.py`
- Test: `tests/viewer/test_builder.py`
- Test: `tests/viewer/test_viewer_offline.py`

**Interfaces:** `StaticViewerBuilder(settings, dependencies).build(batch, *, dry_run=False) -> BuildManifest`; CLI accepts `--offline-fixture`, `--output-root`, `--dry-run`, and prints only counts/version/statuses.

- [ ] **Step 1: Write RED integration tests**
```python
def test_builder_is_deterministic_and_partial_is_explicit(tmp_path):
    first = builder(tmp_path).build(valid_and_partial_batch())
    second = builder(tmp_path).build(valid_and_partial_batch())
    assert first == second
    assert "部分内容未通过或无法完成验证" in (tmp_path / "papers" / "partial.html").read_text()

def test_dry_run_and_invalid_input_write_no_output(tmp_path):
    assert builder(tmp_path).build(empty_batch(), dry_run=True).written_paths == ()
    assert not tmp_path.exists()
```
- [ ] **Step 2: Run RED** — `uv run pytest tests/viewer/test_builder.py tests/viewer/test_viewer_offline.py -q`; expect module failure.
- [ ] **Step 3: Implement minimum composition** — validate unique paper IDs/run identity, apply policy without mutation, create index/detail models in stable order, publish resources, write CSS/pages/manifest atomically, isolate one-paper `ViewerBuildIssue`, and prevent socket creation in offline fixture test.
- [ ] **Step 4: Run GREEN + Stage 4 regression** — `uv run pytest tests/viewer tests/analysis/test_stage4_offline.py tests/pipeline/test_validation.py -q`; expect pass.
- [ ] **Step 5: Commit** — `git commit -m "feat: build validated static viewer offline"`.

### Task 5: Verify responsive browser behavior, review, and document Stage 5

**Files:**
- Create: `tests/viewer/test_playwright_viewer.py`
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/BASELINE.md`

- [ ] **Step 1: Write Playwright RED checks** — fixture build + local static server; assert 1440×900 and 390×844 index/detail/partial/empty pages, no horizontal overflow, visible keyboard focus, no console errors, valid figure/table, JS-disabled content, and save ignored screenshots for visual inspection.
- [ ] **Step 2: Run RED then implement only missing CSS/template behavior** — `uv run pytest tests/viewer/test_playwright_viewer.py -q`.
- [ ] **Step 3: Run GREEN and inspect screenshots** — use Playwright at both viewports and `view_image`; record actual observations, never infer visual success from DOM alone.
- [ ] **Step 4: Completion verification** — run `uv sync --frozen`, Stage 5 suites, Stage 4/3/2/1 suites, `uv run pytest -q`, `uv run pytest -m "slow or not slow" -q`, `uv run python -m compileall -q src`, offline viewer CLI, `git diff --check 2fb1d09..HEAD`, and tracking/security scans. Report existing Windows spawn/Hugging Face failures exactly.
- [ ] **Step 5: Independent review loop** — invoke `requesting-code-review` for `2fb1d09..HEAD`; for confirmed Critical/Important findings invoke `receiving-code-review`, add a focused RED test, fix minimally, GREEN, commit, and request a second review until Ready.
- [ ] **Step 6: Update documents and commit** — mark Stage 5 complete, Stage 6 unstarted; record commits, no-code Hermes result, commands/counts, Playwright observations, scans, rollback. Commit `docs: record stage 5 verification`.
