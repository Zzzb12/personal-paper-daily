# Stage 7 GitHub Actions Daily Automation Design

## 决策与范围

Stage 7 只增加安全的日常编排、运行清单、缓存/恢复边界和 GitHub Actions，
不改变 Stage 1–6 的推荐、解析、分析、验证、渲染或飞书协议。采用“薄编排器”方案：
`pipeline/daily.py` 依赖六个可注入 runner，并在生产组合函数中适配现有 Stage 1–6
接口。Stage 4 仍是唯一 publication eligibility 边界；任何恢复的旧缓存都必须重新
经过 Stage 4，不能直接进入 viewer 或 Feishu。

默认执行模式是 `dry-run` 且 `no-send`。离线验收必须显式提供受控 fixture，并且
不会构造 Zotero、arXiv、PDF、LLM 或 Feishu 网络客户端。生产模式只能由完整的
`--mode live` 启用；真实飞书还需要不支持缩写的精确 `--send-feishu`、完整环境变量
和持久幂等账本共同放行。Stage 7 不执行真实外部调用。

## 组件与数据流

```text
trigger/config -> DailyRunContext
  -> Stage 1 candidate runner
  -> Stage 2 document runner（逐论文隔离）
  -> Stage 3 analysis runner（逐论文隔离）
  -> Stage 4 validation runner（唯一发布门）
  -> Stage 5 viewer runner ----> artifact audit/hash
  -> Stage 6 delivery runner --> persistent idempotency ledger
  -> atomic RunManifest
```

- `pipeline/daily_schemas.py` 定义冻结、`extra=forbid` 的 `RunManifest`、阶段状态、
  计数、站点结果、飞书结果、受控错误码和版本化 cache identity。
- `pipeline/cache.py` 负责完整身份 cache key、大小/期限/身份校验和原子 JSON 写入。
- `pipeline/artifacts.py` 负责受限输出路径、manifest 原子写入、viewer artifact 审计
  与内容哈希；拒绝路径穿越、符号链接、UNC/外部 Windows drive、私有/缓存文件。
- `pipeline/daily.py` 只做依赖编排、状态归并、计数和 CLI 组合；网络、clock、sleep、
  runner、ledger、manifest store 和 artifact auditor 都可替换。
- `delivery/ledger.py` 只持久化 idempotency key 与无敏感信息的收据摘要。同一 key
  在跨进程/跨 workflow 恢复后仍不会重复发送。

阶段级异常只转换成固定错误码；不保存异常文本。已有 Stage 2–4 的逐论文结果继续
保留成功项，整体状态按 `success / empty / partial / failed` 归并。viewer 与 Feishu
永远分别记录：站点成功后飞书失败不会删除站点；站点失败时飞书默认跳过，不会被
标成成功。

## RunManifest 合同

`RunManifest` 使用 `schema_version=1.0` 和 `pipeline_version=stage7-v1`，至少包含：

- `run_id`、`trigger`（scheduled/manual/local）、`config_hash`、dry-run/send 请求；
- 每阶段状态、输入/输出数量、cache hits、retry count、partial failure count、受控
  error codes；
- retrieved/candidate/selected/analyzed/validated/published/delivered 汇总计数；
- 静态站状态、发布数量、审核文件数/字节数和 artifact SHA-256；
- 飞书 preview/sent/duplicate/failed/skipped 状态、幂等键和安全错误码；
- 总 cache hits、retries、partial failures、最终状态与完成时间。

清单模型不提供 prompt、全文、Zotero 字段、密钥、URL 响应体或自由格式异常字段。
序列化后再执行敏感字段名与凭据 sentinel 审计，写入采用同目录临时文件、
flush/fsync、`os.replace`，失败时清理临时文件。

## 缓存与恢复

版本化 cache identity 同时绑定 pipeline、配置、embedding model、parser、mapper、
prompt、analysis schema、validator、viewer renderer/template 和 delivery renderer
版本。key 是固定前缀与规范 JSON SHA-256，不含密钥、prompt 或私有文本。

cache envelope 读取时检查最大字节数、JSON/schema、cache version、创建时间/TTL、
完整 identity、payload hash；损坏、过大、过期或任一身份不符都视为 miss。写入与
manifest 使用相同原子协议。GitHub Actions 只允许缓存 `cache/embeddings`、
`cache/documents` 和 `cache/workflow`；明确排除 `.env`、Zotero 原始数据、
`cache/analysis`、`cache/validation`、viewer/feedback 和 outputs。因分析/验证结果不从
workflow cache 恢复，且编排固定重跑 Stage 4，旧缓存不能绕过发布资格。

## CLI 与 GitHub Actions

CLI 使用 `argparse(allow_abbrev=False)`：

- `--trigger {scheduled,manual,local}`，默认 local；
- `--mode {dry-run,live}`，默认 dry-run；
- `--offline-fixture` 仅 dry-run 使用，dry-run 缺失 fixture 直接安全失败；
- `--send-feishu` 仅 live 允许，且是唯一真实发送入口；
- `--config-dir`、`--run-root`、`--viewer-output`、`--manifest-output` 均经过路径边界；
- `--run-id` 可供 GitHub run identity 注入，未提供时由注入 clock 生成。

`.github/workflows/personal-paper-daily.yml` 的 schedule 与 workflow_dispatch 进入同一
job、同一 `python -m zotero_arxiv_daily.pipeline.daily`。默认走 fixture dry-run/no-send。
live schedule 需要 GitHub Variable `PAPER_DAILY_SCHEDULE_LIVE` 精确等于
`I_UNDERSTAND_LIVE_NETWORK`；schedule send 还需要
`PAPER_DAILY_SCHEDULE_SEND=I_UNDERSTAND_FEISHU_SEND`。manual live/send 同时需要显式
dispatch boolean 和对应确认 Variable。缺省或拼写不完全匹配都不会联网/发送。

工作流设置最小权限、branch concurrency、job/step timeout，所有外部 action 固定到
完整 commit SHA，checkout 使用 `persist-credentials: false`。build job 只缓存白名单
目录；上传前要求 daily CLI 已完成 artifact 审计。Pages deployment 是独立 job，仅在
`PAPER_DAILY_ENABLE_PAGES_DEPLOY=I_UNDERSTAND_PUBLIC_ARTIFACT` 时运行，权限只在该 job
提升为 `pages: write`/`id-token: write`；工作流不会改变 repository visibility 或 push。
旧的 secret-bearing/写仓库 workflow 被移除，CI 保留原测试覆盖并补充 Stage 7 静态
安全测试。

## 配置、安全与错误处理

GitHub Secrets：`ZOTERO_ID`、`ZOTERO_KEY`、`LLM_API_KEY`、`FEISHU_APP_ID`、
`FEISHU_APP_SECRET`、`FEISHU_CHAT_ID`。GitHub Variables：`LLM_BASE_URL`、
`LLM_MODEL`、`PAPER_DAILY_SITE_URL` 和三个精确确认开关。`.env.example` 只包含名称、
空占位和注释。

缺少配置只报告变量名。日志只输出 run ID、状态、计数、hash 和固定错误码。禁止
`printenv`、`set -x`、verbose 环境转储、生成 secret-bearing config 或打印动态异常。

## 测试与回滚

测试先 RED 后 GREEN，覆盖成功、空、逐论文 partial、Stage 4 阻止、viewer/Feishu
独立失败、dry-run 零调用、环境名安全、manifest 内容安全、跨运行幂等、cache miss、
原子写、路径边界、workflow 安全和 artifact 排除。验收按用户指定执行 focused、
Stage 6/5/4 回归、默认/完整套件、compileall、YAML 静态检查、diff/hygiene 扫描、
fixture CLI 和 artifact 审计，并区分既有 Windows/Hugging Face 失败。

回滚时禁用或回退 Stage 7 workflow/daily CLI，保留 Stage 1–6 数据与最后一个已审核
站点。删除忽略的 Stage 7 outputs/cache/workflow 状态不会影响 Stage 6；若曾配置外部
凭据，应在 GitHub/供应商侧轮换，不改写 Git 历史。
