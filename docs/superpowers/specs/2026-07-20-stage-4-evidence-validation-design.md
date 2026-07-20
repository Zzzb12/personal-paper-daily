# Stage 4：证据校验与防幻觉设计

日期：2026-07-20
状态：已批准
范围：仅 Stage 4

## 1. 目标与边界

Stage 4 消费 Stage 1 `CandidatePaper`、Stage 2 `DocumentGraph`、Stage 3
`EvidencePacket`、`PaperAnalysisResult` 和 `PaperAnalysis`，以完全确定性、离线的方式判断分析
是否满足证据完整性要求并具备发布资格。验证器不重新阅读 Abstract 或 PDF，不生成另一套分析，
也不修改、补齐或重写 Stage 3 保存的来源映射。

本阶段必须保留以下既有边界：每批最多 5 篇、每篇最多 3 个支撑图表、分析内容只来自
`DocumentGraph`、Candidate 只提供 identity/英文标题/链接、Abstract-only 证据不能支撑成功
Insight、Stage 3 partial/failed 不进入昂贵调用缓存、Stage 3 完整来源映射不可覆盖。

只有 `valid` 结果具有发布资格。`partial` 和 `invalid` 均明确阻断后续发布；Stage 5 只能消费
Stage 4 的验证封装，不得直接消费未验证的 `PaperAnalysis`。

## 2. 方案比较与选择

### 方案 A：只依赖 Pydantic Schema

优点是实现量小，能拒绝字段类型和部分局部不变量。缺点是无法验证跨 Candidate、DocumentGraph、
EvidencePacket、PaperAnalysis 的引用和来源一致性，也无法判断缓存身份和发布资格，不满足验收。

### 方案 B：分层纯函数校验器（采用）

建立只读索引并逐层验证输入身份、证据注册表、claim、SupportingVisual、Parameter 和 Ablation。
每条规则产生结构化、安全的 `ValidationIssue`，再统一计算 `valid/partial/invalid` 和发布资格。
该方案确定性强、易于 TDD、能隔离单篇失败，也不会引入网络或模型成本。

### 方案 C：校验器加 LLM 纠错重试

可能自动修复格式或缺失引用，但会引入费用、重试状态和新幻觉面。本阶段没有实现必要性，因此
不采用。未来若单独批准，必须默认关闭、严格限制次数和成本、注入 fake client、不得绕过验证器，
且 failed/partial 纠错结果不得缓存。

## 3. 模块与依赖方向

```text
CandidatePaper + DocumentGraph + EvidencePacket + PaperAnalysisResult
  -> validation identity checks
  -> evidence registry/provenance checks
  -> claim/visual/parameter/ablation checks
  -> ValidationReport
  -> ValidatedPaperAnalysis
  -> ValidationBatchResult
```

新增模块：

- `analysis/validation_schemas.py`：Stage 4 严格 Schema 和状态模型；
- `analysis/validator.py`：无网络、无文件写入的单篇纯校验逻辑；
- `analysis/validation_cache.py`：版本化、原子、损坏即 miss 的验证报告缓存；
- `pipeline/validation.py`：最多五篇、逐篇失败隔离和离线 golden CLI。

验证模块只依赖 Stage 1–3 Schema，不依赖 LLM client、网络、delivery、viewer、feedback 或旧
`Executor`。Stage 3 文件仅在确有必要时增加导出，不改变既有字段含义或核心接口。

## 4. Stage 4 Schema

继续复用冻结、`extra="forbid"` 的 `StrictModel`。版本常量：

- `VALIDATION_SCHEMA_VERSION = "1.0"`
- `VALIDATOR_VERSION = "stage4-v1"`

### 4.1 `ValidationIssue`

字段：

- `code`：稳定错误码；
- `severity`：`info | warning | error`；
- `paper_id`；
- `claim_id` 或 `field_path`（均可空，但至少一个定位字段或 evidence/visual ID 存在）；
- `evidence_id`；
- `visual_id`；
- `message`：固定、短、安全文本，不含 prompt、response、证据正文、论文全文、URL query、
  本地私人路径、凭据或异常原文。

### 4.2 结果模型

- `ClaimValidationResult`：`claim_id`、`status`、已解析 evidence IDs 和该 claim 的 issue codes；
- `ValidationReport`：validator/schema 版本、paper ID、`valid | partial | invalid`、发布资格、
  claim 结果、issue 列表和输入 fingerprint；
- `ValidatedPaperAnalysis`：原始 `PaperAnalysis` 的不可变引用与 `ValidationReport`，不删除 claim、
  不覆写 evidence candidate 或 provenance；
- `ValidationPaperResult`：单篇 `validated | partial | invalid | failed | skipped` 批处理状态、
  可空验证封装、issues、cache hit 和 processing time；
- `ValidationBatchResult`：run ID、validator version、最多五个结果和 cache hit 数量。

`PublicationEligibility` 使用 `eligible | blocked`。仅当 report status 为 `valid` 且不存在 error
issue 时为 `eligible`；其他情况均为 `blocked`。warning 本身不阻断发布，但 warning 不会把
`partial` 提升为 `valid`。

## 5. 输入身份与不变性

单篇验证必须先确认：

1. Candidate、EvidencePacket、PaperAnalysisResult、PaperAnalysis 的 `paper_id` 一致；
2. `PaperAnalysis.english_title` 精确等于 Candidate title；
3. PDF/arXiv/code 链接精确等于 Candidate 链接，`None` code URL 保持 `None`；
4. `EvidencePacket.document_fingerprint` 等于 `DocumentGraph.content_fingerprint`；
5. `PaperAnalysis.evidence_candidates` 与 `EvidencePacket.candidates` 按顺序、内容完全相等；
6. Stage 3 `failed/skipped` 不产生 validated analysis；Stage 3 `partial` 最多得到 Stage 4
   `partial`，不得升级为 `valid`。

任一 identity、标题、链接、packet 或 candidate tampering 属于 error，report 为 `invalid`。

## 6. 证据与来源校验

构建以下只读索引：Document block/section/visual、EvidencePacket candidate、analysis claim、
parameter 和 ablation。

### 6.1 Evidence candidate

- evidence ID 在 packet 内唯一且 paper ID 一致；
- text candidate 的 block IDs 必须存在，page/section/section path/region SourceMapping 可解析；
- Figure/Table candidate 的 `visual_id` 必须指向真实 `DocumentGraph.visuals`；
- `kind`、`visual_id`、`label`、`caption`、首个 `pdf_page`、`section_id`、`section_title`、
  `section_path`、全部 region 的 bbox/image path/SourceMapping/confidence，以及 candidate confidence
  必须与 Stage 2/3 来源精确一致；
- 不存在或被修改的 Figure/Table 为 error；confidence 不能绕过不一致。

Section path 由 `DocumentGraph.sections` 的 parent 链确定；无法解析、循环或不一致均失败，不猜测
标题或层级。验证器比较 `Path` 值但安全消息不显示路径内容。

### 6.2 Claim

- 所有 claim ID 在整篇分析中唯一；
- 每个 evidence ID 必须存在于对应 packet；
- claim kind 必须与所在字段一致，包括 method module、parameter role、ablation conclusion 和
  support explanation；
- `system_inference -> inferred=true`；`author_statement/system_summary -> inferred=false`；
- 每个成功 Insight 至少有一个非 Abstract-only 证据；否则 error/invalid；
- Schema 本身通常已阻止 kind/source 错误，Stage 4 仍执行防御性检查，以覆盖损坏缓存或绕过
  Schema 构造的输入。

### 6.3 SupportingVisual

- evidence ID 必须指向 Figure/Table candidate，不能指向 text；
- visual 必须绑定至少一个存在的 Insight；
- `support_explanation` 必须为非空结构化 `ClaimRecord(kind="support")`，引用该 visual evidence；
- materialized visual 的全部来源字段必须与 packet candidate 完全一致；
- 单篇不得超过 3 个 SupportingVisual。

### 6.4 Parameter 与 Ablation

- 每个 Ablation ID 唯一；
- Ablation 的每个 visual evidence ID 必须存在且为 Figure/Table；普通 text 不可满足；
- Ablation 的每个 parameter name 必须精确解析到一个真实 `ParameterRecord.name`；
- Parameter 的 `ablation_ids` 必须解析到真实 Ablation；
- Parameter 的 `evidence_ids` 必须解析到 packet；
- Parameter name 应唯一，避免消融名称产生歧义；
- `final_value`、`symbol`、`selection_method`、`per_model_tuning` 缺失时保持 `None`，不产生错误；
- 论文未提供 parameter、ablation、limitation 或 code URL 时，空 tuple/`None` 是合法状态，不补齐。

## 7. 错误等级、状态和发布资格

### 7.1 `invalid`

以下 error 使单篇 `invalid` 并阻断发布：输入 identity/标题/链接篡改；analysis evidence candidates
被修改；unknown/dangling evidence；Figure/Table kind/label/caption/page/section/path/bbox/image/
SourceMapping/confidence 不一致；SupportingVisual 指向 text 或未知 Insight；Abstract-only Insight；
Ablation 无真实 Figure/Table；parameter/ablation 悬空或歧义；claim/ablation/parameter 等要求唯一的
ID 重复；claim kind 或 source/inferred 不一致；验证输入损坏或无法安全解析。

### 7.2 `partial`

Stage 3 已标记 partial、缺少可发布核心 Insight、或验证范围内存在无法证明但不构成来源篡改的
内容时为 `partial`。partial 永远阻断发布，且不得静默标为 valid。

### 7.3 warning/info

论文未明确提供可选 parameter、ablation、limitation 或 code URL 不产生 warning；它们保持
`None`/空 tuple。非阻断、可操作性诊断可使用 warning/info，且在没有 error/partial 原因时不影响
`valid` 和发布资格。

## 8. 验证缓存与运行身份

验证器独立版本必须进入 cache identity 和 `ValidationBatchResult`。缓存 identity 至少包含：

- validator/schema version；
- paper ID 与 Candidate title/link fingerprint；
- DocumentGraph schema/parser/mapper/config、PDF hash 和 content fingerprint；
- EvidencePacket schema/builder/packet fingerprint；
- Stage 3 analysis schema、generation cache key 和完整 deterministic analysis fingerprint。

任一版本或内容变化产生 cache miss。缓存位于忽略的 `cache/validation`，限制单文件大小，路径必须
保持在根目录内；读取损坏、超限、identity/report 不一致的条目时返回 miss。写入使用同目录临时
文件、flush/fsync、重新验证和 `os.replace`。缓存的是确定性 validation result，不更改 Stage 3
昂贵调用缓存规则。

## 9. 批处理与离线 golden

`build_validation_batch` 按 CandidateBatch `selected_for_full_analysis` 顺序处理最多 5 篇，要求
Candidate、Document、EvidencePacket 和 Analysis 输入一一对应。重复 paper result、run ID 不一致、
缺少输入或单篇异常产生该篇结构化 failed/skipped/invalid 结果，不终止其他论文。

提供 `python -m zotero_arxiv_daily.pipeline.validation --dry-run --offline-fixture ...`。fixture 为人工
构造、小体积、许可明确的 Figure/Table + Insight + Parameter + Ablation golden 数据。dry-run 不读
环境凭据、不构造真实 LLM/Zotero/network client、不下载 PDF/模型、不写缓存或运行结果，输出仅为
状态计数、validator version、cache hit 数量和 publication eligible 数量。

## 10. TDD 与测试矩阵

每个行为严格执行 RED -> 最小实现 -> GREEN -> 相关回归 -> 提交。核心 validator 不用 mock
绕过；fixture 只使用人工短文本和人工路径。

必须覆盖：

- 完全离线 golden valid；
- unknown evidence ID；
- Abstract-only Insight；
- Figure/Table label、caption、PDF page、bbox、section/source mapping、image path 篡改；
- visual evidence 实际指向 text；
- Ablation 没有 Figure/Table；
- Parameter 引用不存在 Ablation 或 evidence；
- Ablation 引用不存在 Parameter；
- source/inferred 不一致；
- claim kind 错位；
- duplicate claim/ablation/parameter ID；
- Candidate title/PDF/arXiv/code 链接篡改；
- Stage 3 partial 输入不得升级；
- corrupt validator cache；
- validator version 变化 cache miss；
- 单篇失败不终止批次；
- optional facts 为 `None`/空 tuple 时合法。

## 11. 安全与成本

- 不读取剪贴板或真实环境变量值；不使用聊天中出现过的凭据；
- 不调用收费服务，不下载论文或 Hugging Face 模型；
- 错误消息只包含固定类别与非私人 ID，不包含论文正文、prompt、response、密钥或私人路径；
- cache/run outputs 位于 Git 忽略目录；
- 不新增网络代码；既有网络代码的 timeout/retry 边界不改变；
- acceptance 完全离线、零凭据、零费用。

## 12. 明确非目标

- Stage 5 静态网页；飞书、邮件、反馈、GitHub Actions；
- 新 PDF parser、OCR、VLM、图表数值识别；
- 新完整 LLM 分析流程或真实/收费 LLM 调用；
- 真实 Zotero、邮件或飞书调用；
- 自动发布、创建 PR 或推送 upstream；
- 纠错重试；
- 修改、重写或删除 Stage 1–3 核心接口；
- 对无法从结构化来源确定的论文内容作语义补全。

## 13. 验收与回滚

完成前真实运行冻结同步、Stage 4 专属测试、Stage 3/2/1 回归、默认测试、`slow or not slow`
完整配置、compileall、`git diff --check`、安全/Git 跟踪扫描和离线 golden。两个 Windows spawn
失败与一个 Hugging Face slow 失败按既有基线如实报告，不修改无关代码消除。

独立审查覆盖 Stage 3 最终提交 `8603098474db25826de88af407bdfffb00cdb2b7` 到 Stage 4 HEAD；
所有确认的 Critical/Important 通过新的 RED -> GREEN 修复并复审至 Ready。

回滚时停用 Stage 4 validation pipeline 并回退 Stage 4 提交；保留 Stage 1–3 候选、DocumentGraph、
EvidencePacket 和 PaperAnalysis。验证缓存是可删除的忽略派生物。不得以绕过 validator 作为回滚。
