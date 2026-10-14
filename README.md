# nonebot-plugin-fanqie-verify

番茄读书群（QQ）入群验证插件：新成员入群后必须发送**自己发布的番茄小说书评详情页截图**，经 PaddleOCR（PP-OCRv6）云端 API 识别、校验「我」徽章、书名与作者后自动放行；验证失败则通知管理员决定通过或踢出。

> - 判定核心：检测到「**我**」徽章 + 提取到书名与作者（他人书评直接拒绝）
> - OCR 多模型并行识别按字段融合，通过后**视觉模型二次复核**（持否决权）
> - `/kick` 已于 2026-10-07 移除：手动移出成员请用 lingchu-bot 的「踢出」命令

## 功能

- **FR1 入群触发**：监听 OneBot V11 `group_increase` 事件，对新成员发送验证提示并开启超时计时
- **FR2 图片识别**：接收新成员发送的图片，调用 PaddleOCR 云端 API **多模型并行识别并按字段融合**（PaddleOCR-VL-1.6 / PP-OCRv6 / PP-StructureV3，置信度阈值可配）；OCR 失败时回退**视觉模型看图判定**（支持**群聊发送**或**私聊机器人发送**两种方式验证）
- **FR3 信息提取**：检测「我」徽章（书评为本人发布）、「书评详情」标题与五角星评分组件，提取读者名、发布日期、评分、阅读时长、书评正文、书名与作者
- **FR4 综合判断**：本人书评 + 书名/作者合理性校验 + 可配置的放行策略（按群配置作者白名单）；区分**书评 vs 短评/段评/章评/想法**（后者一律判无效）；OCR 通过后**视觉模型二次复核**（持否决权，确保高准确率）
- **FR5 通过放行**：发送欢迎消息
- **FR6 拒绝处理**：不执行禁言，转入"待管理员决策"并私信通知管理员决定通过或踢出
- **FR7/FR8 超时/重试**：超时未响应或连续识别失败达到上限，转入"待管理员决策"并通知管理员；响应超时后还会在群内 @ 成员提示已超时、可发送「重审」重试
- **私聊验证**：新成员可直接私聊机器人发送书评详情页截图完成验证；多群待验证时用「验证 群号」指定目标群
- **管理决策超时**：通知管理员后超过 16 小时（可配置）未处理，自动在群内通报并移出成员；被移出前最后 1 小时、5 分钟（可配置）会再次 @ 提醒该成员及时重审
- **FR9 管理员命令**：`/keep`、`/通过` 决定保留/直接批准（`/kick` 已于 2026-10-07 移除，移出请用 lingchu-bot 的「踢出」命令）；`待处理列表`、`处理中列表` 查询本群待决策/待提交成员；`查看白名单` 查看本群作者与作品配置
- **状态持久化**：验证会话（等待提交 / 待管理员决策）持久化到数据库，重启后自动恢复并重建超时，不会把验证中的成员误当作已通过
- **追踪 ID**：每次验证流程携带全局唯一 trace ID，贯穿日志、落库（会话/事件/审计三表）与管理员通知，可回溯完整判定链路
- **运行时重载**：`重载番茄OCR配置` 命令热更新放行策略
- **边界守卫**：监听退群/管理员变动/禁言事件，操作前查询群成员状态，避免对已退出成员执行无效动作
- **漏验补验**：机器人（LLBot）掉线期间入群的成员不会收到入群事件，`补验` 命令可按「入群时间在最近 N 小时内 + 无验证记录」扫描并补进验证流程，也支持直接指定成员；机器人重连时可配置为自动补验或仅提醒管理员
- **延期审核**：管理员可用 `延期` 命令推迟「待管理员决策」成员的自动移出时间（默认 6 小时、单次上限 48 小时，均可配置），用于暂时无法处理时保住成员；可指定成员（`延期 @成员 12`）或对本群全部待审成员生效（`延期`），不限制累计次数
- **引用回复**：群内各项回复（提交反馈、命令回复、验证通过欢迎、超时通报、移出前提醒）均**引用原消息**发送，便于成员对照上下文；超时类通报引用该成员最后提交的图片消息。私聊消息与入群引导（系统事件，无原消息）保持普通发送
- **群公告已读闸门**：`FANQIE_NOTICE_GATE_ENABLED`（默认开）开启后，已用「设为验证公告」绑定公告的群里，未确认阅读全部绑定公告的新成员判**不通过**；该群未绑定公告、或查询接口调用失败时**不拦**（降级放行并在流程事件里记明细）。需要 LLBot v8.3.0+（提供 `get_group_notice_acklist`）
- **验证公告绑定**：`获取群公告列表` 出图列出本群公告的 `notice_id`（QQ 客户端看不到 id），`设为验证公告` / `取消验证公告` / `查看验证公告` 管理绑定；绑定落在独立的 `fanqie_verification_notices.toml`，不写进放行策略文件

## 命令

| 命令 | 别名 | 说明 |
|---|---|---|
| `验证 <群号>` | `/验证`、`选择群`、`切换群` | 私聊机器人发送：多群待验证时指定在哪个群验证（恰有一个待验证群时直接发截图即可） |
| `keep <user_id>` | `保留`、`留`、`/keep` | 通过并保留成员（仅限处于验证流程中的成员） |
| `通过 <user_id>` | `直接通过`、`批准`、`放行`、`/通过`、`/approve` | **插入直接批准**：对新入群且仍在验证流程（等待截图 / 待管理员决策）的成员直接放行并欢迎，无需等待提交截图或超时 |
| `待处理列表` | `待处理`、`未处理列表` | 查询本群等待管理员决策的成员及剩余时间 |
| `处理中列表` | `处理中`、`验证中` | 查询本群正在等待截图提交（验证中）的成员 |
| `补验 [小时数] [@成员/QQ号]` | `补验证`、`同步入群`、`漏验扫描`、`/补验` | 把错过入群事件的成员补进验证流程：无参数按默认窗口扫描，可带小时数（如 `补验 48`）或指定成员；`FANQIE_BACKFILL_CONFIRM_FIRST=true` 时先列名单 |
| `补验确认` | `确认补验`、`/补验确认` | 执行 `补验` 列出的候选名单（仅 `CONFIRM_FIRST=true` 时用到） |
| `延期 [@成员/QQ号] [小时数]` | `推迟`、`延长`、`/延期`、`/extend` | 推迟「待管理员决策」成员的自动移出时间（新截止 = 当前时间 + 延期时长）：不带成员时作用于本群全部待审成员；不带时长时用 `FANQIE_EXTEND_DEFAULT_HOURS`（默认 6 小时），单次上限 `FANQIE_EXTEND_MAX_HOURS`（默认 48 小时，超出按上限处理） |
| `查看白名单` | `白名单`、`作者白名单`、`/白名单` | 展示当前群配置的作者与作品列表 |
| `重载番茄OCR配置` | `重载OCR配置`、`重载配置` | 热重载放行策略 TOML |
| `重审 [@成员]` | `重新审核`、`重新验证`、`/重审` | 普通成员重审**自己**（限 `FANQIE_REVIEW_MAX_TIMES` 次，默认 2）；管理员 `@成员` 可重审任意普通成员（不限次数）。重审复用完整验证流程：重置识别重试、重新发送引导、重排超时窗口 |
| `获取群公告列表` | `群公告列表`、`公告列表`、`查看群公告`、`/公告列表` | 出图列出本群公告的 `notice_id`（并标注哪条能当验证公告；QQ 客户端看不到 id） |
| `查看验证公告` | `验证公告`、`公告绑定`、`/验证公告` | 列出本群已绑定的验证公告 |
| `设为验证公告 <id...>` | `绑定公告`、`添加验证公告`、`/设为验证公告` | 绑定公告为验证公告（可多个；逐个校验是否带确认环节，不合格的跳过并汇报） |
| `取消验证公告 <id...\|全部>` | `解绑公告`、`移除验证公告`、`/取消验证公告` | 解绑验证公告 |

- `<user_id>` 也可用 `@成员` 代替。命令前缀由 `COMMAND_START` 决定（默认 `/`）。「重审」命令不受前缀限制（`重审` 与 `/重审` 均可用）。
- **权限**：除 `验证 <群号>`、`重审` 外，全部命令注册权限为 `SUPERUSER`。处理器内再按身份放行：`通过`、`待处理列表`、`处理中列表`、`查看白名单`、`重载番茄OCR配置` 仅限配置管理员（`FANQIE_ADMIN_IDS`）；`keep`、`延期` 在 `FANQIE_ALLOW_GROUP_ADMIN_COMMANDS=true`（默认）时群内管理员/群主亦可执行，否则仅配置管理员；`补验`/`补验确认` 配置管理员或群内管理员/群主（不受该开关限制）；全部公告类命令仅限超管。`重审` 所有群成员可重审自己，管理员可 `@成员` 重审他人。
- 私聊验证通道（`FANQIE_PRIVATE_VERIFY_ENABLED`，默认开）：直接私聊发书评截图即可验证；若恰有一个待验证群则直接验证，多群则先提示用 `验证 群号` 选择。

## 配置

### 环境变量

以下变量通过 `.env`（或 `.env.dev` / `.env.prod`）配置，环境变量名大小写不敏感。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `FANQIE_VERIFY_GROUPS` | `[]` | 需要验证的群号集合，`[]` 表示全部群（与策略群节点取交集） |
| `FANQIE_ADMIN_IDS` | `[]` | 管理员 QQ 号集合，可接收通知并执行 `/keep` |
| `FANQIE_WELCOME_MESSAGE` | 内置默认文案 | 入群提示消息 |
| `FANQIE_RESPONSE_TIMEOUT` | `600` | 等待新成员发送截图的超时（秒），超时按验证失败处理并通知管理员决策 |
| `FANQIE_MAX_ATTEMPTS` | `3` | 识别失败允许尝试次数，达上限按验证失败处理并通知管理员决策 |
| `FANQIE_ADMIN_DECISION_TIMEOUT` | `57600`（16 小时） | 通知管理员后等待其决策的超时（秒），超时群内通报并移出成员 |
| `FANQIE_REMIND_BEFORE_KICK` | `[3600, 300]` | 待管理员决策成员被移出前提前提醒的秒数列表（升序），如 `[3600, 300]` 表示最后 1 小时、5 分钟各提醒一次 |
| `FANQIE_REVIEW_MAX_TIMES` | `2` | 普通成员通过「重审」命令重新验证的最大次数 |
| `FANQIE_ALLOW_GROUP_ADMIN_COMMANDS` | `true` | 是否允许群内管理员（admin/群主）使用 `/keep` 等命令 |
| `FANQIE_PRIVATE_VERIFY_ENABLED` | `true` | 是否启用「私聊发图完成验证」通道 |
| `FANQIE_BACKFILL_ENABLED` | `true` | 是否启用「补验」（补进错过入群事件的成员） |
| `FANQIE_BACKFILL_DEFAULT_HOURS` | `24` | 补验默认扫描窗口（小时） |
| `FANQIE_BACKFILL_RECONNECT_MODE` | `notify` | 机器人重连时的补验行为：`notify` 仅提醒 / `auto` 自动补验 / `off` 不处理 |
| `FANQIE_BACKFILL_CONFIRM_FIRST` | `false` | 补验是否先列候选名单等确认 |
| `FANQIE_BACKFILL_MAX_BATCH` | `20` | 单次补验人数上限 |
| `FANQIE_EXTEND_ENABLED` | `true` | 是否启用「延期」命令 |
| `FANQIE_EXTEND_DEFAULT_HOURS` | `6` | 延期不带时长参数时的默认小时数 |
| `FANQIE_EXTEND_MAX_HOURS` | `48` | 单次延期的最大小时数（不限制累计次数） |
| `FANQIE_KICK_RETRY_TIMES` | `3` | 踢出失败后的重试轮数；用尽则记为「待补踢」，机器人重连时自动补偿 |
| `FANQIE_KICK_RETRY_DELAY` | `60` | 踢人重试间隔（秒） |
| `FANQIE_NOTIFY_CHANNEL` | `group` | 验证失败通知管理员的渠道：`group`（群内一条消息并 @ 全部管理员，可引用原消息）/ `private`（逐个私聊，失败回退群内）/ `none`（不通知） |
| `FANQIE_BOOK_NAME_MAX_LEN` | `100` | FR4 有效书名最大字符数 |
| `FANQIE_OCR_API_URL` | 官方默认 | OCR 任务提交地址，留空用官方默认 |
| `FANQIE_OCR_API_TOKEN` | 空 | OCR API 认证令牌（必填） |
| `FANQIE_OCR_TIMEOUT` | `15` | 单次 HTTP 请求超时（秒） |
| `FANQIE_OCR_POLL_TIMEOUT` | `120` | 等待 OCR 任务完成总超时（秒） |
| `FANQIE_OCR_ENABLED` | `true` | 是否启用 PaddleOCR 识别；`false` 时跳过 OCR 直接走视觉模型判定（仅视觉模式） |
| `FANQIE_OCR_MODEL` | `PaddleOCR-VL-1.6` | PaddleOCR 模型名 |
| `FANQIE_OCR_MODELS` | `["PaddleOCR-VL-1.6", "PP-OCRv6", "PP-StructureV3"]` | 多模型并行识别的模型集合（JSON 数组），结果按字段融合 |
| `FANQIE_SIMILARITY_THRESHOLD` | `0.9` | 多模型字段融合的置信度阈值（0~1） |
| `FANQIE_VISION_ENABLED` | `true` | 是否启用视觉模型兜底/复核 |
| `FANQIE_VISION_API_BASE` | `https://api.deepseek.com` | 视觉模型 OpenAI 兼容 API 地址 |
| `FANQIE_VISION_API_KEY` | 空 | 视觉模型 API 密钥 |
| `FANQIE_VISION_MODEL` | `deepseek-v4-flash-vision-exp` | 视觉模型名称 |
| `FANQIE_VISION_TIMEOUT` | `60` | 视觉模型单次请求超时（秒） |
| `FANQIE_NOTICE_GATE_ENABLED` | `true` | 是否启用「群公告已读」闸门：绑定过公告的群里未确认阅读的新成员判不通过；未绑定公告/接口调用失败时不拦（降级放行） |
| `FANQIE_VERIFICATION_POLICY_PATH` | 空 | 放行策略 TOML 路径，空用 localstore 配置目录 |
| `FANQIE_MESSAGE_STORE_ENABLED` | `true` | 是否启用消息/审计存储 |
| `FANQIE_MESSAGE_STORE_SUMMARY_LIMIT` | `500` | 文本摘要最大字符数 |
| `FANQIE_MESSAGE_STORE_CLEANUP_ENABLED` | `true` | 关闭时是否清理过期记录 |
| `FANQIE_MESSAGE_STORE_RETENTION_DAYS` | `7` | 记录保留天数 |
| `FANQIE_MESSAGE_STORE_RECORD_API_CALLS` | `false` | 是否记录平台 API 调用审计 |

完整模板见 `.env.example`。

### 放行策略 TOML（FR4）

首次运行后生成在 `config/nonebot_plugin_fanqie_verify/verification_policy.toml`。
策略**以群为节点**组织：每个群下可配置多个作者，每个作者下可配置多个作品（书名）。

```toml
[verification]
require_all = false          # true 要求全部受支持元素匹配；false 按 required_elements
required_elements = ["book_name", "author"]  # require_all=false 时必配元素

# 群节点：群号 → 作者列表。配置了节点的群才执行验证。
[verification.groups]

[[verification.groups.868258211.authors]]
name = "阿百川大鬼"
books = ["综漫：吉他雇佣兵无法找到归宿？", "另一部作品"]

[[verification.groups.868258211.authors]]
name = "刘慈欣"
books = ["三体", "球状闪电"]
```

判定规则（按群）：
- **群未配置任何作者节点**：放行（宽松模式，仅校验必配元素存在）。
- **群配置了作者节点**：提取到的**作者必须命中其中一个作者名**（只校验作者，
  `books` 作品列表仅作配置参考与审计展示，不参与判定）。
- 群节点本身即**监控范围**：只有配置了节点的群才会执行入群验证。

受支持的元素名：`reader_name`、`publish_time`、`rating`、`read_duration`、
`book_name`、`author`、`review_text`。

## 依赖与安装

- 运行依赖（以 `pyproject.toml` 的 `dependencies` 为准）：`nonebot2[fastapi,httpx,websockets] >= 2.5.0`、
  `nonebot-adapter-onebot >= 2.4.6`、`nonebot-plugin-localstore >= 0.7.4`、
  `nonebot-plugin-orm[aiosqlite] >= 0.8.3`（验证会话持久化）、`aiofiles >= 25.1.0`、
  `openai >= 1.0.0`（视觉模型调用）、`rtoml >= 0.13.0`（放行策略读写）
- 可选依赖（extra `htmlkit`）：`nonebot-plugin-htmlkit == 0.1.0rc5`（群公告命令的图片渲染；未安装时这些命令自动回退纯文本，不影响入群验证主流程）
- Python：`>= 3.13, < 3.14`
- 安装（宿主以 git 子模块引入）：

```bash
git submodule add -b main https://github.com/xinvxueyuan/nonebot-plugin-fanqie-verify.git \
  vendor/nonebot-plugin-fanqie-verify
git -C vendor/nonebot-plugin-fanqie-verify checkout <钉定的 SHA>
```

- 宿主 `[tool.nonebot] plugin_dirs` 加一行 `vendor/nonebot-plugin-fanqie-verify/src/plugins`
  （本仓自带的 `plugin_dirs = ["src/plugins"]` 是插件独立开发时用的）

## 测试与门禁

```bash
uv run python -m pytest tests/ -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

- 等价的 Taskfile 封装：`task test`、`task check`、`task fix`、`task ci`（check → test）。
- 冒烟测试：`uv run python -m pytest -m smoke`（插件加载与核心业务链路的快速验证，`tests/smoke/`）。

## 如何开始

前置要求：Python 3.13、[uv](https://docs.astral.sh/uv/)、一个 OneBot V11 实现（如
[NapCat](https://napneko.github.io/) / [go-cqhttp](https://docs.go-cqhttp.org/)）。

1. 安装依赖：

   ```bash
   uv sync --all-groups
   ```

2. 配置环境变量：复制 `.env.example` 为 `.env` 并填写：

   ```bash
   cp .env.example .env
   ```

   至少需要配置：
   - `FANQIE_OCR_API_TOKEN`：PaddleOCR 云端 API 令牌
   - `SUPERUSERS` / `FANQIE_ADMIN_IDS`：管理员 QQ 号（`/keep`、
     `重载番茄OCR配置` 命令权限，NoneBot 要求 SUPERUSER）
   - 群范围：放行策略 TOML 中配置群节点（配置了节点的群才验证）

3. 配置 OneBot V11 反向 WebSocket：

   在 NapCat / go-cqhttp 中添加反向 WebSocket 连接，地址指向本插件的
   `ws://<HOST>:<PORT>/onebot/v11/ws`（默认 `ws://127.0.0.1:8080/onebot/v11/ws`）。
   HOST / PORT 由 `.env` 中的 `HOST` / `PORT` 决定。

4. 首次运行生成放行策略配置，然后按需编辑（见上文）：

   ```bash
   uv run nb run
   ```

5. 运行机器人（开发热重载）：

   ```bash
   uv run nb run --reload
   ```

## 验证目标与判定

新成员需发送**自己发布的番茄小说书评详情页截图**。页面关键标识：

- 「**我**」徽章：书评为自己发布（他人书评无此徽章，将被拒绝）
- 读者名、发布日期、评分（★）、阅读时长、书评正文
- 书名（如 `综漫：吉他雇佣兵无法找到归宿？`）、作者

判定标准：**必须检测到「我」徽章 + 提取到书名与作者**。

## 验证流程

1. 新成员入群 → 机器人 @ 成员发送验证提示并开始超时计时
2. 成员发送**自己发布的书评详情页截图** → OCR 识别 → 提取「我」徽章、书名、作者、读者名等
3. 综合判断：本人书评（「我」徽章）+ 信息足够 + 放行策略满足 + 合理性通过 → **放行**（欢迎）
4. 否则（他人书评 / 缺少书名作者 / 白名单不命中 / 超时未发图 / 识别失败达上限）→ 转入**待管理员决策**并私信通知管理员，同时响应超时会在群内 @ 成员提示已超时、可发「重审」；管理员用 `/keep` 放行（移出请用 lingchu-bot 的「踢出」命令）
5. 管理员超过 `FANQIE_ADMIN_DECISION_TIMEOUT`（默认 16 小时）未处理 → 被移出前最后 1 小时、5 分钟各在群内 @ 提醒该成员；仍未处理则群内通报并移出该成员
6. 所有进行中的会话（等待提交 / 待管理员决策）持久化到数据库，重启后自动恢复，不会把验证中的成员误当作已通过

## 开发

```bash
task check   # ruff + pyright
task test    # pytest
task fix     # 自动修复 lint/格式
```

插件源码位于 `src/plugins/nonebot_plugin_fanqie_verify/`。

## 更新日志

各版本的重要变更见 [CHANGELOG.md](./CHANGELOG.md)。

## 文档

- [NoneBot2 文档](https://nonebot.dev/)
- [PaddleOCR / PP-OCRv6](https://www.paddleocr.ai/)

## 许可

MIT（见仓库根目录 `LICENSE` 文件，Copyright (c) 2026 xinvxueyuan；`pyproject.toml` 的 `license` 字段同为 MIT）。
