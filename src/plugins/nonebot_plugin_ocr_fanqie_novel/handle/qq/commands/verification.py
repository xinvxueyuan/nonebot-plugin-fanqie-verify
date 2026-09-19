"""QQ 平台命令定义与事件响应器。"""

from nonebot import on_command, on_message, on_notice, on_type
from nonebot.adapters.onebot.v11 import (
    GroupAdminNoticeEvent,
    GroupBanNoticeEvent,
    GroupDecreaseNoticeEvent,
)
from nonebot.adapters.onebot.v11.event import (
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.message import MessageSegment
from nonebot.permission import SUPERUSER

from ....services.verification import get_session_store


def _has_pending_session(event: GroupMessageEvent) -> bool:
    """事件是否来自有待验证会话的成员。"""
    return get_session_store().is_waiting(
        str(event.group_id),
        str(event.user_id),
    )


def _is_sticker(segment: MessageSegment) -> bool:
    """判断 image 段是否为表情包（商城表情转换而来，带 emoji 字段）。

    LLOneBot/NapCat 里商城表情（mface）转换为 image 类型时，data 会带
    ``emojiId``/``emojiPackageId``（LLOneBot 驼峰）或 ``emoji_id``/
    ``emoji_package_id``（NapCat 下划线）；普通图片没有这些字段。

    """
    if segment.type != "image":
        return False
    data = segment.data
    return bool(
        data.get("emojiId")
        or data.get("emojiPackageId")
        or data.get("emoji_id")
        or data.get("emoji_package_id")
    )


def _contains_image(event: MessageEvent) -> bool:
    """消息是否包含图片消息段（排除表情包/商城表情）。"""
    return any(
        segment.type == "image" and not _is_sticker(segment)
        for segment in event.message
    )


# FR1：新人入群。低优先级、不 block，确保其他插件也能响应群增事件。
group_increase = on_notice(priority=1, block=False)

# 成员退群：清理遗留会话。
group_decrease = on_type(GroupDecreaseNoticeEvent, priority=1, block=False)

# 群管理员变动：用于边界守卫（成员被提升管理员后不再需要验证）。
group_admin_change = on_type(GroupAdminNoticeEvent, priority=1, block=False)

# 群禁言事件：同步会话的禁言状态，防止重复禁言。
group_ban = on_type(GroupBanNoticeEvent, priority=1, block=False)

# FR2：待验证成员的图片提交。
# 注意：不使用自定义 Rule（避免 NoneBot 依赖注入对 DependencyCache 的
# TypeAdapter 构建问题），改为在处理器内自行判断。
image_submission = on_message(priority=5, block=False)

# 私聊验证：处理用户私聊机器人的待验证群截图。
# onebot.v11 私聊事件；processor 内按是否带图分流。
private_image_submission = on_type(
    PrivateMessageEvent,
    priority=5,
    block=False,
)

# 私聊选群：多群待验证时，用户用「验证 <群号>」指定目标群。
verify_cmd = on_command(
    "验证",
    aliases={"/验证", "选择群", "切换群"},
    priority=5,
    block=True,
)

# FR9：管理员决策命令。
# 注意：zhenxun 全局 COMMAND_START=[""]（裸词匹配），NoneBot2 的 on_command
# 无 command_start 参数（透传给 on() 会 TypeError）。为使通知文案里的
# "/kick /keep"（带斜杠）与裸词写法都能命中，把带 "/" 的写法加入 aliases：
# COMMAND_START=[""] 下，命令名 "/keep" 恰好匹配消息 "/keep ..."。
kick_cmd = on_command(
    "kick",
    aliases={"踢出", "踢", "/kick"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)
keep_cmd = on_command(
    "keep",
    aliases={"保留", "留", "/keep"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 管理员“插入直接批准”：对新入群且仍在验证流程(等待截图/待管理员决策)
# 的成员直接放行并欢迎，无需等待成员提交截图或超时。
approve_cmd = on_command(
    "通过",
    aliases={"直接通过", "批准", "放行", "/通过", "/approve"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 配置热重载：管理员手动触发重新加载策略与提取规则 TOML。
reload_config_cmd = on_command(
    "重载番茄OCR配置",
    aliases={"重载OCR配置", "重载配置"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 待处理列表：查询本群等待管理员决策的成员。
pending_list_cmd = on_command(
    "待处理列表",
    aliases={"待处理", "未处理列表"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 处理中列表：查询本群正在等待提交截图的成员。
processing_cmd = on_command(
    "处理中列表",
    aliases={"处理中", "验证中"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 查看白名单：展示当前群已配置的作者与作品列表。
whitelist_cmd = on_command(
    "查看白名单",
    aliases={"白名单", "作者白名单", "/白名单"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 补验：把错过入群事件的成员补进验证流程（LLBot 掉线重连等场景）。
# 无参数时扫描最近 N 小时（配置默认）内入群且无验证记录的成员；
# 也可带小时数（如「补验 48」）或直接指定成员（@成员 / QQ 号）。
backfill_cmd = on_command(
    "补验",
    aliases={"补验证", "同步入群", "漏验扫描", "/补验"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 补验确认：执行「补验」列出的候选名单（confirm_first 模式使用）。
backfill_confirm_cmd = on_command(
    "补验确认",
    aliases={"确认补验", "/补验确认"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 延期：推迟「待管理员决策」成员的自动移出时间（管理员暂时无法处理时用）。
# 不带成员时作用于本群全部待审成员；不带时长时按配置的默认小时数延期。
extend_cmd = on_command(
    "延期",
    aliases={"推迟", "延长", "/延期", "/extend"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 重审：普通成员重审自己（限次数），管理员可 @ 任意普通成员重审（不限次数）。
# 不带 permission 限定——权限与次数在处理器内按发起者身份判断。
review_cmd = on_command(
    "重审",
    aliases={"重新审核", "重新验证", "/重审"},
    priority=5,
    block=True,
)


__all__ = [
    "approve_cmd",
    "backfill_cmd",
    "backfill_confirm_cmd",
    "extend_cmd",
    "group_admin_change",
    "group_ban",
    "group_decrease",
    "group_increase",
    "image_submission",
    "keep_cmd",
    "kick_cmd",
    "pending_list_cmd",
    "private_image_submission",
    "processing_cmd",
    "reload_config_cmd",
    "review_cmd",
    "verify_cmd",
    "whitelist_cmd",
]
