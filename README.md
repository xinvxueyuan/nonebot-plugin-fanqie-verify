# ocr-fanqie-novel

番茄读书群入群验证插件：新成员入群后要求发送**自己发布的番茄小说书评详情页截图**，
通过 PaddleOCR（PP-OCRv6）云端 API 识别书评信息，校验「我」徽章、书名、作者后
自动放行；验证失败则私信管理员决定通过或踢出。

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
- **FR9 管理员命令**：`/kick`、`/keep`、`/通过` 决定踢出/保留/直接批准；`待处理列表`、`处理中列表` 查询本群待决策/待提交成员；`查看白名单` 查看本群作者与作品配置
- **状态持久化**：验证会话（等待提交 / 待管理员决策）持久化到数据库，重启后自动恢复并重建超时，不会把验证中的成员误当作已通过
- **追踪 ID**：每次验证流程携带全局唯一 trace ID，贯穿日志、落库（会话/事件/审计三表）与管理员通知，可回溯完整判定链路
- **运行时重载**：`重载番茄OCR配置` 命令热更新放行策略
- **边界守卫**：监听退群/管理员变动/禁言事件，操作前查询群成员状态，避免对已退出成员执行无效动作
- **漏验补验**：机器人（LLBot）掉线期间入群的成员不会收到入群事件，`补验` 命令可按「入群时间在最近 N 小时内 + 无验证记录」扫描并补进验证流程，也支持直接指定成员；机器人重连时可配置为自动补验或仅提醒管理员
- **延期审核**：管理员可用 `延期` 命令推迟「待管理员决策」成员的自动移出时间（默认 6 小时、单次上限 48 小时，均可配置），用于暂时无法处理时保住成员；可指定成员（`延期 @成员 12`）或对本群全部待审成员生效（`延期`），不限制累计次数
- **引用回复**：群内各项回复（提交反馈、命令回复、验证通过欢迎、超时通报、移出前提醒）均**引用原消息**发送，便于成员对照上下文；超时类通报引用该成员最后提交的图片消息。私聊消息与入群引导（系统事件，无原消息）保持普通发送

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
   - `SUPERUSERS` / `FANQIE_ADMIN_IDS`：管理员 QQ 号（`/kick`、`/keep`、
     `重载番茄OCR配置` 命令权限，NoneBot 要求 SUPERUSER）
   - 群范围：放行策略 TOML 中配置群节点（配置了节点的群才验证）

3. 配置 OneBot V11 反向 WebSocket：

   在 NapCat / go-cqhttp 中添加反向 WebSocket 连接，地址指向本插件的
   `ws://<HOST>:<PORT>/onebot/v11/ws`（默认 `ws://127.0.0.1:8080/onebot/v11/ws`）。
   HOST / PORT 由 `.env` 中的 `HOST` / `PORT` 决定。

4. 首次运行生成放行策略配置，然后按需编辑（见下文）：

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

## 配置

### 环境变量

以下变量通过 `.env`（或 `.env.dev` / `.env.prod`）配置，环境变量名大小写不敏感。

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `FANQIE_VERIFY_GROUPS` | 需要验证的群号集合，`[]` 表示全部群（与策略群节点取交集） | `[]` |
| `FANQIE_ADMIN_IDS` | 管理员 QQ 号集合，可接收通知并执行 `/kick` `/keep` | `[]` |
| `FANQIE_WELCOME_MESSAGE` | 入群提示消息 | 内置默认文案 |
| `FANQIE_RESPONSE_TIMEOUT` | 等待新成员发送截图的超时（秒），超时按验证失败处理并通知管理员决策 | `600` |
| `FANQIE_MAX_ATTEMPTS` | 识别失败允许尝试次数，达上限按验证失败处理并通知管理员决策 | `3` |
| `FANQIE_ADMIN_DECISION_TIMEOUT` | 通知管理员后等待其决策的超时（秒），超时群内通报并移出成员 | `57600`（16 小时） |
| `FANQIE_REMIND_BEFORE_KICK` | 待管理员决策成员被移出前提前提醒的秒数列表（升序），如 `[3600, 300]` 表示最后 1 小时、5 分钟各提醒一次 | `[3600, 300]` |
| `FANQIE_REVIEW_MAX_TIMES` | 普通成员通过「重审」命令重新验证的最大次数 | `2` |
| `FANQIE_ALLOW_GROUP_ADMIN_COMMANDS` | 是否允许群内管理员（admin/群主）使用 `/keep` `/kick` 等命令 | `true` |
| `FANQIE_PRIVATE_VERIFY_ENABLED` | 是否启用「私聊发图完成验证」通道 | `true` |
| `FANQIE_BACKFILL_ENABLED` | 是否启用「补验」（补进错过入群事件的成员） | `true` |
| `FANQIE_BACKFILL_DEFAULT_HOURS` | 补验默认扫描窗口（小时） | `24` |
| `FANQIE_BACKFILL_RECONNECT_MODE` | 机器人重连时的补验行为：`notify` 仅提醒 / `auto` 自动补验 / `off` 不处理 | `notify` |
| `FANQIE_BACKFILL_CONFIRM_FIRST` | 补验是否先列候选名单等确认 | `false` |
| `FANQIE_BACKFILL_MAX_BATCH` | 单次补验人数上限 | `20` |
| `FANQIE_EXTEND_ENABLED` | 是否启用「延期」命令 | `true` |
| `FANQIE_EXTEND_DEFAULT_HOURS` | 延期不带时长参数时的默认小时数 | `6` |
| `FANQIE_EXTEND_MAX_HOURS` | 单次延期的最大小时数（不限制累计次数） | `48` |
| `FANQIE_NOTIFY_ADMIN` | 验证失败时是否私信通知管理员决定通过或踢出 | `true` |
| `FANQIE_BOOK_NAME_MAX_LEN` | FR4 有效书名最大字符数 | `100` |
| `FANQIE_OCR_API_URL` | OCR 任务提交地址，留空用官方默认 | 官方默认 |
| `FANQIE_OCR_API_TOKEN` | OCR API 认证令牌（必填） | 空 |
| `FANQIE_OCR_TIMEOUT` | 单次 HTTP 请求超时（秒） | `15` |
| `FANQIE_OCR_POLL_TIMEOUT` | 等待 OCR 任务完成总超时（秒） | `120` |
| `FANQIE_OCR_ENABLED` | 是否启用 PaddleOCR 识别；`false` 时跳过 OCR 直接走视觉模型判定（仅视觉模式） | `true` |
| `FANQIE_OCR_MODEL` | PaddleOCR 模型名 | `PaddleOCR-VL-1.6` |
| `FANQIE_OCR_MODELS` | 多模型并行识别的模型集合（JSON 数组），结果按字段融合 | `["PaddleOCR-VL-1.6", "PP-OCRv6", "PP-StructureV3"]` |
| `FANQIE_SIMILARITY_THRESHOLD` | 多模型字段融合的置信度阈值（0~1） | `0.9` |
| `FANQIE_VISION_ENABLED` | 是否启用视觉模型兜底/复核 | `true` |
| `FANQIE_VISION_API_BASE` | 视觉模型 OpenAI 兼容 API 地址 | `https://api.deepseek.com` |
| `FANQIE_VISION_API_KEY` | 视觉模型 API 密钥 | 空 |
| `FANQIE_VISION_MODEL` | 视觉模型名称 | `deepseek-v4-flash-vision-exp` |
| `FANQIE_VISION_TIMEOUT` | 视觉模型单次请求超时（秒） | `60` |
| `FANQIE_VERIFICATION_POLICY_PATH` | 放行策略 TOML 路径，空用 localstore 配置目录 | 空 |
| `FANQIE_MESSAGE_STORE_ENABLED` | 是否启用消息/审计存储 | `true` |
| `FANQIE_MESSAGE_STORE_SUMMARY_LIMIT` | 文本摘要最大字符数 | `500` |
| `FANQIE_MESSAGE_STORE_CLEANUP_ENABLED` | 关闭时是否清理过期记录 | `true` |
| `FANQIE_MESSAGE_STORE_RETENTION_DAYS` | 记录保留天数 | `7` |
| `FANQIE_MESSAGE_STORE_RECORD_API_CALLS` | 是否记录平台 API 调用审计 | `false` |

完整模板见 `.env.example`。

### 放行策略 TOML（FR4）

首次运行后生成在 `config/nonebot_plugin_ocr_fanqie_novel/verification_policy.toml`。
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

## 管理员命令

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `/kick <user_id>` | 超级用户 / 配置管理员 | 将指定成员移出群聊 |
| `/keep <user_id>` | 超级用户 / 配置管理员 | 通过并保留成员（仅限处于验证流程中的成员） |
| `/通过 <user_id>` | 超级用户 / 配置管理员 | **插入直接批准**：对新入群且仍在验证流程（等待截图 / 待管理员决策）的成员直接放行并欢迎，无需等待提交截图或超时 |
| `待处理列表` | 超级用户 / 配置管理员 | 查询本群等待管理员决策的成员及剩余时间 |
| `处理中列表` | 超级用户 / 配置管理员 | 查询本群正在等待截图提交（验证中）的成员 |
| `补验 [小时数] [@成员/QQ号]` | 超级用户 / 配置管理员 | 把错过入群事件的成员补进验证流程：无参数按默认窗口扫描，可带小时数（如 `补验 48`）或指定成员；`FANQIE_BACKFILL_CONFIRM_FIRST=true` 时先列名单 |
| `补验确认` | 超级用户 / 配置管理员 | 执行 `补验` 列出的候选名单（仅 `CONFIRM_FIRST=true` 时用到） |
| `延期 [@成员/QQ号] [小时数]` | 超级用户 / 配置管理员 | 推迟「待管理员决策」成员的自动移出时间（新截止 = 当前时间 + 延期时长）：不带成员时作用于本群全部待审成员；不带时长时用 `FANQIE_EXTEND_DEFAULT_HOURS`（默认 6 小时），单次上限 `FANQIE_EXTEND_MAX_HOURS`（默认 48 小时，超出按上限处理） |
| `查看白名单` | 所有群成员 | 展示当前群配置的作者与作品列表 |
| `重载番茄OCR配置` | 超级用户 / 配置管理员 | 热重载放行策略 TOML |
| `重审 [@成员]` | 所有群成员 | 普通成员重审**自己**（限 `FANQIE_REVIEW_MAX_TIMES` 次，默认 2）；管理员 `@成员` 可重审任意普通成员（不限次数）。重审复用完整验证流程：重置识别重试、重新发送引导、重排超时窗口 |

`<user_id>` 也可用 `@成员` 代替。命令前缀由 `COMMAND_START` 决定（默认 `/`）。「重审」命令不受前缀限制（`重审` 与 `/重审` 均可用）。

### 成员命令（私聊验证）

| 命令 | 说明 |
| --- | --- |
| `验证 <群号>` | 多群待验证时，指定在哪个群验证（私聊机器人发送） |
| （直接私聊发书评截图） | 若恰有一个待验证群则直接验证；多群则先提示用 `验证 群号` 选择 |

## 验证流程

1. 新成员入群 → 机器人 @ 成员发送验证提示并开始超时计时
2. 成员发送**自己发布的书评详情页截图** → OCR 识别 → 提取「我」徽章、书名、作者、读者名等
3. 综合判断：本人书评（「我」徽章）+ 信息足够 + 放行策略满足 + 合理性通过 → **放行**（欢迎）
4. 否则（他人书评 / 缺少书名作者 / 白名单不命中 / 超时未发图 / 识别失败达上限）→ 转入**待管理员决策**并私信通知管理员，同时响应超时会在群内 @ 成员提示已超时、可发「重审」；管理员用 `/kick` 或 `/keep` 决策
5. 管理员超过 `FANQIE_ADMIN_DECISION_TIMEOUT`（默认 16 小时）未处理 → 被移出前最后 1 小时、5 分钟各在群内 @ 提醒该成员；仍未处理则群内通报并移出该成员
6. 所有进行中的会话（等待提交 / 待管理员决策）持久化到数据库，重启后自动恢复，不会把验证中的成员误当作已通过

## 开发

```bash
task check   # ruff + pyright
task test    # pytest
task fix     # 自动修复 lint/格式
```

插件源码位于 `src/plugins/nonebot_plugin_ocr_fanqie_novel/`。

## 文档

- [NoneBot2 文档](https://nonebot.dev/)
- [PaddleOCR / PP-OCRv6](https://www.paddleocr.ai/)
