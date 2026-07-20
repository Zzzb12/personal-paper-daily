# Stage 3：结构化中文论文分析设计

日期：2026-07-20  
状态：已批准  
范围：仅 Stage 3

## 1. 目标与边界

Stage 3 消费 Stage 1 的 `CandidatePaper` 元数据和 Stage 2 已验证的
`DocumentGraph`，为最多 5 篇 `selected_for_full_analysis` 论文生成严格、版本化的中文
`PaperAnalysis`。论文内容结论只能来自 `DocumentGraph`，候选元数据仅用于英文标题、作者和
PDF/arXiv/code 链接，不得退回 Abstract-only 的完整分析路径。

固定叙事顺序为：

`Insight → 支撑 Figure/Table → Method → 关键参数 → 参数消融 → 实验结论 → 局限性`

本阶段生成“带证据候选引用的结构化草稿”，并执行 Schema、来源类型、引用集合和协议层
完整性检查。Stage 4 负责最终语义证据校验、数值核对、Abstract-only 证明、视觉一致性和
发布资格判定。Stage 3 不实现 Stage 4 的最终强校验器。

本阶段不实现静态网页、飞书、邮件、反馈、GitHub Actions、真实付费调用或真实凭据验收。

## 2. 已选方案

采用“确定性证据包 + 单次结构化 LLM 调用”：

```text
CandidatePaper + DocumentGraph
  -> EvidencePacketBuilder
  -> bounded EvidencePacket
  -> StructuredAnalysisClient
  -> strict PaperAnalysisDraft parsing
  -> protocol/reference-set checks
  -> versioned private cache
  -> PaperAnalysisResult
```

没有采用双 LLM 阶段，因为它使费用、延迟和失败面翻倍；没有采用大量规则抽取参数，因为
论文的符号、参数和值表达差异过大，第一版容易产生不可审计的漏检。确定性筛选负责缩小并
约束输入，单次结构化调用负责中文归纳，Stage 4 再负责最终证据语义审查。

## 3. 模块设计

### 3.1 结构化 Schema

新增 `analysis/paper_schemas.py`，继续使用冻结、`extra="forbid"` 的 Pydantic 模型。

核心类型：

- `ClaimSource = author_statement | system_summary | system_inference`；
- `EvidenceCandidate`：Stage 2 来源事实的只读投影；
- `EvidencePacket`：单篇论文的有界证据集合；
- `ClaimRecord`：Insight、Method、实验结果或局限性声明；
- `SupportingVisual`：保留完整 Figure/Table 来源和支撑解释；
- `MethodModule`：模块名称、作用和证据 ID；
- `ParameterRecord`：名称、符号、作用、最终取值、设置来源、调参范围和消融关联；
- `AblationRecord`：参数、结论和 Figure/Table 证据关联；
- `PaperLinks`：PDF、arXiv 和可空 code URL；
- `PaperAnalysis`：固定顺序的完整中文分析；
- `AnalysisIssue`、`PaperAnalysisResult`、`AnalysisBatchResult`：失败隔离与批处理状态。

`PaperAnalysis` 至少按以下字段顺序序列化：

1. `english_title`
2. `chinese_title`
3. `recommendation_reason`
4. `research_problem`
5. `insights`
6. `supporting_visuals`
7. `insight_formation_logic`
8. `method_overview`
9. `method_modules`
10. `differences_from_prior_work`
11. `parameters`
12. `ablations`
13. `experimental_conclusions`
14. `limitations`
15. `links`
16. `evidence_candidates`
17. `generation`

缺失的单值事实使用 `None`；缺失的多值事实使用空 tuple。Schema 不使用“论文未明确提供”
作为内部哨兵，该中文显示文本属于未来渲染层。

来源类型强制：

- `system_inference` 必须 `inferred=true`；
- `author_statement` 和 `system_summary` 必须 `inferred=false`；
- confidence 只表示归纳/绑定信心，不能替代证据。

### 3.2 确定性证据包

新增 `documents/evidence.py`。输入只能是 `DocumentGraph`，输出 `EvidencePacket`。

章节优先级：

1. Introduction / Motivation / Observation / Analysis
2. Method / Approach / Framework / Algorithm
3. Experiments / Results / Evaluation
4. Ablation Study / Ablations
5. Appendix / Supplementary
6. 其他非 Abstract 章节
7. Abstract，仅作为背景候选

匹配基于标准化章节标题和章节树路径，不改变 Stage 2 的原始标题。Evidence candidate 保存：

- 稳定 `evidence_id`；
- `paper_id`；
- `kind = text | figure | table`；
- `pdf_page`；
- `section_id`、`section_title` 和 `section_path`；
- `block_ids`；
- 有界 `evidence_text`；
- `visual_id`、`label`、`caption`；
- `bbox`、`image_path`、完整 `SourceMapping`；
- Stage 2 extraction confidence；
- `abstract_only` 标志。

文本候选按章节优先级、页码和 reading order 稳定排序。视觉候选按其 section 优先级、页码和
`visual_id` 排序。每篇最多保留 3 个视觉候选；无法解析来源的视觉不进入可引用集合，但保留
结构化 issue。证据包有显式字符预算、候选数量上限和单块文本上限，超限时按确定性顺序裁剪，
不从中间截断 UTF-8 字节。

证据包允许包含 Abstract 背景，但 prompt 和 Schema 将其标记为 `abstract_only`。Stage 3
协议检查拒绝“所有 Insight 证据 ID 都只来自 Abstract”的结果；更全面的间接支撑判断留给
Stage 4。

### 3.3 Prompt

新增 `analysis/prompts/stage3_v1.py`，定义不可变 `PROMPT_VERSION = "stage3-v1"` 和构造器。

system 指令要求：

- 使用简洁、技术准确的中文；
- 保留英文原题、符号、模型、数据集和指标名；
- 只能引用提供的 evidence ID；
- 不得生成未提供的 Figure/Table label、caption、参数、数值、设置、代码链接或局限性；
- 找不到时输出 JSON `null` 或空数组；
- 明确区分 author statement、system summary、system inference；
- 每个 system inference 设置 `inferred=true`；
- 每个 SupportingVisual 必须解释“该图表如何支撑 Insight”；
- 输出必须符合提供的 JSON Schema，不输出 Markdown 包围符或额外文字。

user 内容只包含隐私最小化的 CandidatePaper 展示元数据、版本信息、EvidencePacket 和目标 JSON
Schema。不得加入 Zotero collection path、笔记、API key、环境变量或完整本地文件内容。

### 3.4 可注入 LLM 客户端

新增 `analysis/client.py`：

```python
class StructuredAnalysisClient(Protocol):
    model_identity: str
    def generate(self, request: AnalysisRequest) -> str: ...
```

`OpenAICompatibleAnalysisClient` 是生产适配器，复用已安装的 OpenAI Python SDK，但：

- 仅从构造参数接收 key/base URL/model；领域对象不读取环境；
- SDK 内部 retry 设为 0，由 Stage 3 明确控制；
- 使用显式 request timeout；
- 请求结构化 JSON；
- 响应按 UTF-8 字节数执行上限；
- 异常翻译为不含 URL query、key、prompt 或论文正文的安全错误类别。

重试只覆盖 timeout、连接失败、408、409、425、429 和 5xx；认证、Schema、永久 4xx 和响应
超限不重试。最大尝试次数不超过 5，退避和 `Retry-After` 有上限。

测试提供 deterministic fake client，可返回合法 JSON、malformed JSON、截断/超限响应、
retryable error、permanent error 和悬空 evidence ID。所有验收测试只用 fake client。

### 3.5 Analyzer 与最小协议检查

新增 `analysis/analyzer.py`。单篇流程：

1. 从 `DocumentGraph` 构建 EvidencePacket；
2. 生成版本化 request；
3. 检查缓存；
4. 未命中时调用 client；
5. 解析严格 `PaperAnalysis`；
6. 执行 Stage 3 最小协议检查；
7. 成功结果原子写缓存；
8. 返回 success/partial/failed 和安全 issue。

最小协议检查包括：

- 论文 ID、英文标题和链接必须等于输入元数据；
- 所有 evidence ID 必须属于当前 EvidencePacket；
- SupportingVisual 的 page/section/label/caption/bbox/image/source mapping 必须由所引用的
  `EvidenceCandidate` 复制，LLM 不能重写这些来源字段；
- 每个 Insight 至少引用一个非 Abstract-only evidence ID；
- 每个 Ablation 必须引用视觉 EvidenceCandidate；
- 单篇 SupportingVisual 不超过 3；
- inference/source-type 约束满足 Schema。

这些属于 API 协议和引用集合完整性，不验证“文字是否真的证明结论”、数值是否逐字出现、
caption 是否语义一致或 claim 是否可发布；这些仍由 Stage 4 处理。

### 3.6 缓存

新增 `analysis/cache.py`，默认根目录 `cache/analysis`。缓存键的规范 JSON 必须包含：

- PDF SHA-256；
- DocumentGraph schema/parser/parser_version/mapper/config 版本；
- DocumentGraph content fingerprint；
- EvidencePacket builder 版本与 packet fingerprint；
- prompt version；
- PaperAnalysis schema version；
- model identity；
- generation settings identity。

key 对规范 JSON 做 SHA-256。缓存只保存成功、重新验证通过的 `PaperAnalysis` 和非敏感版本
元数据；不单独保存 API key、Authorization header 或 Zotero 数据。写入使用同目录临时文件、
flush/fsync、重新读取验证和 `os.replace`。版本变化、损坏 JSON、Schema 失败、key 不一致或
partial/failed 结果全部视为 miss。

### 3.7 批处理管线与 dry-run

新增 `pipeline/analysis.py`。输入 `CandidateBatch` 和 `DocumentBatchResult`：

- 只按 `selected_for_full_analysis` 顺序处理；
- 最多处理 5 篇；
- 只接受 paper ID 对齐且状态为 success/partial、含 DocumentGraph 的 Stage 2 结果；
- Stage 2 failed/skipped、缺图或 ID 不匹配返回单篇结构化 issue；
- 单篇失败不阻断其他论文；
- 不接入旧 `Executor`、邮件或未来 daily pipeline。

提供完全离线 fixture CLI：只装载受控 JSON/项目内合成 DocumentGraph 和 fake response。
`--dry-run` 不读取 `LLM_API_KEY`，不构造真实 OpenAI client，不联网，不写成功缓存或运行输出，
但完成 EvidencePacket、prompt、Schema 和批处理边界验证。

生产依赖构造器只在明确的非 dry-run 路径从环境解析 `LLM_API_KEY`、`LLM_BASE_URL`、
`LLM_MODEL`。缺少配置时安全失败，错误只报告缺失的变量名，不显示值。

## 4. 配置

在 `config/base.yaml` 新增无秘密的 `analysis_pipeline`：

- `cache_root: cache/analysis`
- `prompt_version: stage3-v1`
- `schema_version: 1.0`
- `max_papers: 5`
- `max_visuals_per_paper: 3`
- `max_evidence_candidates`
- `max_evidence_chars`
- `max_block_chars`
- `response_max_bytes`
- connect/read/write/pool timeout；
- retry max attempts/backoff/max Retry-After；
- `config_version`。

密钥不进入 YAML。`.env.example` 已有空白 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，无需
加入真实值。

## 5. 错误与状态

至少支持：

- `document_not_available`
- `paper_document_mismatch`
- `evidence_packet_empty`
- `evidence_budget_exceeded`
- `analysis_timeout`
- `analysis_retry_exhausted`
- `analysis_auth_failed`
- `analysis_permanent_error`
- `analysis_response_too_large`
- `analysis_malformed_json`
- `analysis_schema_invalid`
- `analysis_unknown_evidence`
- `analysis_abstract_only_insight`
- `analysis_visual_provenance_mismatch`
- `analysis_cache_corrupt`

`success` 表示完整结构通过 Stage 3 协议；`partial` 表示可用分析包含明确缺失字段/非阻断 issue；
`failed` 表示无可用分析；`skipped` 表示没有对应可分析 DocumentGraph。错误消息不得包含 prompt、
response 正文、密钥、base URL query 或私人 Zotero 内容。

## 6. 测试策略

严格 TDD：

1. Schema 字段、unknown-field、None、序列化顺序、source/inferred 不变量；
2. EvidencePacket 章节优先级、稳定顺序、Abstract 标记、预算、3 个视觉上限和完整来源投影；
3. prompt version、禁止编造指令、Schema 注入和隐私最小化；
4. fake client 的合法、malformed、timeout、retry、永久错误和响应上限；
5. 缓存键全要素、命中、版本失效、损坏恢复、路径逃逸与原子写；
6. analyzer 的未知 evidence、Abstract-only Insight、视觉来源篡改、参数/消融缺失和每篇隔离；
7. pipeline 的最多 5 篇、顺序、ID 对齐、Stage 2 failed/partial 和不触发旧 Executor；
8. 完全离线 dry-run，断言真实 client 构造、网络、凭据读取和缓存写入次数均为 0；
9. Stage 2、Stage 1、默认和完整含 slow 回归。

测试不提交真实 PDF、真实论文内容、个人 Zotero 数据、LLM 输出缓存或任何密钥。所有分析样例
为人工合成的短 DocumentGraph 和人工 fake response。

## 7. 安全与成本

- 本阶段不使用用户在聊天中提供的任何凭据；已暴露凭据应在服务端撤销并轮换。
- unit/integration/dry-run 不允许真实 LLM 或收费请求。
- production client 构造与领域逻辑分离，测试通过依赖注入证明零网络。
- prompt/response 可能含论文文本，只能进入忽略缓存，不进入日志、异常、Git 或测试快照。
- 每批最大调用 5 次；相同完整 cache identity 不重复调用。
- 记录调用次数、cache hit 和安全状态，不记录 prompt/response 或 key。

## 8. 明确非目标

- Stage 4 最终 evidence validator、claim 发布/拒绝和纠错重试；
- 对实验数值进行全文逐字核验；
- VLM 图像理解、图表 OCR 或图中数值重建；
- LLM reranking；
- 网页、飞书、邮件、反馈、GitHub Actions；
- 真实 Zotero、真实 LLM、真实邮件或收费服务验收；
- 修改旧 `Paper.generate_tldr`、旧 Executor 或邮件接口；
- 每批分析超过 5 篇、每篇视觉证据超过 3 个。

## 9. 验收标准

- 输入分析内容只来自 Stage 2 DocumentGraph；
- 至多处理 5 篇，每篇最多 3 个视觉证据；
- 中文分析包含全部确定字段，缺失内容为 `None`/空 tuple；
- 每个 Insight 有非 Abstract-only evidence candidate ID；
- 每个 Ablation 有视觉 evidence candidate ID；
- Figure/Table 投影完整保留 Stage 2 page、section、label、caption、bbox、image 和 source mapping；
- source type 和 inferred 关系由 Schema 强制；
- LLM client 可注入，timeout/retry/大小限制和安全错误均有测试；
- 缓存键包含所有要求版本，命中时零重复调用；
- fake-LLM dry-run 完全离线、零凭据、零费用；
- Stage 3 专属、Stage 2、Stage 1、默认和完整含 slow 测试真实运行并如实报告；
- 不提交秘密、PDF、Zotero 数据、cache、响应或大文件；
- 独立审查确认的 Critical/Important 全部修复；
- 更新路线图和基线，创建独立提交，不创建 PR、不推送 upstream。
