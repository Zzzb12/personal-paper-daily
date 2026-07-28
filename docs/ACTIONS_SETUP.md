# GitHub Actions configuration

Stage 7 的 workflow 默认执行人工 fixture 的 `dry-run`，不会读取 Zotero、调用 LLM、
下载论文或发送飞书。配置值只能在仓库 **Settings → Secrets and variables → Actions**
中维护；不要把真实值写入已跟踪的 `config/custom.yaml`（该文件只保留环境变量引用和
公开默认值），也不要把值粘贴到 workflow、日志、issue 或聊天中。

## GitHub Secrets

下列值按凭据或私人目标处理，配置在 **GitHub Secrets**：

| 名称 | 用途 |
|---|---|
| `ZOTERO_ID` | Zotero 私人文库标识 |
| `ZOTERO_KEY` | Zotero 只读 API 凭据 |
| `LLM_API_KEY` | OpenAI-compatible LLM 凭据 |
| `HF_TOKEN` | Hugging Face read token；仅用于下载固定 revision 的公开模型 |
| `FEISHU_APP_ID` | 飞书应用标识 |
| `FEISHU_APP_SECRET` | 飞书应用凭据 |
| `FEISHU_CHAT_ID` | 私人飞书目标会话 |

## GitHub Variables

下列非密钥配置放在 **GitHub Variables**：

| 名称 | 用途 |
|---|---|
| `LLM_BASE_URL` | OpenAI-compatible HTTPS endpoint |
| `LLM_MODEL` | 完整模型名称；参与 workflow cache identity |
| `PAPER_DAILY_SITE_URL` | 已审核静态站的绝对 HTTPS URL |

本地 `.env` 可使用相同十个名称，示例只见 `.env.example` 的空占位；本地已有完整
模型缓存时可不设置 `HF_TOKEN`。即使 endpoint 或 model 通常不是密钥，也不得在
运行时打印完整环境或生成配置文件。

## 精确启用门

所有门默认缺失，因此 schedule/manual 都保持 dry-run/no-send：

- scheduled live：`PAPER_DAILY_SCHEDULE_LIVE=I_UNDERSTAND_LIVE_NETWORK`
- scheduled Feishu：在 live 基础上再设置
  `PAPER_DAILY_SCHEDULE_SEND=I_UNDERSTAND_FEISHU_SEND`
- manual live：dispatch 勾选 `live_run`，并设置
  `PAPER_DAILY_MANUAL_LIVE=I_UNDERSTAND_LIVE_NETWORK`
- manual Feishu：在 manual live 基础上勾选 `send_feishu`，并设置
  `PAPER_DAILY_MANUAL_SEND=I_UNDERSTAND_FEISHU_SEND`
- Pages：`PAPER_DAILY_ENABLE_PAGES_DEPLOY=I_UNDERSTAND_PUBLIC_ARTIFACT`

字符串必须精确匹配。Pages 门只允许部署 daily CLI 已审核并哈希的
`outputs/daily/viewer`，不会改变 repository visibility。启用 live 前还必须准备本地
Docling artifacts 和 embedding reranker。workflow 仅在 live 门精确开启后下载
`layout`/`tableformer` 到 `models/docling`，并通过无凭据 model preflight 下载、加载和
验证 `config/base.yaml` 中固定 commit revision 的
`sentence-transformers/multi-qa-MiniLM-L6-cos-v1` reranker。该模型禁用
`trust_remote_code`，公开模型文件只缓存到 `models/reranker`；revision、任务标识、
512-token 上限、均值池化版本、编码参数和 remote-code 策略同时进入 embedding
cache identity。生产路径用 `transformers` 的纯文本 tokenizer/model 实现该模型声明的
attention-mask mean pooling 和 L2 normalization，不执行无关的 SentenceTransformers
视觉模块导入。`torch==2.11.0` 与 `torchvision==0.26.0` 均固定到 PyTorch CPU index；
preflight 会实际验证 CPU runtime 和 `torchvision.ops`，以便在 Docling 或 reranker
运行前发现 wheel 失配。任一模型准备失败都会在 Zotero、LLM 和 Pages 边界之前阻止
live run；CPU/vision 失配只报告固定错误码 `cpu_vision_runtime_failed`。GitHub-hosted
Runner 的共享出口容易触发 Hugging Face 未认证限流，因此 live preflight 明确要求
read-only `HF_TOKEN`；缺失时只报告变量名，不打印值。

manual live/send 只允许从 repository default branch 触发。workflow 使用全局
concurrency 且不取消进行中的 run，避免发送中断。飞书 ledger 只包含 SHA-256
idempotency keys：每个 Actions run 使用唯一 cache key，并通过稳定 restore prefix
恢复上一份 ledger 后保存新快照；lock 文件、消息正文和凭据不进入 cache。若 GitHub
平台清理了该 cache，应先保持 send 门关闭并执行 no-send 检查，不要把空 ledger 当作
已有投递历史。

## 手动验收与恢复

首先运行默认 workflow_dispatch，不勾选 live/send，确认 run manifest、viewer
artifact 和 Pages artifact 都不含 `.env`、cache、Zotero 数据或 feedback。之后如需
受控 live run，先只启用 live、不启用 send；检查 Stage 4 eligibility 和站点，再单独
启用飞书。manual live/send 必须选择 default branch；其他分支会保持 dry-run。

回滚时删除/禁用精确确认 Variable 或禁用 schedule。保留上一个已审核 Pages artifact
和 Stage 1–6 数据；不要通过 Git 提交“撤销”密钥。若凭据曾在聊天或日志出现，请在
Zotero、LLM 或飞书侧轮换。
