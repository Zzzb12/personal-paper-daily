# Stage 6 飞书投递设计

## 决策与范围

本阶段实现飞书消息的离线渲染、可注入 HTTP 客户端和显式发送 CLI。
采用“预览优先”方案：默认只从人工 Stage 4 fixture 生成 JSON 预览；只有
`--send`、`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_CHAT_ID` 同时存在时才
允许访问飞书。不会实现 Stage 7 自动化、Stage 8 本地反馈、浏览器状态或真实论文
运行。未向飞书发送任何消息。

不使用 Hermes 的代码、模板、数据、图片或缓存：该目录没有可核验许可证文本，
本阶段只依据项目产品规范与飞书官方 API 文档重新实现。

## 输入与发布策略

```text
ValidationBatchResult + HTTPS site URL
  -> DigestPolicy（仅 valid + eligible，最多 5 篇）
  -> FeishuRenderer（纯函数、中文 interactive card）
  -> DeliveryRequest（稳定 digest_id / idempotency key）
  -> FeishuClient（token -> send；仅 --send）
  -> DeliveryReceipt（无密钥、无正文）
```

`partial`、`invalid`、`failed`、`skipped` 或 blocked 结果不进入详细消息；渲染器
只显示受验证分析中的标题、推荐理由、一个 Insight、最强图表的页码/标签、一个实验
结论和安全 HTTPS 链接。缺失字段统一显示“论文未明确提供”，不生成新结论。

## 模块与安全边界

- `delivery/schemas.py`：冻结、`extra=forbid` 的卡片、请求、收据和错误分类模型。
- `delivery/feishu.py`：纯 `FeishuRenderer`、`DigestPolicy`、环境解析器和注入式
  `FeishuClient`；日志和异常只含受控错误码。
- `pipeline/feishu.py`：fixture-only preview CLI；`--send` 是唯一网络入口，默认关闭。

客户端使用飞书的 `tenant_access_token` 与 `POST /open-apis/im/v1/messages`
（`receive_id_type=chat_id`）。所有 HTTP 调用有连接/读取超时、有限重试和
429/5xx 退避；认证和其他 4xx 不重试。每个 digest 的幂等键来自稳定内容哈希；同一
进程内重复键不重复发送。密钥只读环境，绝不进入 payload、JSON 预览、receipt、
日志或异常。

## 测试与验收

先写 RED 测试，覆盖：最多五篇、只允许 eligible valid、固定叙事字段与缺失降级、
HTML/Markdown 安全、HTTPS 链接、环境缺失、token/send 成功、401/429/5xx/超时、
幂等、默认 preview 零网络、`--send` 显式门控。所有 HTTP 测试使用注入 fake，
不读取真实 `.env`，不产生付费模型或飞书调用。

验收包括 focused Stage 6、Stage 4 回归、默认 pytest、编译、fixture preview、
secret/private-data/large-file 扫描、独立审查。已知 Windows multiprocessing 与慢测
Hugging Face 失败只如实报告。

## 回滚

停止调用 `pipeline.feishu` 或移除 `--send`，删除忽略的预览输出并回退 Stage 6
提交；Stage 4 输出、Stage 5 站点和本地 `.env` 不受影响。若未来真实发送配置有误，
在飞书控制台撤销凭据，不改写 Git 历史。
