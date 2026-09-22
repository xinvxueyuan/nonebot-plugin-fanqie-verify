# 更新日志

本插件的所有重要变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。0.x 阶段不稳定的破坏性变更按次版本号递增。

## [0.4.0] - 2026-09-23

⚠️ **本版本包含破坏性变更，升级前请先阅读「破坏性变更」一节。**

### 破坏性变更

- **仓库与 Python 包重命名**：`ocr-fanqie-novel` → `nonebot-plugin-fanqie-verify`
  （包名 `nonebot_plugin_ocr_fanqie_novel` → `nonebot_plugin_fanqie_verify`）。
  旧仓库地址会 301 重定向，但**包名变更需同步调整部署侧**：
  插件目录名要一并改名，且需移走 `nonebot_plugin_orm` 按包名归档的旧迁移缓存目录
  （`~/.local/share/nonebot2/nonebot_plugin_orm/migrations/<旧包名>/`），
  否则启动会报 `Revision ... is present more than once`。
- **移除 `FANQIE_NOTIFY_ADMIN`**：验证失败的通知开关由布尔值改为三态渠道
  `FANQIE_NOTIFY_CHANNEL=group|private|none`。旧的 `FANQIE_NOTIFY_ADMIN`
  已从配置模型删除，**写了会被忽略**（`false` 应对应 `none`，`true` 对应 `private`）。

### 新增

- **「补验」命令**：机器人掉线期间 QQ 不补发 `group_increase`，这期间入群的成员
  不会被验证。`补验 [小时数] [@成员/QQ号]` 按入群时间与数据库记录筛出漏验成员并
  补进验证流程；`补验确认` 可在正式执行前先列名单。支持重连后按
  `FANQIE_BACKFILL_RECONNECT_MODE` 自动/提醒/关闭。
- **「延期」命令**：`延期 [@成员/QQ号] [小时数]` 推迟待管理员决策（`awaiting_admin`）
  成员的自动移出；不带参数时作用于本群全部待审成员。别名为「推迟」「延长」。
- **通知渠道三态**（`FANQIE_NOTIFY_CHANNEL`）：
  - `group`（默认）在群内发**一条**消息并 @ 全部管理员，可引用成员原消息；
  - `private` 逐个私聊管理员，私聊失败回退群内；
  - `none` 完全不发送。
- **踢人容灾**：踢出失败（掉线/网络异常/权限不足）时不再丢状态 ——
  新增 `kick_pending` 待补踢状态，按 `FANQIE_KICK_RETRY_TIMES` ×
  `FANQIE_KICK_RETRY_DELAY` 后台重试，机器人重连时（`on_bot_connect`）自动补踢。
- **群内消息引用原消息发送**：响应类群消息统一引用成员的原消息，便于对照上下文。
- **`FANQIE_OCR_ENABLED` 开关**：为 `false` 时完全跳过 PaddleOCR，直接使用视觉模型
  端到端判定（仅视觉模式），可显著降低 OCR 调用成本。

### 修复

- **首次验证未通过即转管理员**：仅视觉模式下 `_handle_reject` 绕过了重试计数，
  导致 `FANQIE_MAX_ATTEMPTS` 形同废弃。现与 OCR 路径对齐，统一走重试计数，
  达上限才转管理员决策（并保留识别结果用于通知）。
- **自定义表情包被当作验证截图**：改用 image 段的 `subType`（即 QQ `bizType`）判据 ——
  用户收藏的自定义表情走图片段、不带任何 emoji 字段，旧的 emoji 判据对它是死码。
- **成员退群/被踢后会话未落库**：原先只清内存，重启后已退群成员的会话会被
  `restore_pending_sessions` 恢复，在「待处理列表」里误导管理员。现落库为终态，
  并区分 `leave`（主动退群→`left_group`）与 `kick`（被踢→`kicked`）。
- **踢人失败仍被标记为已踢**：超时处理忽略踢出返回值，失败也标 `kicked`；
  bot 不在线时还会直接按 `expired` 结案（成员既未移出也没留下痕迹）。均已修正。
- 并发提交截图导致欢迎消息重复发送。
- 超时处理在机器人缺失时转为终态，修复「处理中列表」长期显示「剩余 0 分」的堆积。

### 变更

- `/keep`、`/kick` 默认允许群管理员（owner/admin）使用，可用
  `FANQIE_ALLOW_GROUP_ADMIN_COMMANDS` 收窄为仅配置的机器人管理员。
- 新增「待处理列表」（待决策）与「处理中列表」（等待截图）两条查询命令。
- 书评判定细化书评/段评/章评三种形式的特征，段评与章评一律判为无效。
- 新增以下配置项（完整列表见 README 配置表）：
  `FANQIE_BACKFILL_ENABLED`、`FANQIE_BACKFILL_DEFAULT_HOURS`、
  `FANQIE_BACKFILL_RECONNECT_MODE`、`FANQIE_BACKFILL_CONFIRM_FIRST`、
  `FANQIE_BACKFILL_MAX_BATCH`、`FANQIE_EXTEND_ENABLED`、
  `FANQIE_EXTEND_DEFAULT_HOURS`、`FANQIE_EXTEND_MAX_HOURS`、
  `FANQIE_KICK_RETRY_TIMES`、`FANQIE_KICK_RETRY_DELAY`、
  `FANQIE_NOTIFY_CHANNEL`、`FANQIE_OCR_ENABLED`。

### 说明

- 群解散（`group_dismiss`）无法被监听：nonebot 的 OneBot11 适配器没有对应的
  事件类，该事件会被丢弃。实际解散通常伴随 `kick_me`，已由整群清理逻辑兼顾。

## [0.3.0] - 2026-09-11

### 修复

- 并发提交截图导致欢迎消息发送两次（新增原子处理标记）。
- 机器人缺失时超时处理转为终态，修复「处理中列表」0 秒堆积。
- 表情包不再被当作验证截图。
- 补全评论形式：段评与章评均判为无效。

### 变更

- 视觉判定细化书评/段评/章评三种形式的特征（最可靠信号为五角星评分组件）。
- `/keep`、`/kick` 默认支持群管理员，新增「处理中列表」命令。

## [0.2.0] - 2026-08-17

### 新增

- OCR 识别失败时的视觉模型兜底判定。
- 欢迎文案支持按群配置（策略文件群节点的 `welcome_message`）。

### 变更

- 响应超时由 300 秒调整为 600 秒。

## [0.1.2] - 2026-08-14

### 修复

- 入群验证流程的边界问题与提示文案调整。

## [0.1.1] - 2026-08-14

### 新增

- 管理员决策超时后的群内通报与移出流程。
- 移出前提醒（默认提前 1 小时与 5 分钟）。

## [0.1.0] - 2026-08-14

### 新增

- 首个版本：新成员入群后要求发送自己发布的番茄小说书评详情页截图，
  OCR 识别书评信息并依据放行策略自动通过，失败则通知管理员决策。

[0.4.0]: https://github.com/xinvxueyuan/nonebot-plugin-fanqie-verify/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/xinvxueyuan/nonebot-plugin-fanqie-verify/releases/tag/v0.3.0
[0.2.0]: https://github.com/xinvxueyuan/nonebot-plugin-fanqie-verify/releases/tag/v0.2.0
[0.1.2]: https://github.com/xinvxueyuan/nonebot-plugin-fanqie-verify/releases/tag/v0.1.2
[0.1.1]: https://github.com/xinvxueyuan/nonebot-plugin-fanqie-verify/releases/tag/v0.1.1
[0.1.0]: https://github.com/xinvxueyuan/nonebot-plugin-fanqie-verify/releases/tag/v0.1.0
