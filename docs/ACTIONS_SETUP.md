# GitHub Actions configuration

Stage 7 的 workflow 默认执行人工 fixture 的 `dry-run`，不会读取 Zotero、调用 LLM、
下载论文或发送飞书。配置值只能在仓库 **Settings → Secrets and variables → Actions**
中维护；不要生成或提交 `config/custom.yaml`，也不要把值粘贴到 workflow、日志、issue
或聊天中。

## GitHub Secrets

下列值按凭据或私人目标处理，配置在 **GitHub Secrets**：

| 名称 | 用途 |
|---|---|
| `ZOTERO_ID` | Zotero 私人文库标识 |
| `ZOTERO_KEY` | Zotero 只读 API 凭据 |
| `LLM_API_KEY` | OpenAI-compatible LLM 凭据 |
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

本地 `.env` 使用相同九个名称，示例只见 `.env.example` 的空占位。即使 endpoint 或
model 通常不是密钥，也不得在运行时打印完整环境或生成配置文件。

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
Docling artifacts；workflow 仅在 live 门精确开启后通过锁定的 Docling 工具版本下载
`layout`/`tableformer` 到 `models/docling`，随后 production factory 在构造 Zotero/arXiv
客户端前执行本地预检。模型准备失败会阻止 live run 和 Pages 更新。

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
