"""入群验证动作：踢出、通知管理员、发送引导/欢迎消息（FR5/7/8/9）。

验证失败统一为通知管理员决策（通过或踢出），不执行禁言。所有群管理
动作（踢出）在执行前都会通过 ``get_group_member_info`` 做边界守卫：
目标成员已不在群等特殊情况会被提前识别并跳过。

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nonebot import logger
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.adapters.onebot.v11.message import Message, MessageSegment

from ...core.config import plugin_config

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, GroupMessageEvent

_ACTION_ERROR = "执行群管理操作失败（权限不足或参数错误）"

# 群成员 role 的合法取值。
_ROLE_OWNER = "owner"
_ROLE_ADMIN = "admin"
_ROLE_MEMBER = "member"


@dataclass(frozen=True, slots=True)
class MemberInfo:
    """群成员信息快照（用于边界守卫）。

    Attributes:
        user_id: 成员 QQ 号。
        role: 群内角色（owner/admin/member）。
        card: 群名片，无则取昵称。
        nickname: 昵称。
        shut_up_timestamp: 禁言到期时间戳（秒），0 表示未禁言。

    """

    user_id: int
    role: str
    card: str
    nickname: str
    shut_up_timestamp: int

    @property
    def display_name(self) -> str:
        """展示名：优先群名片，其次昵称，最后 QQ 号。"""
        return self.card or self.nickname or str(self.user_id)

    @property
    def is_admin(self) -> bool:
        """是否为群主或管理员。"""
        return self.role in (_ROLE_OWNER, _ROLE_ADMIN)

    @property
    def is_muted(self) -> bool:
        """当前是否处于禁言状态（禁言到期时间在未来）。"""
        if self.shut_up_timestamp <= 0:
            return False
        return self.shut_up_timestamp > int(datetime.now(UTC).timestamp())


async def get_member_info(
    bot: OneBot11Bot,
    group_id: int,
    user_id: int,
) -> MemberInfo | None:
    """获取群成员信息；成员不在群或查询失败时返回 ``None``。

    Args:
        bot: OneBot11 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。

    Returns:
        成员信息快照；无法获取（如成员已退群）时为 ``None``。

    """
    try:
        info = await bot.get_group_member_info(
            group_id=group_id,
            user_id=user_id,
            no_cache=True,
        )
    except ActionFailed:
        logger.debug(
            "获取群成员信息失败，视为成员不在群: group={} user={}",
            group_id,
            user_id,
        )
        return None
    if not isinstance(info, dict):
        return None
    return MemberInfo(
        user_id=int(info.get("user_id", user_id)),
        role=str(info.get("role", _ROLE_MEMBER)),
        card=str(info.get("card") or ""),
        nickname=str(info.get("nickname") or ""),
        shut_up_timestamp=int(info.get("shut_up_timestamp") or 0),
    )


def _is_admin(user_id: int) -> bool:
    """校验用户是否属于配置的管理员列表。"""
    return user_id in plugin_config.fanqie_admin_ids


async def send_guide(bot: OneBot11Bot, group_id: int, user_id: int) -> bool:
    """FR1：@新成员发送验证引导消息。"""
    timeout_minutes = max(1, plugin_config.fanqie_response_timeout // 60)
    quote_guide = (
        "可在群成员列表中点击我发起临时会话直接发送截图。\n"
        if plugin_config.fanqie_private_verify_enabled
        else ""
    )
    message = Message(MessageSegment.at(user_id)) + (
        f" {_welcome_message(group_id)} "
        f"请在 {timeout_minutes} 分钟内完成截图并发送，超时后将由管理员人工处理。"
        f"{quote_guide}"
        "【如何查找书评】进入番茄小说 App -> 右下角「我的」页面按压进入 -> "
        "点我的头像栏目进入用户信息页面 -> 在顶级菜单栏找到「讨论」并进入 -> "
        "在子菜单栏找到「书评」 -> 滚动屏幕找到本书的书评并点击进入「书评详情页」 -> "
        "直接截取手机整个屏幕（不要裁剪，含顶部「书评详情」标题）发送到本 QQ 群。"
    )
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
    except ActionFailed:
        logger.warning("发送验证引导消息失败 group={} user={}", group_id, user_id)
        return False
    return True


def _with_reply(message: Message, reply_message_id: int | None) -> Message:
    """按需在消息前加入引用段（引用原消息发送）。

    Args:
        message: 原始消息。
        reply_message_id: 被引用的消息 id；为 ``None``/空时原样返回。

    Returns:
        带引用段的消息（引用 id 为空则为原消息）。

    """
    if not reply_message_id:
        return message
    return Message(MessageSegment.reply(reply_message_id)) + message


def _welcome_message(group_id: int) -> str:
    """返回该群的验证引导文案。

    优先取策略配置中该群节点的自定义 ``welcome_message``，未配置时回退到
    全局默认 ``fanqie_welcome_message``。

    """
    from . import policy

    group = policy.get_policy().group_policy(group_id)
    if group is not None and group.welcome_message:
        return group.welcome_message
    return plugin_config.fanqie_welcome_message


async def send_welcome(
    bot: OneBot11Bot,
    group_id: int,
    user_id: int,
    reply_message_id: int | None = None,
) -> bool:
    """FR5：发送验证通过欢迎消息（可引用原消息）。"""
    message = _with_reply(
        Message(MessageSegment.at(user_id)) + " 验证通过，欢迎加入本群！",
        reply_message_id,
    )
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
    except ActionFailed:
        logger.warning("发送欢迎消息失败 group={} user={}", group_id, user_id)
        return False
    return True


async def announce_admin_timeout(
    bot: OneBot11Bot,
    group_id: int,
    user_id: int,
    reply_message_id: int | None = None,
) -> bool:
    """管理员决策超时：在群内通报该成员将被移出（可引用原消息）。"""
    message = _with_reply(
        Message(MessageSegment.at(user_id))
        + " 验证未通过且管理员超时未处理，现将其移出群聊。",
        reply_message_id,
    )
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
    except ActionFailed:
        logger.warning("群内通报超时移出失败 group={} user={}", group_id, user_id)
        return False
    return True


async def announce_member_timeout(
    bot: OneBot11Bot,
    group_id: int,
    user_id: int,
    reply_message_id: int | None = None,
) -> bool:
    """FR7：成员响应超时后，在群内 @ 成员提示已超时并指引重审。

    群消息发送成功后，额外私发一份给该新用户（便于其及时看到超时提示）。
    私发失败/异常静默屏蔽——对方可能未开启临时会话或已屏蔽机器人，不影响
    群消息主流程与返回结果。群消息可引用成员最后提交的图片消息；私聊
    无法跨会话引用，私发副本不带引用段。
    """
    message = Message(MessageSegment.at(user_id)) + (
        " 验证超时未收到书评截图。如需继续验证，请在群内发送「重审」重试，"
        "或等待管理员处理。"
    )
    ok = True
    try:
        await bot.send_group_msg(
            group_id=group_id,
            message=_with_reply(message, reply_message_id),
        )
    except ActionFailed:
        logger.warning("群内提示成员超时失败 group={} user={}", group_id, user_id)
        ok = False
    # 私发一份给新用户（尽力而为，失败静默；私聊无引用语义）。
    try:
        await bot.send_private_msg(user_id=user_id, message=message)
    except Exception:  # noqa: BLE001 - 私发失败需静默，不影响主流程
        logger.debug("私发超时提示失败（可能未开启临时会话）：user={}", user_id)
    return ok


async def announce_kick_reminder(
    bot: OneBot11Bot,
    group_id: int,
    user_id: int,
    remaining_seconds: int,
    reply_message_id: int | None = None,
) -> bool:
    """管理决策超时前提醒成员：临近被移出，指引重审。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。
        remaining_seconds: 距被移出剩余的秒数。
        reply_message_id: 被引用的消息 id；为空则不带引用段。

    Returns:
        是否发送成功。

    """
    remaining = _format_remaining(remaining_seconds)
    message = _with_reply(
        Message(MessageSegment.at(user_id))
        + (
            f" 您的验证尚未通过，距被移出群聊还有约 {remaining}。"
            "如需继续验证，请在群内发送「重审」重试，或请管理员处理。"
        ),
        reply_message_id,
    )
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
    except ActionFailed:
        logger.warning("群内移出前提醒失败 group={} user={}", group_id, user_id)
        return False
    return True


def _format_remaining(total_seconds: int) -> str:
    """把剩余秒数格式化为人类可读的时长描述。"""
    total = max(0, int(total_seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{max(1, minutes)} 分钟"


async def kick_member(
    bot: OneBot11Bot,
    group_id: int,
    user_id: int,
    member: MemberInfo | None = None,
) -> bool:
    """FR7/8/9：将成员移出群聊。

    边界守卫：成员已不在群时返回 ``False``（视为已达成目标状态）。

    Args:
        bot: OneBot11 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。
        member: 预取的成员信息；为 ``None`` 时内部重新查询。

    Returns:
        是否已不在群（踢出成功或原本已不在）。

    """
    if member is None:
        member = await get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info("踢出跳过：成员 {} 不在群 {} 中", user_id, group_id)
        return True
    try:
        await bot.set_group_kick(group_id=group_id, user_id=user_id)
    except ActionFailed:
        logger.warning("踢出失败 group={} user={}", group_id, user_id)
        return False
    return True


async def notify_admins(
    bot: OneBot11Bot,
    *,
    group_id: int,
    user_id: int,
    event: GroupMessageEvent | None,
    message: str,
) -> int:
    """向配置的管理员列表发送通知（私聊优先，回退群内转发）。

    通知附加上对应群的作者白名单（来自放行策略的群节点），并提示管理
    员在群内执行 /kick 或 /keep 决策。

    Args:
        bot: OneBot11 Bot 实例。
        group_id: 群号。
        user_id: 相关成员 QQ 号。
        event: 触发事件（用于回退发送）。
        message: 通知文本。

    Returns:
        成功发送的管理员数量。

    """
    if not plugin_config.fanqie_notify_admin:
        return 0
    full_message = _decorate_notice(message, group_id, user_id)
    sent = 0
    for admin_id in sorted(plugin_config.fanqie_admin_ids):
        try:
            await bot.send_private_msg(user_id=admin_id, message=full_message)
            sent += 1
        except ActionFailed:
            logger.warning("私聊通知失败 admin={}，回退群内发送", admin_id)
            if event is not None:
                try:
                    await bot.send_group_msg(
                        group_id=event.group_id,
                        message=(
                            Message(MessageSegment.at(admin_id)) + f" {full_message}"
                        ),
                    )
                    sent += 1
                except ActionFailed:
                    logger.warning("群内通知失败 admin={}", admin_id)
    return sent


def _decorate_notice(message: str, group_id: int, user_id: int) -> str:
    """为管理员通知附加群作者白名单与决策指引。"""
    from .policy import get_policy

    policy = get_policy()
    group = policy.group_policy(group_id)
    allowed = (
        ", ".join(sorted(group.author_names)) if group and group.authors else "未配置"
    )
    hours = max(1, plugin_config.fanqie_admin_decision_timeout // 3600)
    return (
        f"{message}\n"
        f"该群允许作者：{allowed}\n"
        f"请在群内执行：/kick {user_id} 或 /keep {user_id}。\n"
        f"若 {hours} 小时内未处理，将自动在群内通报并移出该成员。"
    )


def build_admin_notice(
    *,
    group_id: int,
    user_id: int,
    reader_name: str | None,
    book_name: str | None,
    author: str | None,
    rating: str | None,
    publish_time: str | None,
) -> str:
    """构造验证失败的管理员通知文本（不含决策指引，由通知装饰统一附加）。"""
    info = (
        f"读者={reader_name or '无'}"
        f"，书名={book_name or '无'}"
        f"，作者={author or '无'}"
        f"，评分={rating or '无'}"
        f"，发布={publish_time or '无'}"
    )
    return (
        f"【验证失败】群「{group_id}」新成员 {user_id} 未通过书评验证。"
        f"提取信息：{info}。"
    )


__all__ = [
    "MemberInfo",
    "_is_admin",
    "announce_admin_timeout",
    "announce_kick_reminder",
    "announce_member_timeout",
    "build_admin_notice",
    "get_member_info",
    "kick_member",
    "notify_admins",
    "send_guide",
    "send_welcome",
]
