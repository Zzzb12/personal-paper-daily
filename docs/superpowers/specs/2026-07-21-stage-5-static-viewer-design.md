# Stage 5：静态网页阅读器设计

日期：2026-07-21  
状态：已批准，待实现计划审阅  
范围：仅 Stage 5

## 目标与边界

Stage 5 从 Stage 4 的 `ValidatedPaperAnalysis`、`ValidationReport` 和
`ValidationBatchResult` 构建零服务器依赖的静态站点。它不读取未封装的 Stage 3
`PaperAnalysis`，不重算验证结论，也不生成新的论文判断。所有页面在无 JavaScript
或 JavaScript 失败时仍保留完整可读正文。

本阶段交付每日索引页和每篇已发布论文的详情页；详情页按固定阅读顺序展示英文原题、
中文标题、推荐理由、研究问题、Insight、Insight 形成逻辑、对应 Figure/Table 及其支撑
说明、Method、参数、消融、实验结论、局限性和安全链接。缺失值统一呈现为“论文未明确提供”。

不实现 Feishu/邮件、Actions、部署、登录、数据库、网络 API、PDF 下载或解析、OCR/VLM、
LLM 调用、Stage 8 反馈或 localStorage 状态；也不改变 Stage 4 validator 的语义。

## 方案选择

采用 Python 严格模型、内置 HTML 模板和本地 CSS 的静态生成器，而不引入 Node/React 构建链。
这与现有 Python/Pydantic/pytest 技术栈一致，离线构建可复现，模板默认转义文本，页面本身
不依赖运行时框架或 CDN。Hermes 的布局、卡片和静态 JSON 组织只作为经许可的视觉/交互思路；
不会复制其真实论文数据、缓存、图片、状态或 Git 历史。

## 输入、发布策略与数据流

```text
ValidationBatchResult + CandidateBatch + Document/Evidence roots
  -> ViewerInputLoader（严格解析、唯一性与批次身份检查）
  -> PublicationPolicy（只读 Stage 4 状态）
  -> Page models（IndexPageModel / PaperPageModel）
  -> AssetResolver + EvidenceImagePublisher（受控本地复制）
  -> TemplateRenderer（自动转义）
  -> 原子输出（HTML、CSS、manifest、站点 JSON）
```

`valid` 且 `publication_eligibility=eligible` 生成完整详情页；`partial` 仅在配置允许时生成，
并在索引与详情页显示明显的文字、图标和结构化 validation issue；`invalid`、`failed`、
`skipped` 默认不进入正常公开阅读输出，只记录最小、公开安全的 `ViewerBuildIssue`，可选本地
诊断输出不得伪装成论文页。网页层不得降低 severity、删除 error 或把 blocked 改成 eligible。

每个论文在进入页模型前必须具有匹配的 validated 封装和 report。重复 `paper_id`、批次身份
不一致或不安全输入只影响该论文，其他论文继续生成。输入集合和页面/资源排序均按稳定 paper ID
与既有批次顺序决定；重复构建得到字节级稳定或语义等价的输出。

## 页面信息架构与阅读体验

索引页包含批次日期、总数、valid/partial 数、空状态、论文卡片和状态过滤入口。卡片展示英文/
中文标题、推荐理由摘要、主要分类、validation 状态以及详情、arXiv/PDF 链接。发布论文按稳定顺序
显示；手机为单列卡片，桌面为受限宽度的可扫描列表。

详情页使用 `header`、`nav`、`main`、`article`、连续层级 heading 与返回索引链接。页首显示双语
标题、链接区、状态和 partial 警告。正文固定为：推荐理由与研究问题；Insight 与形成逻辑；紧邻
Insight 的 evidence figure/table；Method 总体流程及模块；已有工作区别；参数表与对应消融；主要
实验结论；作者明确局限性。每段 Claim 显示“作者陈述 / 系统总结 / 系统推断”文字标签；
`inferred=true` 额外显示“系统推断，非作者直接陈述”，不只用颜色区分。

证据卡使用 `figure`、图片和 `figcaption`，并显示 label、caption、PDF page、section、confidence、
validation status、来源类型以及“该图表如何支撑 Insight”。缺图或损坏图显示结构化降级说明，
绝不尝试外网回退。参数和消融使用带 `caption`/可访问说明的语义表格；移动端以卡片化单元或
可横向滚动的受控容器保证不压缩关键内容且不造成页面横向溢出。

视觉语言采用暖白纸张、深墨文字、低饱和靛蓝链接和状态色的研究笔记风格；状态还辅以图标及
文本。正文最大宽度约 72ch，中文与英文均使用系统字体栈。交互只允许轻量渐进增强；CSS 提供
`prefers-reduced-motion: reduce`，无外部字体、CDN 或大型依赖。

## 资源、路径与安全

生成根目录默认 `outputs/viewer/`，必须被 Git 忽略；测试使用 pytest 临时目录。受追踪的源文件
限于 Python、模板、CSS、小型人工 fixture 和文档。`BuildManifest` 保存公开安全的 build/template
版本、相对输出路径、内容哈希和计数，不保存绝对本地路径、私钥、环境值、论文全文或 LLM 输出。

`EvidenceImagePublisher` 只接受 Stage 2/3/4 已解析的本地图像路径，并在解析真实路径后验证其位于
批准 evidence root 内；拒绝 `..`、绝对输出逃逸、符号链接逃逸、未知扩展名、超限大小和损坏文件。
复制文件采用 SHA-256 内容寻址名称，避免同名覆盖，目标始终位于 `assets/evidence/`。所有输出写入
临时同级目录，经 fsync/验证后使用 replace 原子发布；`--dry-run` 不创建目录或文件。

模板引擎默认 HTML 转义所有论文、caption、分析和 issue 文本；不提供 raw HTML 通道。外链仅允许
`https`（必要时受控 `http` 仅用于人工 fixture），拒绝 `javascript:`、`data:` 与含凭据 URL；链接
使用有意义文本与 `rel="noopener noreferrer"`。站点设置适合纯静态内容的 CSP，且无内联脚本或
外部请求。构建日志只记录 paper ID、状态、固定错误码和计数。

## 模块、配置与缓存

新增 `viewer` 包的边界为：`ViewerInputLoader`、`PublicationPolicy`、`StaticViewerBuilder`、
`TemplateRenderer`、`AssetResolver`、`EvidenceImagePublisher`、`PageManifest/BuildManifest`、
`ViewerBuildIssue`、`IndexPageModel`、`PaperPageModel` 和离线 CLI。所有跨模块对象均为冻结、
`extra="forbid"` 的 Pydantic Schema；文件系统、时钟和输出根可注入测试。

`viewer_pipeline` 配置将严格定义输出根、站点标题、每页合理上限、是否允许 partial、单批资源复制
上限、单图大小限制、build version、template version、evidence roots 与诊断页开关，并采用安全默认值。
manifest 内容哈希用于跳过未变资源；模板或配置版本变化使对应输出失效。构建过程不得执行网络请求。

## 可访问性与测试

测试先于实现，逐项执行 RED → 最小实现 → GREEN → 相关回归。覆盖 validated-only 输入、
valid/partial/invalid 策略、单篇失败隔离、稳定排序、重复 ID、空批次、固定字段/缺失文案、
source/inferred 标签、图表和参数/消融关联、安全链接、HTML 转义、路径/符号链接/输出逃逸、
资源冲突和损坏图片、原子写入、dry-run、manifest 隐私和零网络。

HTML 测试验证语义元素、heading 层级、figure/figcaption、alt、表格说明、viewport、焦点样式、
reduced motion 与无 JavaScript 内容。Playwright 将对人工 fixture 站点在 1440×900 与 390×844
检查索引、详情、partial、图表、参数表、空状态、键盘导航、禁用 JavaScript、控制台错误和横向
溢出，并实际查看被忽略临时目录内截图。

## Hermes 许可证与归属

实现前读取 `F:\Yan_0\Video_generaton\PaperDaily\hermes-reference\hermes-arxiv-agent-main\LICENSE`
并核对 `docs/BASELINE.md` 已记录的项目所有者授权结论。若直接复制或实质改编 Hermes 文件，须在
本设计与实现文档列出源文件、保留全部适用版权/许可证文本；不合并历史、不用 submodule，也不复制
其数据或状态。若只采纳布局思路而不复制代码，则记录为“理念参考，无代码复制”。

## 验收、回滚与后续边界

完成前运行冻结同步、Stage 5 focused/integration、Playwright、Stage 4/3/2/1 回归、默认和完整
pytest、compileall、diff check、离线构建、追踪/安全扫描与独立复审。既有 Windows spawn timeout
以及 Hugging Face slow cache/network 失败只如实报告，不通过修改无关模块消除。

回滚只需停止调用 viewer CLI、删除忽略的 `outputs/viewer/`，并回退 Stage 5 提交；Stage 4 验证
结果和私有 evidence 根保留供后续重建。Stage 6 仍未开始。
