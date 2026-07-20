# Stage 2：PDF 正文、页码与 Figure/Table 提取设计

日期：2026-07-20  
状态：已批准  
范围：仅 Stage 2

## 1. 背景与目标

Stage 1 已产出 metadata-only 的候选论文批次，并用
`selected_for_full_analysis` 标记最多 5 篇需要进入完整分析的论文。Stage 2
只处理这些已选论文：安全下载 PDF，提取带页码、阅读顺序、章节、坐标和来源映射的正文，
并提取可追溯的 Figure/Table、caption 与证据裁剪图。

本阶段不生成中文论文解读，不调用 LLM，也不实现网页、飞书、邮件、反馈或
GitHub Actions。输出是 Stage 3 和 Stage 4 可以消费和验证的版本化 `DocumentGraph`。

## 2. 已确定的技术选择

- 使用 Docling Standard PDF Pipeline 作为主解析器。
- 使用 PyMuPDF 做 PDF 预检、页数和页面尺寸确认、坐标边界校验、页面渲染及裁剪。
- 不在本阶段启用 VLM，不运行 GROBID 服务；GROBID 仅作为未来可插拔后端。
- 项目定义自己的稳定 Pydantic Schema，不让业务层直接依赖 Docling 内部类型。
- 一个逻辑 `VisualArtifact` 可以包含多个跨页 `VisualRegion`。
- caption 可以引用多个跨页文本块；无法可靠绑定时保留 `null` 并记录结构化 issue。
- 不承诺 OCR。扫描件和无文本层 PDF 必须返回明确状态。

## 3. 数据流与模块

```text
CandidateBatch
  -> SelectionGate
  -> SafePdfDownloader
  -> PdfInspector
  -> DoclingParser
  -> DocumentMapper
  -> EvidenceImageExtractor
  -> DocumentValidator
  -> PaperDocumentResult
```

### 3.1 SelectionGate

输入 Stage 1 `CandidateBatch`，只保留 `selected_for_full_analysis=true` 的论文，
并再次强制最多 5 篇。未选中论文不得触发下载、解析或模型初始化。

### 3.2 SafePdfDownloader

下载器必须提供：

- 显式连接和读取 timeout；
- 有限次数 retry，并只重试可恢复错误；
- 最大响应体字节数；
- HTTP 状态和 `Content-Type` 校验；
- `%PDF-` magic bytes 校验；
- 临时文件写入和原子重命名；
- SHA-256、大小、响应类型和缓存命中信息；
- 不记录认证头、URL 查询参数或环境变量内容。

真实运行文件只能写入 Git 忽略的内容寻址缓存目录。

### 3.3 PdfInspector

PyMuPDF 负责在 Docling 前检查：

- PDF 是否损坏或加密；
- 页数和每页尺寸；
- 是否存在可用文本层；
- 是否可能为扫描件；
- 是否超过页数或资源限制。

预检失败不得继续调用 Docling。

### 3.4 DoclingParser

解析器通过窄接口调用 Docling，并返回项目内部的中间表示。默认使用 Standard PDF
Pipeline 的布局、表格和图片能力；禁用远程服务和 VLM。模型不可用时返回
`parser_model_unavailable`，不能在测试或 dry-run 中隐式联网获取模型。

### 3.5 DocumentMapper

映射器将 Docling 页面、文本、层级、表格、图片和 provenance 归一化成项目 Schema。
所有页码转换成 1-based 物理 PDF 页码。PyMuPDF 对页面尺寸和 bbox 做边界校验。
无法稳定映射的项保留 issue，不通过猜测补齐。

### 3.6 EvidenceImageExtractor

提取器使用 PyMuPDF 按 `VisualRegion` 的 page+bbox 渲染页面裁剪图。裁剪图路径由
PDF hash、page、bbox 和渲染配置决定。一个跨页表格或图可以由多个 region 组成，
caption 也可以跨页引用多个 block。

### 3.7 DocumentValidator

验证器检查：

- page、section、block、visual 和 caption 引用不存在悬空项；
- bbox 有限、方向正确且位于页面范围内；
- 每个 visual 至少有一个有效 region；
- 图片路径属于预期缓存根目录；
- 成功状态不存在阻断性 issue。

单篇论文失败必须隔离，不能阻断同批其他论文。

## 4. 稳定数据模型

### 4.1 PdfArtifact

- `source_url`
- `local_path`
- `sha256`
- `byte_size`
- `content_type`
- `page_count`
- `downloaded_at`
- `cache_hit`

### 4.2 BoundingBox 与 SourceMapping

`BoundingBox` 保存 `left`、`top`、`right`、`bottom` 和坐标原点。
`SourceMapping` 保存 parser 名称和版本、源对象 ID、PDF page、bbox、映射方法和置信度。
映射必须保留 Docling provenance，不能只保存最终文本。

### 4.3 DocumentBlock 与 DocumentPage

`DocumentBlock` 至少保存稳定 ID、类型、文本、PDF page、bbox、阅读顺序、章节引用和
来源映射。`DocumentPage` 保存 1-based 物理页码、可选印刷页标签、宽高、文本层状态和
有序 block 引用。

### 4.4 SectionNode

保存稳定 section ID、标题、层级、父节点、block 引用、起止 PDF page、来源映射和
置信度。无法可靠判断章节层级时保留较平的结构并记录 issue。

### 4.5 VisualRegion 与 VisualArtifact

`VisualRegion` 保存 PDF page、bbox、image path、来源映射和置信度。
`VisualArtifact` 保存：

- `kind = figure | table`；
- `label: str | null`；
- `caption: str | null`；
- `caption_block_ids`；
- `section_id`；
- 一个或多个 `regions`；
- 综合提取置信度；
- 结构化 issues。

一个跨页对象是一个逻辑 artifact，其每一页有独立 region。caption 缺失、label 缺失或
bbox 不可靠时必须保留 `null` 或 issue，不得从正文臆造。

### 4.6 DocumentGraph 与 PaperDocumentResult

`DocumentGraph` 保存 Schema、parser、mapper 和配置版本，PDF artifact、pages、blocks、
sections、visuals、文档级 issues 和可重复生成的内容指纹。

`PaperDocumentResult` 关联 Stage 1 `paper_id`，状态为
`success | partial | failed | skipped`，保存可选 DocumentGraph、issues、处理时间和缓存信息。

## 5. 结构化失败状态

至少支持：

- `download_timeout`
- `download_retry_exhausted`
- `invalid_content_type`
- `size_limit_exceeded`
- `invalid_pdf_signature`
- `corrupt_pdf`
- `encrypted_pdf`
- `no_text_layer`
- `scanned_document`
- `parser_model_unavailable`
- `parser_failed`
- `two_column_order_uncertain`
- `cross_page_caption`
- `caption_not_found`
- `visual_bbox_not_found`
- `visual_image_not_extracted`
- `source_mapping_invalid`

`success` 表示核心映射完整；存在可用文档图但有非阻断证据缺口时为 `partial`；无法产出
可信文档图时为 `failed`；未被 Stage 1 选择时不进入下载管线，批次结果可记为 `skipped`。

## 6. 缓存边界

- 原始 PDF：以内容 SHA-256 标识。
- Docling 转换：键包含 PDF hash、Docling 版本和解析配置版本。
- DocumentGraph：键包含 PDF hash、Schema 与 mapper 版本。
- 证据裁剪图：键包含 PDF hash、page、bbox、缩放和渲染配置。

所有缓存都位于 Git 忽略目录。缓存损坏、版本不匹配或校验失败时重新生成；不得把旧版本
结果伪装为有效命中。

## 7. 测试策略

严格按 TDD 添加：

1. Schema、引用完整性和 bbox 校验测试；
2. SelectionGate 只处理已选论文且最多 5 篇的测试；
3. 下载 timeout、retry、大小、类型、magic bytes 和原子写入测试；
4. 损坏、加密、无文本层和扫描件预检测试；
5. Docling 适配器及版本化中间表示测试；
6. 双栏阅读顺序、章节映射测试；
7. Figure/Table、缺失 caption、跨页 caption、多 region 测试；
8. 页面裁剪和坐标边界测试；
9. 缓存命中、版本失效和损坏恢复测试；
10. 多论文失败隔离和离线端到端测试。

测试 PDF 优先用 ReportLab 在临时目录动态生成。代表性 fixture 包括单栏图表、双栏、
跨页 caption、多页表格、图片型扫描件和损坏字节流。不提交真实论文 PDF。测试中的 HTTP
使用本地 mock transport，不访问 arXiv。Docling 单元测试使用伪转换对象，不下载模型。

按 PDF skill 将代表性动态 fixture 渲染为 PNG 做人工视觉核验，PNG 只保存在临时目录。

## 8. 依赖与运行约束

- `docling` 使用项目锁文件支持的兼容版本范围。
- PyMuPDF 继续作为直接运行依赖。
- 测试 fixture 生成依赖放入开发依赖，不进入生产路径。
- Docling 模型准备是显式运维步骤；测试和 dry-run 禁止自动联网。
- 单篇下载大小、页数、解析时长和并发度均由配置限制。

## 9. 明确非目标

- 中文 Insight/Method/Ablation 或任何 LLM 分析；
- OCR 质量保证或扫描论文完整识别；
- VLM 图像理解和图表数值重建；
- 网页、飞书、邮件、反馈和 GitHub Actions；
- 真实 Zotero Key、真实 LLM 或收费服务；
- GROBID 服务部署和双引擎融合；
- Stage 3 及之后业务逻辑。

## 10. 验收标准

- 只下载和解析 `selected_for_full_analysis`，且最多 5 篇；
- 下载器满足 timeout、有限 retry、大小、Content-Type 和签名校验；
- 每个正文块保留 page、section、bbox、阅读顺序和 source mapping；
- Figure/Table 至少保存 label/caption 可空、page、bbox、image path 和置信度；
- 跨页 visual 使用一个 artifact 加多个 region；
- 扫描件、无文本层、双栏不确定、跨页 caption、损坏 PDF 有明确状态；
- 不调用 LLM、收费服务或真实 Zotero；
- Stage 2、Stage 1、默认和完整配置测试均真实运行并报告；
- 不提交 PDF、缓存、密钥或个人 Zotero 数据；
- 独立审查确认的 Critical/Important 全部修复；
- 更新实施计划和基线，创建独立提交，不创建 PR、不推送 upstream。
