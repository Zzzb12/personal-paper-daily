# Stage 8 Reader Feedback Design

## 决策与范围

Stage 8 为已通过 Stage 4 publication eligibility 的静态阅读页增加私有的
`read`、`favorite`、`irrelevant` 显式反馈，并将经过校验的最小反馈投影接回
Stage 1 排名。采用“浏览器本地状态 + 显式导出/导入桥”方案：静态站在当前浏览器
即时保存状态，用户主动导出版本化反馈包，本地 CLI 校验后写入 Git 忽略的权威私有
store，后续本地 daily run 再读取该 store。GitHub Pages 不直接写回本地文件，也不
引入云数据库、账户体系或跨设备自动同步。

Stage 8 不采集隐式行为，不根据阅读时长或点击推断兴趣，不修改证据、分析或 Stage 4
发布资格，不自动删除 Zotero 条目，不把反馈状态写入公开 viewer artifact、GitHub
Actions cache、RunManifest 或日志。CI/scheduled run 没有显式挂载私有 store 时按
“无反馈”运行；这是一条有意保留的隐私边界。

## 方案选择

评估过三种同步方式：

1. 浏览器 `localStorage` + 显式导出/CLI 导入：无后台服务、最小权限、适配 Pages，
   但同步需要用户动作；采用此方案。
2. 本地 HTTP companion service：可自动写入文件，但 Pages 页面无法可靠访问用户
   本机服务，并扩大 CSRF、CORS、绑定地址和文件写入攻击面；本阶段不采用。
3. 私有云端反馈服务：支持多设备同步，但新增身份、凭据、后端和数据保留策略，违反
   Stage 8 无云数据库边界；不采用。

## 组件与数据流

```text
eligible viewer model
  -> static HTML + same-origin feedback.js
  -> browser localStorage (private browser state)
  -> explicit feedback-v1.json download
  -> feedback CLI strict validation / deterministic merge
  -> ignored private feedback store (authoritative local state)
  -> InterestFeedbackProjection
  -> Stage 1 candidate ranking / exact irrelevant exclusion
  -> Stage 2–7 unchanged
```

- `viewer/feedback.py` 定义严格 schema、状态机、导入导出包、私有 store、迁移和原子
  写入边界。
- viewer renderer 只为 eligible paper 输出规范化 paper ID 和无状态控制；第一方外部
  `feedback.js` 负责浏览器 adapter、筛选、键盘交互和备份导入导出。
- `candidates/feedback.py`（或同职责 adapter）只把权威 store 映射为
  `InterestFeedbackProjection`，不读取浏览器目录或公开 artifact。
- daily production composition 可注入反馈 projection；store 缺失时使用空 projection，
  损坏或不安全的 store fail closed 并产生固定安全错误码，不泄露内容或路径细节。

公开构建从来不复制 store 或浏览器导出文件。viewer artifact 可以包含实现交互的
HTML/CSS/JS，但不能包含任何用户状态、反馈 bundle、反馈缓存或浏览器状态快照。

## 版本化数据合同

### 稳定论文 ID

反馈键使用现有 arXiv 标识规范化规则，并将版本化 ID（如 `2401.01234v2`）折叠为
稳定 ID（`2401.01234`）。只接受严格的现代/旧式 arXiv ID 语法和受控长度；拒绝 URL、
控制字符、路径分隔符、`..`、drive/UNC 表达及任意自由文本。规范化逻辑由一个共享
函数提供，浏览器和 Python 使用同一组 fixture 验证。

### `FeedbackCommand`

命令表达期望状态而不是“翻转”：

- `schema_version`：固定命令 schema 版本；
- `command_id`：不可变 UUID，用于幂等去重；
- `paper_id`：规范化稳定 ID；
- `action`：`set_read`、`set_favorite`、`set_irrelevant`；
- `value`：严格 boolean；
- `occurred_at`：UTC 时间，只用于确定性排序和审计，不作为可信安全边界；
- `device_id`、`sequence`：浏览器本地随机设备 ID 与单调序号，用于同设备稳定顺序。

同一个 `command_id` 重放必须无副作用。不同设备或相同时间的命令按
`(occurred_at, device_id, sequence, command_id)` 建立确定性全序；Stage 8 不承诺自动
多设备合并，显式导入时会报告受控冲突计数而不打印内容。

### `FeedbackRecord`

每个稳定 paper ID 至多一条冻结、`extra=forbid` 的记录，包含三个 boolean 状态、
`read` 域排序水位、`favorite/irrelevant` 互斥偏好域排序水位和 record schema version。
只有排序键严格晚于对应水位的命令才能改变状态；相同排序键是 no-op，较早命令被计为
受控 stale command。这样相同 bundle 集合无论以何种导入顺序到达，都得到相同状态。
状态转换固定为：

- `set_irrelevant(true)` 同时令 `favorite=false`；
- `set_favorite(true)` 同时令 `irrelevant=false`；
- `read` 与另外两个状态独立共存；
- 设置为当前值会推进对应水位但不改变 boolean，并仍被视为成功的幂等 no-op；
- 清除一个状态不会隐式开启另一个状态。

### 导出包与私有 store

浏览器导出包包含 bundle schema version、唯一 bundle ID、生成时间、按稳定顺序排列的
命令和规范 payload 的 SHA-256。它不包含标题、摘要、全文、prompt、Zotero 字段、页面
URL、凭据或自由格式异常。CLI 在完整校验摘要、大小、条目数量、版本和全部命令后才
合并；任一失败都不产生部分写入。

权威 store 额外保存已应用 bundle/command ID 的有界去重账本和 store schema version。
默认位置位于仓库内明确 Git 忽略的私有反馈目录，也可通过显式配置指向另一个经过
边界校验的本地目录。迁移只支持列出的旧 schema；未知新版本拒绝读取，旧版本迁移先
写新文件再原子替换。

## Viewer 交互与内容安全

Stage 5 的 CSP 从 `script-src 'none'` 调整为只允许 `script-src 'self'`，继续禁止内联
脚本、远程脚本、`eval` 和第三方资源。脚本使用静态 DOM API 和 `textContent`，不把
反馈或论文文本拼接进 `innerHTML`。每张 eligible paper 卡片提供：

- “已读/未读”、“收藏/取消收藏”、“不相关/恢复”三个可访问按钮；
- 明确的 `aria-pressed`、焦点样式和状态文本；
- 在卡片获得焦点且用户不处于输入控件时生效的 `r`、`f`、`i` 快捷键；
- 未读、仅收藏和“显示不相关”筛选；不相关论文默认隐藏。

页面提供“导出反馈”和“导入浏览器备份”。浏览器导入同样严格校验版本、大小、摘要、
ID 和命令，再以事务式内存合并后一次写回 `localStorage`；失败保持原状态。存储不可用、
配额不足或数据损坏时显示固定、非敏感错误，并允许重新导出/清空当前站点命名空间，
不会清除其他 origin 的数据。

## 排名闭环与发布边界

`InterestFeedbackProjection` 仅包含规范化 ID 集合和受控权重，不携带浏览器元数据或
自由文本：

- `irrelevant_ids`：在任何付费/网络分析前从候选中精确排除；相同 arXiv 论文的新版本
  因稳定 ID 规范化同样被排除，因此不能进入分析、Stage 4、viewer 或 Feishu；
- `favorite_ids`：仅对相同稳定论文的重现/新版本增加一个可配置的小幅加分；
- `read_ids`：只支持阅读页筛选，不影响排名。

默认 favorite delta 为 `+0.05`，配置必须落在 `[0.0, 0.10]`，应用后总 ranking score
仍按现有范围 clamp。irrelevant 是精确 ID veto，而不是负面主题 embedding。Stage 8
不从少量 favorite/irrelevant 自动扩展关键词或训练语义偏好，从而限制过滤气泡与错误
泛化；主题级学习留待有独立设计和可解释性测试的后续阶段。

Stage 4 继续是公开资格的唯一证据门。反馈只能更早排除论文，不能把无资格论文提升为
可发布，也不能改写 validation result。恢复缓存或旧 viewer artifact 不能绕过当前
projection 和 Stage 4。

## 文件系统、隐私与错误处理

私有 store 和 CLI 导入遵守以下边界：

- 输入/输出最大字节数、最大命令数和最大记录数均有保守上限；
- 拒绝符号链接文件、符号链接父目录、路径穿越、仓库边界逃逸、Windows drive/UNC
  越界和非普通文件；
- 写入使用目标同目录临时文件、flush、`fsync`、`os.replace` 和失败清理，并在支持时
  同步父目录；
- 读取损坏、过大、未知版本或摘要不匹配的文件时保留原文件并 fail closed；
- 日志/manifest 只记录固定错误码、导入/冲突计数和非内容摘要，不记录动态异常原文、
  paper ID 列表或反馈状态；
- `.gitignore`、workflow cache 配置和 artifact auditor 明确排除私有反馈目录、导出包、
  localStorage 测试快照及迁移备份。

导入 CLI 使用 `argparse(allow_abbrev=False)`，不会自动扫描 Downloads 或用户目录；必须
显式给出 bundle 和 store 路径。dry-run 只验证并报告受控计数，不写文件。

## Hermes 授权与复用边界

项目所有者已明确确认其朋友制作的 Hermes 参考实现可供本项目授权复用，因此缺少当前
参考目录中的 `LICENSE/COPYING/NOTICE` 文件不构成 Stage 8 阻塞。实现可以参考并适配其
`localStorage` 收藏交互和本地持久化行为，但不会虚构许可证名称、条款或新增伪造的
许可证文件。任何复用代码仍必须经过本项目的严格 schema、CSP、路径、原子写入、隐私
和测试要求；文档只记录“依据项目所有者确认获得授权”。

## 测试、验收与回滚

实现严格按 RED → GREEN → REFACTOR：

- schema/状态机：三种状态、冲突规则、幂等命令、非法 ID、确定性合并和版本迁移；
- store：缺失/损坏/过大/摘要不匹配、原子失败清理、符号链接、路径穿越、UNC/drive；
- 浏览器 adapter：localStorage 缺失/损坏/配额错误、按钮、筛选、快捷键、刷新持久化、
  备份导入导出与 CSP；
- 排名：favorite delta 上限、read 零影响、irrelevant 在付费边界前阻断及 Stage 4 不可
  绕过；
- 隐私：viewer artifact、workflow cache、manifest、日志和 Git tracked files 中没有
  反馈状态、导出包、`.env`、Zotero 私有数据或浏览器快照；
- 回归：Stage 7 focused、Stage 6/5/4 相关套件、默认与 slow/non-slow 完整套件、
  compileall、workflow 静态安全、fixture dry-run、artifact audit、diff/hygiene 扫描和
  独立整分支审查。

回滚时移除/禁用反馈 JS、CLI import 和 Stage 1 projection adapter，恢复 Stage 5 的
无脚本 CSP；Stage 1–7 原流水线仍可按空 projection 运行。删除浏览器 origin 下本项目
命名空间或 Git 忽略的私有 store 会丢失用户反馈，因此回滚前应先显式导出备份；不会
改写 Git 历史、Zotero 数据或最后一个审核后的静态站。

已知限制是 Pages 与本地 store 不会自动同步、浏览器清理站点数据会删除未导出的反馈、
跨设备合并只提供确定性显式导入，以及 Stage 8 只做精确 ID 偏好而不学习主题语义。
