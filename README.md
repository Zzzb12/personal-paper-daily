# Personal Paper Daily

[![CI](https://github.com/Zzzb12/personal-paper-daily/actions/workflows/ci.yml/badge.svg)](https://github.com/Zzzb12/personal-paper-daily/actions/workflows/ci.yml)
[![Daily](https://github.com/Zzzb12/personal-paper-daily/actions/workflows/personal-paper-daily.yml/badge.svg)](https://github.com/Zzzb12/personal-paper-daily/actions/workflows/personal-paper-daily.yml)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/)
[![License: AGPLv3](https://img.shields.io/badge/License-AGPLv3-blue.svg)](LICENSE)

> A privacy-conscious daily research workflow that turns Zotero interests into
> ranked arXiv recommendations, evidence-bound Chinese paper readings, a static
> reader, and optional Feishu delivery.

## 项目简介

Personal Paper Daily 是一个面向个人研究者的每日论文工作流。它从指定的
Zotero collections 提取研究兴趣，检索并排序新的 arXiv 论文，只对最高优先级
候选执行 PDF 解析和 LLM 深度分析，再通过严格的证据校验决定哪些结果可以进入
静态阅读站和飞书消息。

这个仓库不是上游项目的原样镜像。它在原有 Zotero/arXiv 推荐基础上实现了完整的
Stage 1–9 流水线，包括结构化中文分析、Figure/Table 证据绑定、发布资格校验、
静态阅读器、飞书投递、GitHub Actions 自动化、反馈闭环以及质量/成本/运行时观测。

> [!IMPORTANT]
> 统一 CLI 默认是 `dry-run` 且不会发送飞书。真实 Zotero、论文下载和 LLM
> 调用必须显式选择 `--mode live`；真实飞书投递还必须额外提供精确开关
> `--send-feishu`。Live 模式可能产生网络流量和 LLM 费用。

## 核心能力

- **Zotero 兴趣建模**：只读取配置允许的 collections，支持 include/ignore glob。
- **每日候选排序**：检索 arXiv、去重、验证元数据，以本地 embedding 和研究兴趣排序。
- **有限深度分析**：默认候选池 30 篇、重排 15 篇、完整分析最多 5 篇。
- **PDF 与视觉证据**：使用 Docling 解析页面、章节、Figure、Table 和 caption。
- **证据约束中文阅读**：输出 Insight、Method、参数、消融、实验结论和局限，并绑定页码与证据。
- **发布资格校验**：证据不足或结构无效的论文不会进入 viewer 或飞书投递。
- **静态阅读器**：生成响应式 HTML 页面和经过审计的 viewer artifact。
- **飞书投递**：以企业自建应用向指定会话发送交互卡片，支持投递幂等。
- **反馈闭环**：本地记录已读、收藏和不相关反馈，不把浏览器状态上传到公开 artifact。
- **自动化与恢复**：schedule/manual/local 共用同一个版本化 daily CLI，缓存身份可验证、写入原子化。
- **可观测性**：RunManifest 记录各阶段状态、数量、缓存、重试、部分失败、成本和 artifact hash。

## 处理流程

```text
Zotero collections
        │
        ▼
兴趣语料与每日候选 ──► 排序与限额 ──► PDF 下载/Docling 解析
                                              │
                                              ▼
                                   结构化中文 LLM 分析
                                              │
                                              ▼
                                   Stage 4 证据与资格校验
                                      │               │
                                      ▼               ▼
                               静态 Viewer       飞书卡片（可选）
                                      │               │
                                      └──── RunManifest ┘
```

单篇论文失败会被隔离并记录为 partial result，不会丢弃整个批次。静态站构建和飞书
投递分别记录结果；飞书失败不会删除已经成功生成的静态站。

## 快速开始

### 环境要求

- Python `>=3.13`
- [uv](https://docs.astral.sh/uv/)
- Git
- Live 模式需要可访问的 Zotero、OpenAI-compatible LLM endpoint 和 Docling 模型

```powershell
git clone https://github.com/Zzzb12/personal-paper-daily.git
Set-Location personal-paper-daily
uv sync --frozen
```

### 离线 dry-run

下面的 fixture 命令不会访问网络、不会调用付费 LLM，也不会发送飞书：

```powershell
uv run python -m zotero_arxiv_daily.pipeline.daily `
    --trigger local `
    --mode dry-run `
    --offline-fixture tests/fixtures/evidence/stage4_golden.json
```

默认输出位于 `outputs/daily/`。需要为每次运行隔离输出时：

```powershell
$runId = 'fixture-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$runRoot = "outputs/daily/$runId"

uv run python -m zotero_arxiv_daily.pipeline.daily `
    --trigger local `
    --mode dry-run `
    --offline-fixture tests/fixtures/evidence/stage4_golden.json `
    --run-id $runId `
    --run-root $runRoot `
    --viewer-output viewer `
    --manifest-output run-manifest.json

Start-Process "$runRoot/viewer/index.html"
```

## 环境变量

复制示例文件后，只在本地编辑 `.env`：

```powershell
Copy-Item .env.example .env
Start-Process notepad.exe -ArgumentList (Resolve-Path '.env')
```

| 变量 | 用途 | GitHub 配置位置 |
|---|---|---|
| `ZOTERO_ID` | Zotero 数字 User ID | Secret |
| `ZOTERO_KEY` | Zotero 只读 API Key | Secret |
| `LLM_API_KEY` | LLM API Key | Secret |
| `LLM_BASE_URL` | OpenAI-compatible API base URL | Variable |
| `LLM_MODEL` | 模型名称 | Variable |
| `FEISHU_APP_ID` | 飞书企业自建应用 App ID | Secret |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | Secret |
| `FEISHU_CHAT_ID` | `oc_` 开头的目标会话 ID | Secret |
| `PAPER_DAILY_SITE_URL` | Viewer 的公开 HTTPS 基础地址 | Variable |

`.env` 不会被 CLI 自动读取。下面的本地 live 命令通过 `python-dotenv` 将它注入
子进程；不要使用会打印变量值的 `dotenv list`、`printenv` 或调试环境转储。

默认 Zotero collection 路径在 [`config/base.yaml`](config/base.yaml)：

```text
PaperDaily/00-Seeds/**
PaperDaily/03-Read/**
PaperDaily/04-Favorite/**
PaperDaily/99-Exclude/**   # 排除
```

请按自己的 Zotero collection 结构调整配置，但不要提交私人库内容或导出。

## 本地 Live 运行

### 1. 准备 Docling 模型

```powershell
uv run docling-tools models download layout tableformer `
    --output-dir models/docling `
    --quiet
```

`models/docling/` 是本地运行依赖，不应作为公开 artifact 提交。

### 2. 真实 Zotero + LLM，不发送飞书

建议第一次真实运行始终省略 `--send-feishu`：

```powershell
$runId = 'live-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$runRoot = "outputs/daily/$runId"

uv run python -m dotenv -f .env run -- `
    python -m zotero_arxiv_daily.pipeline.daily `
    --trigger local `
    --mode live `
    --run-id $runId `
    --run-root $runRoot `
    --viewer-output viewer `
    --manifest-output run-manifest.json
```

检查：

```powershell
Start-Process "$runRoot/viewer/index.html"
notepad "$runRoot/run-manifest.json"
```

Manifest 只保留安全错误代码和运行统计，不保存 API Key、prompt、论文全文、
Zotero 私有内容或动态异常原文。

### 3. 显式发送飞书

确认 viewer、Manifest、飞书应用权限和公开站点地址均正确后，在同一 live 命令中
额外加入且只加入：

```text
--send-feishu
```

真实发送必须同时满足：

- `--mode live`
- 精确的 `--send-feishu`
- 完整的飞书环境变量
- 论文通过 Stage 4 publication eligibility

投递账本保存在 `cache/workflow/delivery-ledger.json`，用于阻止同一幂等键重复发送。
不要为了重试而随意删除该文件。

## GitHub Actions

工作流文件：
[`personal-paper-daily.yml`](.github/workflows/personal-paper-daily.yml)

它支持：

- 每日 schedule（`22:00 UTC`）
- `workflow_dispatch`
- 最小权限、concurrency、job/step timeout
- 固定 commit SHA 的外部 Actions
- 默认 dry-run/no-send
- 审核后的 viewer artifact 与可选 GitHub Pages 部署

### Secrets

在 `Settings → Secrets and variables → Actions → Secrets` 配置：

```text
ZOTERO_ID
ZOTERO_KEY
LLM_API_KEY
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_CHAT_ID
```

### Variables

在 `Settings → Secrets and variables → Actions → Variables` 配置：

```text
LLM_BASE_URL
LLM_MODEL
PAPER_DAILY_SITE_URL
```

网络、发送和公开部署均需要精确 acknowledgement：

| Variable | 精确值 | 作用 |
|---|---|---|
| `PAPER_DAILY_MANUAL_LIVE` | `I_UNDERSTAND_LIVE_NETWORK` | 允许默认分支的手动 live |
| `PAPER_DAILY_MANUAL_SEND` | `I_UNDERSTAND_FEISHU_SEND` | 允许手动飞书发送 |
| `PAPER_DAILY_SCHEDULE_LIVE` | `I_UNDERSTAND_LIVE_NETWORK` | 允许 schedule live |
| `PAPER_DAILY_SCHEDULE_SEND` | `I_UNDERSTAND_FEISHU_SEND` | 允许 schedule 飞书发送 |
| `PAPER_DAILY_ENABLE_PAGES_DEPLOY` | `I_UNDERSTAND_PUBLIC_ARTIFACT` | 允许发布审核后的 Pages artifact |

手动运行路径：

```text
Actions → Personal Paper Daily → Run workflow
```

`live_run=true` 仅申请真实网络运行；`send_feishu=true` 仅在 live 和相应
acknowledgement 同时成立时发送。没有精确门禁值时，schedule 仍保持 dry-run/no-send。

如需 GitHub Pages：

1. 在 `Settings → Pages → Build and deployment` 选择 `GitHub Actions`。
2. 配置 `PAPER_DAILY_ENABLE_PAGES_DEPLOY`。
3. 成功部署后，将实际 Pages HTTPS 地址填入 `PAPER_DAILY_SITE_URL`。

GitHub Pages 网站是公开内容。流水线只上传通过 Stage 4 和 artifact audit 的 viewer
目录，不会自动把私人仓库改成公开，也不会向上游仓库写入。

## 输出与数据边界

典型本地输出：

```text
outputs/daily/<run-id>/
├── run-manifest.json
└── viewer/
    ├── index.html
    ├── papers/
    └── assets/
```

以下内容禁止提交或进入公开 viewer artifact：

- `.env` 和所有凭据
- Zotero 原始私有数据、notes 和 collection 导出
- 下载的论文 PDF
- Docling/embedding/LLM/run cache
- 浏览器反馈状态和投递账本
- 未通过 Stage 4 校验的公开输出
- 日志、临时文件和动态异常原文

缓存键覆盖模型、parser、mapper、prompt、schema、validator、renderer 和配置身份。
缓存损坏、过大、版本或身份不匹配时按 miss 处理；恢复缓存不会绕过 Stage 4。

## 测试与已知限制

```powershell
uv sync --frozen
uv run pytest -q
uv run pytest -q tests/pipeline tests/workflows tests/observability
uv run python -m compileall -q src
git diff --check
```

Windows 上两个一秒 `multiprocessing spawn` 硬超时测试是已记录的上游基线问题；
slow local reranker 在模型未缓存且 Hugging Face 不可达时也可能失败。这些测试没有被
删除、跳过或弱化。完整证据和各 Stage 验证结果见
[`docs/BASELINE.md`](docs/BASELINE.md)。

## 项目文档

- [产品规格](docs/PRODUCT_SPEC.md)
- [架构设计](docs/ARCHITECTURE.md)
- [实施路线与 Stage 状态](docs/IMPLEMENTATION_PLAN.md)
- [基线、测试结果与已知限制](docs/BASELINE.md)
- [代理协作与安全规则](AGENTS.md)

## 许可证与上游致谢

本项目以 [AGPLv3](LICENSE) 发布。

Personal Paper Daily 基于
[`TideDra/zotero-arxiv-daily`](https://github.com/TideDra/zotero-arxiv-daily)
的 Zotero 兴趣读取、论文源检索和推荐基础独立扩展。感谢上游作者及
[pyzotero](https://github.com/urschrei/pyzotero)、
[arxiv.py](https://github.com/lukasschwab/arxiv.py)、
[sentence-transformers](https://github.com/UKPLab/sentence-transformers)、
[Docling](https://github.com/docling-project/docling) 等开源项目。

该上游项目不负责维护本仓库；本仓库的问题与变更请在当前仓库处理。
