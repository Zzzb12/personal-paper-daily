# Stage 2 PDF 文档提取实施计划

> **执行要求：** 按 `superpowers:test-driven-development` 逐任务执行；每个行为先观察
> RED，再写最小实现达到 GREEN。发生异常时使用 `superpowers:systematic-debugging`。

**目标：** 仅对 Stage 1 的 `selected_for_full_analysis` 论文安全下载 PDF，产出带
PDF 页码、章节、文本块、坐标、来源映射和 Figure/Table 证据图的版本化
`DocumentGraph`。

**架构：** Docling Standard PDF Pipeline 是主解析器，PyMuPDF 负责预检、页面校验、
渲染和裁剪。业务层只依赖项目自己的 Pydantic Schema 和窄解析器协议。下载、解析、
映射、裁剪和缓存按单篇论文隔离。

**技术栈：** Python 3.13、Pydantic 2、httpx、Docling、PyMuPDF、pytest、ReportLab。

**明确非目标：** Stage 3 中文或 LLM 分析、OCR 质量保证、VLM、GROBID 服务、网页、
飞书、邮件、反馈和 GitHub Actions。

---

## Task 1：锁定依赖与定义文档 Schema

**文件：**

- 修改：`pyproject.toml`
- 修改：`uv.lock`
- 新建：`src/zotero_arxiv_daily/analysis/document_schemas.py`
- 修改：`src/zotero_arxiv_daily/analysis/__init__.py`
- 新建：`tests/analysis/test_document_schemas.py`

### Step 1：写失败测试

测试覆盖：

- `BoundingBox` 拒绝非有限值、反向坐标和零面积；
- page 必须从 1 开始，confidence 在 `[0, 1]`；
- `DocumentGraph` 拒绝悬空 block/section/caption 引用和越界 bbox；
- 一个 `VisualArtifact` 支持多个跨页 `VisualRegion`；
- caption/label 缺失时必须显式为 `None`，并允许结构化 issue；
- JSON 往返保持稳定。

运行：

```powershell
uv run pytest tests/analysis/test_document_schemas.py -q
```

预期：导入错误或 Schema 尚不存在，确认 RED。

### Step 2：添加依赖

用 `uv add docling` 和 `uv add --dev reportlab` 让 uv 解析兼容版本并更新锁文件；不得手写
未经解析的锁版本。记录 Docling 实际解析版本和是否支持当前 Python。

### Step 3：最小实现

定义冻结且 `extra="forbid"` 的：

- `DocumentIssue`
- `BoundingBox`
- `SourceMapping`
- `PdfArtifact`
- `DocumentBlock`
- `DocumentPage`
- `SectionNode`
- `VisualRegion`
- `VisualArtifact`
- `DocumentGraph`
- `PaperDocumentResult`
- `DocumentBatchResult`

常量至少包括 `DOCUMENT_SCHEMA_VERSION = "1.0"`。用 model validator 做跨对象引用和页面
坐标验证，不把验证推迟到 LLM 阶段。

### Step 4：验证并提交

```powershell
uv run pytest tests/analysis/test_document_schemas.py -q
git add pyproject.toml uv.lock src/zotero_arxiv_daily/analysis tests/analysis/test_document_schemas.py
git commit -m "feat: define stage 2 document schemas"
```

## Task 2：实现 Stage 1 选择门

**文件：**

- 新建：`src/zotero_arxiv_daily/documents/__init__.py`
- 新建：`src/zotero_arxiv_daily/documents/selection.py`
- 新建：`tests/documents/__init__.py`
- 新建：`tests/documents/test_selection.py`

### Step 1：写失败测试

构造合法 `CandidateBatch`，断言：

- 返回顺序与 `selected_for_full_analysis` 完全一致；
- 未选中的论文不会返回；
- 即使接到不可信外部对象也不能超过 5 篇；
- 缺失 ID 产生明确错误，不静默选择别的论文。

### Step 2：最小实现

提供：

```python
def selected_papers(batch: CandidateBatch, *, hard_limit: int = 5) -> tuple[CandidatePaper, ...]: ...
```

### Step 3：验证并提交

```powershell
uv run pytest tests/documents/test_selection.py -q
git add src/zotero_arxiv_daily/documents tests/documents
git commit -m "feat: gate stage 2 paper selection"
```

## Task 3：实现安全 PDF 下载器

**文件：**

- 新建：`src/zotero_arxiv_daily/documents/downloader.py`
- 新建：`tests/documents/test_downloader.py`

### Step 1：写失败测试

使用 `httpx.MockTransport`，禁止真实网络，覆盖：

- timeout 参数被显式传入；
- 429/5xx 和传输异常有限重试；
- 4xx、错误 Content-Type 不重试；
- `application/pdf` 和允许的 octet-stream 加 PDF magic bytes；
- 超过 Content-Length 或流式读取大小上限立即失败；
- 非 `%PDF-` 内容失败；
- 成功文件按 SHA-256 原子落盘；
- 失败不留下 `.tmp`；
- 缓存命中不发网络请求；
- issue/log 不包含 URL 查询参数。

### Step 2：最小实现

定义 `PdfDownloadPolicy`、`PdfDownloadError` 和 `SafePdfDownloader`。使用
`httpx.Client.stream`，按 chunk 累计大小；临时文件与目标位于同一目录，完成校验后
`os.replace`。重试策略只处理明确可恢复错误。

### Step 3：验证并提交

```powershell
uv run pytest tests/documents/test_downloader.py -q
git add src/zotero_arxiv_daily/documents/downloader.py tests/documents/test_downloader.py
git commit -m "feat: download selected pdfs safely"
```

## Task 4：实现 PDF 预检与测试 fixture 工厂

**文件：**

- 新建：`src/zotero_arxiv_daily/documents/inspector.py`
- 新建：`tests/fixtures/pdf_factory.py`
- 新建：`tests/documents/test_inspector.py`

### Step 1：写失败测试

使用 ReportLab/PyMuPDF 在 `tmp_path` 动态生成正常文本、双栏、空白扫描件模拟、加密和
损坏 PDF，覆盖：

- 正常 PDF 的页数、页面尺寸和文本层；
- 无文本层返回 `no_text_layer`/`scanned_document`；
- 损坏 PDF 返回 `corrupt_pdf`；
- 加密 PDF 返回 `encrypted_pdf`；
- 页数上限；
- 文件始终位于临时目录且不进入 Git。

### Step 2：最小实现

使用 PyMuPDF 只读打开 PDF，返回 `PdfInspection` 和结构化 issues。预检不做 OCR。

### Step 3：视觉核验 fixture

调用可用的 `pdftoppm` 将代表性动态 fixture 渲染到 `tmp/pdfs/`，人工检查版面、双栏、
Figure/Table 和跨页 caption；渲染产物不得提交。

### Step 4：验证并提交

```powershell
uv run pytest tests/documents/test_inspector.py -q
git add src/zotero_arxiv_daily/documents/inspector.py tests/fixtures/pdf_factory.py tests/documents/test_inspector.py
git commit -m "feat: inspect pdf structure and failure states"
```

## Task 5：实现 Docling 窄适配器

**文件：**

- 新建：`src/zotero_arxiv_daily/documents/parser.py`
- 新建：`tests/documents/test_parser.py`

### Step 1：写失败测试

用伪 converter/result/doc 对象覆盖：

- 默认只允许本地 PDF；
- pipeline 配置不启用 VLM；
- conversion success 返回版本化中间记录；
- partial/failure 状态转换为项目 issue；
- 模型不可用映射为 `parser_model_unavailable`；
- 禁止测试中隐式联网；
- Docling provenance 的 page 与 bbox 被保留。

### Step 2：最小实现

定义 `DocumentParser` Protocol、内部 `ParsedDocument`/`ParsedItem`/`ParsedProvenance` 和
`DoclingDocumentParser`。Docling import 放在适配器边界，构造 converter 时明确使用
Standard PDF Pipeline；异常只在这里翻译。

### Step 3：验证并提交

```powershell
uv run pytest tests/documents/test_parser.py -q
git add src/zotero_arxiv_daily/documents/parser.py tests/documents/test_parser.py
git commit -m "feat: adapt docling document conversion"
```

## Task 6：实现章节、正文和 Figure/Table 映射

**文件：**

- 新建：`src/zotero_arxiv_daily/documents/sections.py`
- 新建：`src/zotero_arxiv_daily/documents/captions.py`
- 新建：`src/zotero_arxiv_daily/documents/mapper.py`
- 新建：`tests/documents/test_mapper.py`

### Step 1：写失败测试

用确定性 `ParsedDocument` fixture 覆盖：

- 文本块保存 page、bbox、reading_order 和来源映射；
- heading 建立 section 层级并绑定 block；
- 双栏顺序按解析器 reading order 保留，并可记录不确定 issue；
- Figure/Table label 与 caption 提取；
- 缺失 caption 返回 `None` + `caption_not_found`；
- 跨页 caption 引用多个 block 并记录 `cross_page_caption`；
- 跨页 table 生成一个 `VisualArtifact` 和多个 `VisualRegion`；
- 无可靠 bbox 返回结构化 issue，不伪造坐标。

### Step 2：最小实现

映射以 Docling item label 和 provenance 为主，caption 文本规则只做显式 label 解析和相邻
来源绑定。稳定 ID 由 PDF hash、源 item ID、page、bbox 和类型生成。所有启发式判断带
`mapping_method` 与 confidence。

### Step 3：验证并提交

```powershell
uv run pytest tests/documents/test_mapper.py -q
git add src/zotero_arxiv_daily/documents/{sections.py,captions.py,mapper.py} tests/documents/test_mapper.py
git commit -m "feat: map document sections and visual evidence"
```

## Task 7：实现证据裁剪与版本化缓存

**文件：**

- 新建：`src/zotero_arxiv_daily/documents/images.py`
- 新建：`src/zotero_arxiv_daily/documents/cache.py`
- 新建：`tests/documents/test_images.py`
- 新建：`tests/documents/test_cache.py`

### Step 1：写失败测试

覆盖：

- 每个有效 region 生成确定性 PNG 路径；
- 裁剪像素范围与 bbox 对应；
- 越界 bbox 拒绝；
- 裁剪失败返回 `visual_image_not_extracted`；
- 缓存键包含 PDF hash、Schema/parser/mapper/config 版本；
- 版本变化或内容损坏强制 miss；
- 写入原子且 JSON 重新校验；
- 路径逃逸缓存根目录被拒绝。

### Step 2：最小实现

使用 PyMuPDF 页面 pixmap + clip 保存 PNG。缓存实现沿用 Stage 1 store 的临时文件、fsync、
重新校验和 `os.replace` 模式。

### Step 3：验证并提交

```powershell
uv run pytest tests/documents/test_images.py tests/documents/test_cache.py -q
git add src/zotero_arxiv_daily/documents/{images.py,cache.py} tests/documents/test_images.py tests/documents/test_cache.py
git commit -m "feat: cache document graphs and evidence crops"
```

## Task 8：组装 Stage 2 批处理管线

**文件：**

- 新建：`src/zotero_arxiv_daily/pipeline/documents.py`
- 修改：`src/zotero_arxiv_daily/pipeline/__init__.py`
- 修改：`config/base.yaml`
- 修改：`config/custom.yaml`
- 新建：`tests/pipeline/test_documents.py`

### Step 1：写失败测试

覆盖：

- 只向下载器传入选中论文，最多 5 篇；
- 未选中论文零下载、零解析；
- 下载、预检、解析、映射、裁剪、校验按顺序执行；
- 单篇下载或解析失败不影响其他论文；
- 扫描件、损坏件和部分映射产生准确状态；
- dry-run/离线测试不真实联网、不初始化真实模型；
- 配置包含 timeout、retry、大小、页数、缓存根目录和解析版本。

### Step 2：最小实现

定义 `DocumentPipelineSettings`、`DocumentPipelineDependencies` 和
`build_document_batch(batch, settings, dependencies)`。每篇论文用独立错误边界，输出
`DocumentBatchResult`。本阶段不接入旧邮件 executor，也不自动串联 Stage 3。

### Step 3：验证并提交

```powershell
uv run pytest tests/pipeline/test_documents.py -q
git add src/zotero_arxiv_daily/pipeline config tests/pipeline/test_documents.py
git commit -m "feat: build isolated stage 2 document pipeline"
```

## Task 9：离线集成、回归与文档更新

**文件：**

- 新建：`tests/documents/test_stage2_offline.py`
- 修改：`docs/IMPLEMENTATION_PLAN.md`
- 修改：`docs/BASELINE.md`

### Step 1：写离线集成测试

动态生成小型 PDF，并注入本地下载 transport 与伪 Docling converter，验证从
`CandidateBatch` 到 `DocumentBatchResult` 的完整路径。断言无网络、无真实模型、无 LLM、
未选中论文不下载，并检查正文/visual/caption/page/bbox/image/source mapping。

### Step 2：运行分层验证

```powershell
uv run pytest tests/analysis/test_document_schemas.py tests/documents tests/pipeline/test_documents.py -q
uv run pytest tests/analysis/test_stage1_schemas.py tests/interest tests/candidates tests/retriever/test_arxiv_metadata.py tests/pipeline/test_candidates.py -q
uv run pytest -q
uv run pytest -m "slow or not slow" -q
```

完整配置中的既有两个 Windows spawn 失败和 Hugging Face slow 环境失败必须按实际输出
记录，不能跳过或伪装通过。

### Step 3：更新文档

- 在 `docs/IMPLEMENTATION_PLAN.md` 将 Stage 2 标为完成，仅当所有验收门满足；
- 在 `docs/BASELINE.md` 记录 Docling/PyMuPDF 版本、fixture 策略、命令、通过/失败数量、
  模型准备边界和已知环境失败；
- 明确 Stage 3 尚未开始。

## Task 10：安全扫描、独立审查与收尾

### Step 1：安全和仓库卫生

```powershell
git status --short
git diff --check
git ls-files "*.pdf" ".env" ".env.*" "data/papers/**" "data/zotero/**" "cache/**"
git diff --cached --numstat
```

再对已跟踪文本扫描常见私钥、token 和真实凭据模式；不得输出环境变量值。

### Step 2：请求独立代码审查

使用 `superpowers:requesting-code-review`，让独立审查代理比较 Stage 1 基线提交与 Stage 2
HEAD，重点检查 Stage 边界、下载安全、Docling 模型联网、证据映射、路径安全、失败隔离、
测试有效性和仓库卫生。所有确认的 Critical/Important 必须修复并重新验证。

### Step 3：最终验证与提交

使用 `superpowers:verification-before-completion` 重新执行 Task 9 的四组测试和安全扫描，
基于新鲜输出更新文档，然后创建最终独立提交：

```powershell
git add docs/IMPLEMENTATION_PLAN.md docs/BASELINE.md tests/documents/test_stage2_offline.py
git commit -m "docs: record stage 2 verification"
```

### Step 4：开发分支收尾

使用 `superpowers:finishing-a-development-branch` 检查分支差异和可集成状态。遵循用户要求：
不创建 Pull Request、不推送 upstream；保留 Stage 2 分支与 worktree，并报告提交 ID。
