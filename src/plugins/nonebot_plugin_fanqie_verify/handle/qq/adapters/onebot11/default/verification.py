"""OneBot V11 适配器处理器注册（参照对象项目的 selected_adapter_handle 模式）。"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import (
    Bot as OneBot11Bot,
    GroupAdminNoticeEvent,
    GroupBanNoticeEvent,
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
)
from nonebot.adapters.onebot.v11.event import (
    MessageEvent,
    PrivateMessageEvent as OneBot11PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ......handle.qq.commands.verification import (
    _is_sticker,
    approve_cmd,
    backfill_cmd,
    backfill_confirm_cmd,
    extend_cmd,
    group_admin_change,
    group_ban,
    group_decrease,
    group_increase,
    image_submission,
    keep_cmd,
    kick_cmd,
    pending_list_cmd,
    private_image_submission,
    processing_cmd,
    reload_config_cmd,
    review_cmd,
    verify_cmd,
    whitelist_cmd,
)
from ......services.verification import (
    PolicyConfigError,
    admin_decision,
    get_policy,
    get_session_store,
    handle_bot_left_group,
    handle_member_left,
    handle_private_submission,
    handle_submission,
    reload_policy,
    review_verification,
    start_verification,
)

_SECONDS_PER_HOUR = 3600
_MINUTES_PER_HOUR = 60
#: 补验命令里「小时数」与「QQ 号」的分界：小于该值视为小时数。
_BACKFILL_MIN_QQ = 10000
#: 补验扫描窗口的允许上限（小时）。
_BACKFILL_MAX_HOURS = 720
#: 延期时长的下限（小时）：小于该值视为非法参数。
_EXTEND_MIN_HOURS = 1


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """把可能 naive 的 datetime 归一化为 aware（UTC）；None 原样返回。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _register[T: Callable[..., Awaitable[Any]]](
    matcher: type[Matcher],
) -> Callable[[T], T]:
    """返回注册装饰器，把处理函数挂到给定 matcher（本插件仅支持 onebot.v11）。"""

    def decorator(func: T) -> T:
        matcher.handle()(wrapped(func))
        return func

    return decorator


def wrapped[T: Callable[..., Awaitable[Any]]](func: T) -> T:
    """保留函数签名并记录处理异常。"""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except Exception:
            logger.exception("验证处理器异常: %s", func.__name__)
            return None

    return wrapper  # type: ignore[return-value]


def _image_url(event: MessageEvent) -> str | None:
    """从群消息中提取首张图片的 URL（排除表情包）；无 URL 时回退到 file。"""
    for segment in event.message:
        if segment.type != "image" or _is_sticker(segment):
            continue  # 非图片段或表情包，跳过
        data = segment.data
        url = data.get("url")
        if url:
            return str(url)
        file = data.get("file")
        if file:
            return f"file://{file}" if not file.startswith("file://") else str(file)
    return None


@_register(group_increase)
async def on_group_increase(
    bot: OneBot11Bot,
    event: GroupIncreaseNoticeEvent,
) -> None:
    """FR1：新成员入群，开启验证流程。"""
    await start_verification(bot, group_id=event.group_id, user_id=event.user_id)


@_register(group_decrease)
async def on_group_decrease(
    bot: OneBot11Bot,
    event: GroupDecreaseNoticeEvent,
) -> None:
    """PRD 10：成员退群/被踢时终止遗留会话（必须落库终态）。

    三种情形：

    - ``kick_me``：机器人自己被移出 → 终止该群全部会话。
    - ``leave`` 且 ``user_id == self_id``：机器人主动退群（LLBot 用 leave
      上报）→ 同样终止该群全部会话。
    - 其余：仅终止该成员的会话；``leave`` 记 ``left_group``、``kick``
      记 ``kicked``，并在事件里标记是否为机器人踢出。

    注意 nonebot 的 OneBot11 适配器没有 ``group_dismiss`` 事件类，群解散
    无法直接收到；解散时通常伴随 ``kick_me``，可被上面第一条覆盖。

    """
    _ = bot
    if event.sub_type == "kick_me" or event.user_id == event.self_id:
        cleared = await handle_bot_left_group(group_id=str(event.group_id))
        if cleared:
            logger.info(
                "机器人已离开群 %s（%s），终止 %s 个会话",
                event.group_id,
                event.sub_type,
                cleared,
            )
        return
    await handle_member_left(
        group_id=str(event.group_id),
        user_id=str(event.user_id),
        sub_type=event.sub_type,
        operator_id=event.operator_id,
    )


@_register(group_admin_change)
async def on_group_admin_change(
    event: GroupAdminNoticeEvent,
) -> None:
    """群管理员变动：被设为管理员的新成员不再需要验证。"""
    store = get_session_store()
    record = store.get(str(event.group_id), str(event.user_id))
    if record is None or record.status != "waiting":
        return
    if event.sub_type == "set":
        store.end(str(event.group_id), str(event.user_id), status="approved")
        logger.info(
            "成员 %s 在群 %s 被设为管理员，直接放行",
            event.user_id,
            event.group_id,
        )


@_register(group_ban)
async def on_group_ban(
    event: GroupBanNoticeEvent,
) -> None:
    """群禁言事件：同步会话禁言状态。"""
    store = get_session_store()

    if event.sub_type == "ban":
        record = store.get(str(event.group_id), str(event.user_id))
        if record is not None:
            store.set_muted(str(event.group_id), str(event.user_id), is_muted=True)
            logger.debug(
                "成员 %s 在群 %s 被禁言（时长 %s 秒）",
                event.user_id,
                event.group_id,
                event.duration,
            )
    elif event.sub_type == "lift_ban":
        record = store.get(str(event.group_id), str(event.user_id))
        if record is not None:
            store.set_muted(str(event.group_id), str(event.user_id), is_muted=False)


@_register(image_submission)
async def on_image_submission(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> None:
    """FR2：处理待验证成员的阅读截图。

    由于不使用自定义 Rule（见 commands 模块注释），此处自行判断
    消息来源是否处于待验证状态且包含图片；不满足时直接返回。

    """
    from ......handle.qq.commands.verification import (
        _contains_image,
        _has_pending_session,
    )

    if not _has_pending_session(event) or not _contains_image(event):
        return
    reply = await handle_submission(
        bot,
        group_id=event.group_id,
        user_id=event.user_id,
        image_url=_image_url(event),
        reply_message_id=event.message_id,
    )
    message = (
        MessageSegment.reply(event.message_id)
        + MessageSegment.at(event.user_id)
        + f" {reply}"
    )
    await bot.send_group_msg(group_id=event.group_id, message=message)


@_register(private_image_submission)
async def on_private_image_submission(
    bot: OneBot11Bot,
    event: OneBot11PrivateMessageEvent,
) -> None:
    """私聊验证：处理用户私聊机器人的待验证群截图。

    仅在该用户**确实存在待验证（waiting）会话**时才介入；否则静默返回，
    不回复任何消息，避免干扰用户与其他插件的正常互动。

    仅响应带图片的私聊消息；纯文本（如选群命令）交给 verify_cmd 处理。
    """
    from ......core.config import plugin_config

    if not plugin_config.fanqie_private_verify_enabled:
        return
    from ......handle.qq.commands.verification import _contains_image

    if not _contains_image(event):
        return
    store = get_session_store()
    if not store.list_waiting_by_user(str(event.user_id)):
        return
    reply = await handle_private_submission(
        bot,
        user_id=int(event.user_id),
        image_url=_image_url(event),
    )
    await bot.send_private_msg(user_id=int(event.user_id), message=reply)


@_register(verify_cmd)
async def on_verify_select(
    bot: OneBot11Bot,
    event: OneBot11PrivateMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """私聊选群：多群待验证时用户用「验证 <群号>」指定目标群。"""
    from ......core.config import plugin_config

    if not plugin_config.fanqie_private_verify_enabled:
        return
    text = args.extract_plain_text().strip()
    if not text:
        await bot.send_private_msg(
            user_id=int(event.user_id),
            message="请提供群号，例如：验证 123456",
        )
        return
    try:
        group_id = int(text)
    except ValueError:
        await bot.send_private_msg(
            user_id=int(event.user_id),
            message="群号无效，请提供数字群号，例如：验证 123456",
        )
        return
    store = get_session_store()
    waiting = store.list_waiting_by_user(str(event.user_id))
    if not any(r.group_id == str(group_id) for r in waiting):
        await bot.send_private_msg(
            user_id=int(event.user_id),
            message=f"群 {group_id} 不在你当前待验证的群列表中。",
        )
        return
    store.set_private_target(str(event.user_id), str(group_id))
    await bot.send_private_msg(
        user_id=int(event.user_id),
        message=f"已选择在群 {group_id} 验证，请发送书评详情页截图。",
    )


@_register(kick_cmd)
async def on_admin_kick(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """FR9：管理员踢出指定成员。"""
    from ......core.config import plugin_config

    if plugin_config.fanqie_allow_group_admin_commands:
        if not await _is_privileged(bot, event):
            return
    elif not _is_admin_user(event):
        return
    target_user_id = _extract_target_user(args, event)
    if target_user_id is None:
        hint = MessageSegment.at(event.user_id) + (
            " 请提供成员 QQ 号，例如：/kick 123456"
        )
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.reply(event.message_id) + hint,
        )
        return
    reply = await admin_decision(
        bot,
        group_id=event.group_id,
        user_id=target_user_id,
        keep=False,
        reply_message_id=event.message_id,
    )
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + reply,
    )


@_register(keep_cmd)
async def on_admin_keep(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """FR9：管理员保留指定成员。"""
    from ......core.config import plugin_config

    if plugin_config.fanqie_allow_group_admin_commands:
        if not await _is_privileged(bot, event):
            return
    elif not _is_admin_user(event):
        return
    target_user_id = _extract_target_user(args, event)
    if target_user_id is None:
        hint = MessageSegment.at(event.user_id) + (
            " 请提供成员 QQ 号，例如：/keep 123456"
        )
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.reply(event.message_id) + hint,
        )
        return
    reply = await admin_decision(
        bot,
        group_id=event.group_id,
        user_id=target_user_id,
        keep=True,
        reply_message_id=event.message_id,
    )
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + reply,
    )


@_register(approve_cmd)
async def on_admin_approve(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """管理员“插入直接批准”：对仍在验证流程中的新成员直接放行。"""
    if not _is_admin_user(event):
        return
    target_user_id = _extract_target_user(args, event)
    if target_user_id is None:
        hint = MessageSegment.at(event.user_id) + (
            " 请 @ 或提供成员 QQ 号，例如：通过 @成员  或  通过 123456"
        )
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.reply(event.message_id) + hint,
        )
        return
    reply = await admin_decision(
        bot,
        group_id=event.group_id,
        user_id=target_user_id,
        keep=True,
        reply_message_id=event.message_id,
    )
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + reply,
    )


def _is_admin_user(event: GroupMessageEvent) -> bool:
    """命令发起者是否为配置的管理员。"""
    from ......core.config import plugin_config

    try:
        return int(event.user_id) in plugin_config.fanqie_admin_ids
    except (TypeError, ValueError):
        return False


async def _is_privileged(bot: OneBot11Bot, event: GroupMessageEvent) -> bool:
    """命令发起者是否具备管理员权限（配置管理员或群内 admin/owner）。"""
    if _is_admin_user(event):
        return True
    from ......services.verification import get_member_info

    info = await get_member_info(bot, event.group_id, int(event.user_id))
    return bool(info and info.is_admin)


@_register(reload_config_cmd)
async def on_reload_config(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> None:
    """重载番茄 OCR 配置：放行策略 TOML 运行时热更新。"""
    if not _is_admin_user(event):
        return
    try:
        policy = reload_policy()
    except PolicyConfigError as exc:
        message = MessageSegment.at(event.user_id) + f" 配置重载失败：{exc}"
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.reply(event.message_id) + message,
        )
        return

    mode = "全部元素" if policy.require_all else "指定元素"
    total_authors = sum(len(group.authors) for group in policy.groups.values())
    summary = (
        f"番茄 OCR 配置已重载：放行模式={mode}，"
        f"监控群={len(policy.groups)} 个，"
        f"作者白名单={total_authors} 人。"
    )
    message = MessageSegment.at(event.user_id) + f" {summary}"
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + message,
    )


@_register(pending_list_cmd)
async def on_admin_pending_list(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> None:
    """查询本群等待管理员决策的成员列表。"""
    if not _is_admin_user(event):
        return
    from ......services.verification import get_session_store

    records = get_session_store().list_awaiting_admin(str(event.group_id))
    if not records:
        reply = "当前没有等待管理员处理的验证成员。"
    else:
        now = datetime.now(UTC)
        lines: list[str] = []
        for record in sorted(
            records,
            key=lambda r: _ensure_aware(r.expires_at) or now,
        ):
            exp = _ensure_aware(record.expires_at) or now
            remaining = max(0, int((exp - now).total_seconds()))
            hours, remainder = divmod(remaining, _SECONDS_PER_HOUR)
            minutes = (remainder + 59) // 60
            if minutes >= _MINUTES_PER_HOUR:
                hours += 1
                minutes = 0
            left = f"{hours} 小时 {minutes} 分" if hours else f"{minutes} 分"
            lines.append(f"QQ {record.user_id}（剩余 {left}，/keep 或 /kick）")
        reply = f"等待管理员决策的成员 {len(records)} 人：\n" + "\n".join(lines)
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + reply,
    )


@_register(processing_cmd)
async def on_processing_list(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> None:
    """查询本群正在等待提交截图的成员列表。"""
    if not _is_admin_user(event):
        return
    from ......services.verification import get_session_store

    records = get_session_store().list_waiting_by_group(str(event.group_id))
    if not records:
        reply = "当前没有等待提交截图的验证成员。"
    else:
        now = datetime.now(UTC)
        lines: list[str] = []
        for record in sorted(
            records,
            key=lambda r: _ensure_aware(r.expires_at) or now,
        ):
            exp = _ensure_aware(record.expires_at) or now
            remaining = max(0, int((exp - now).total_seconds()))
            minutes = (remaining + 59) // 60
            lines.append(f"QQ {record.user_id}（剩余 {minutes} 分，/keep 或 /kick）")
        reply = f"等待提交截图的成员 {len(records)} 人：\n" + "\n".join(lines)
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + reply,
    )


@_register(whitelist_cmd)
async def on_whitelist_view(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> None:
    """查看验证白名单：展示当前群已配置的作者与作品列表。"""
    if not _is_admin_user(event):
        return
    policy = get_policy()
    group = policy.group_policy(event.group_id)
    if group is None or not group.authors:
        reply = "本群未配置作者白名单（宽松验证，仅校验截图信息完整）。"
    else:
        lines: list[str] = []
        for entry in group.authors:
            books = "、".join(sorted(entry.books)) if entry.books else "（未配置作品）"
            lines.append(f"作者：{entry.name}\n  作品：{books}")
        reply = (
            f"群 {event.group_id} 验证白名单 {len(group.authors)} 位作者：\n"
            + "\n\n".join(lines)
        )
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + reply,
    )


@_register(review_cmd)
async def on_review(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """重审：普通成员重审自己（限次数），管理员可 @ 任意普通成员重审。

    - 普通成员（非群管理/群主）发送“重审”：重审自己，消耗一次重审机会；
    - 管理员发送“重审 @某人”：重审目标成员，不消耗次数、不受上限限制；
    - 管理员裸发“重审”（无 @）：提示需指定目标。
    """
    is_admin = await _is_privileged(bot, event)
    target_user_id = _extract_target_user(args, event)

    if target_user_id is None:
        if is_admin:
            reply = "请 @ 要重审的成员，例如：重审 @某人"
        else:
            target_user_id = int(event.user_id)
            reply = await review_verification(
                bot,
                group_id=event.group_id,
                user_id=target_user_id,
                triggered_by_admin=False,
            )
    elif not is_admin:
        reply = "只有管理员可以重审其他成员，你只能重审自己。"
    else:
        reply = await review_verification(
            bot,
            group_id=event.group_id,
            user_id=target_user_id,
            triggered_by_admin=True,
        )
    message = (
        MessageSegment.reply(event.message_id)
        + MessageSegment.at(event.user_id)
        + f" {reply}"
    )
    await bot.send_group_msg(group_id=event.group_id, message=message)


def _parse_backfill_args(
    args: Message,
    event: GroupMessageEvent,
    default_hours: int,
) -> tuple[int, list[int]]:
    """解析补验命令参数：返回 (扫描小时数, 指定成员 QQ 号列表)。

    纯数字参数按大小区分：小于 ``_BACKFILL_MIN_QQ`` 视为小时数，否则视为
    成员 QQ 号；``@成员`` 一律视为指定成员。

    """
    hours = max(1, default_hours)
    targets: list[int] = []
    for token in args.extract_plain_text().split():
        if not token.isdigit():
            continue
        value = int(token)
        if value >= _BACKFILL_MIN_QQ:
            targets.append(value)
        else:
            hours = min(max(1, value), _BACKFILL_MAX_HOURS)
    for segment in event.message:
        if segment.type != "at":
            continue
        qq = segment.data.get("qq")
        if qq is None or qq == "all":
            continue
        try:
            targets.append(int(qq))
        except (TypeError, ValueError):
            continue
    return hours, targets


async def _send_backfill_reply(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    reply: str,
) -> None:
    """发送补验相关回复（引用原命令消息）。"""
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + reply,
    )


@_register(backfill_cmd)
async def on_backfill(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """补验：把错过入群事件的成员补进验证流程。

    用于机器人掉线期间入群、未收到入群事件的成员。可带小时数窗口
    （如「补验 48」）或直接指定成员（「补验 @某人」/「补验 10001」）。
    """
    from ......core.config import plugin_config

    if not await _is_privileged(bot, event):
        return
    if not plugin_config.fanqie_backfill_enabled:
        await _send_backfill_reply(
            bot,
            event,
            "补验功能已停用（FANQIE_BACKFILL_ENABLED=false）。",
        )
        return
    from ......services.verification import backfill as backfill_module

    hours, targets = _parse_backfill_args(
        args,
        event,
        plugin_config.fanqie_backfill_default_hours,
    )
    candidates = await backfill_module.collect_candidates(
        bot,
        group_id=event.group_id,
        hours=hours,
        targets=targets or None,
    )
    if not candidates:
        await _send_backfill_reply(bot, event, "未发现需要补验的成员。")
        return
    if plugin_config.fanqie_backfill_confirm_first:
        get_session_store().set_pending_backfill(
            str(event.group_id),
            [candidate.user_id for candidate in candidates],
        )
        reply = (
            f"{backfill_module.format_candidate_list(event.group_id, candidates)}\n\n"
            "回复「补验确认」执行补验。"
        )
    else:
        started = await backfill_module.run_backfill(
            bot,
            group_id=event.group_id,
            candidates=candidates,
        )
        reply = (
            f"已为 {started} 名成员开启验证"
            "（已发送引导并开始计时，未通过将转管理员处理）。"
        )
    await _send_backfill_reply(bot, event, reply)


@_register(backfill_confirm_cmd)
async def on_backfill_confirm(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> None:
    """执行「补验」列出的候选名单（confirm_first 模式）。"""
    if not await _is_privileged(bot, event):
        return
    from ......services.verification import backfill as backfill_module

    user_ids = get_session_store().pop_pending_backfill(str(event.group_id))
    if not user_ids:
        await _send_backfill_reply(
            bot,
            event,
            "没有待确认的补验名单，请先发送「补验」。",
        )
        return
    candidates = await backfill_module.collect_candidates(
        bot,
        group_id=event.group_id,
        hours=1,
        targets=user_ids,
    )
    if not candidates:
        await _send_backfill_reply(bot, event, "名单中的成员已无需补验。")
        return
    started = await backfill_module.run_backfill(
        bot,
        group_id=event.group_id,
        candidates=candidates,
    )
    await _send_backfill_reply(bot, event, f"已为 {started} 名成员开启验证。")


def _extract_target_user(args: Message, event: GroupMessageEvent) -> int | None:
    """从命令参数或 @ 中解析目标成员 QQ 号。"""
    text = args.extract_plain_text().strip()
    if text:
        try:
            return int(text)
        except ValueError:
            return None
    for segment in event.message:
        if segment.type == "at":
            qq = segment.data.get("qq")
            if qq is not None and qq != "all":
                try:
                    return int(qq)
                except (TypeError, ValueError):
                    return None
    return None


def _format_remaining(expires_at: datetime | None, now: datetime) -> str:
    """把截止时间格式化为剩余时长的可读文本（如「6 小时」「1 小时 30 分」）。"""
    exp = _ensure_aware(expires_at) or now
    remaining = max(0, int((exp - now).total_seconds()))
    hours, remainder = divmod(remaining, _SECONDS_PER_HOUR)
    minutes = (remainder + 59) // 60
    if minutes >= _MINUTES_PER_HOUR:
        hours += 1
        minutes = 0
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分"


def _parse_extend_args(
    args: Message,
    event: GroupMessageEvent,
    default_hours: int,
    max_hours: int,
) -> tuple[int, list[int], bool]:
    """解析延期命令参数：返回 (小时数, 指定成员 QQ 号列表, 是否被上限裁剪)。

    纯数字参数按大小区分：小于 ``_BACKFILL_MIN_QQ`` 视为小时数，否则视为
    成员 QQ 号；``@成员`` 一律视为指定成员（``@机器人`` 自身除外）。小时数
    超过 ``max_hours`` 时按上限取值并标记已裁剪；显式给 0 小时视为非法
    （返回 0，由调用方提示）。

    Args:
        args: 命令参数。
        event: 群消息事件。
        default_hours: 不带时长参数时的默认小时数。
        max_hours: 单次延期的上限小时数。

    Returns:
        三元组：延期小时数（0 表示参数非法）、指定成员 QQ 号列表、是否被裁剪。

    """
    hours = max(_EXTEND_MIN_HOURS, default_hours)
    capped = False
    targets: list[int] = []
    for token in args.extract_plain_text().split():
        if not token.isdigit():
            continue
        value = int(token)
        if value >= _BACKFILL_MIN_QQ:
            targets.append(value)
        elif value >= _EXTEND_MIN_HOURS:
            if value > max_hours:
                hours, capped = max_hours, True
            else:
                hours = value
        else:
            hours = 0  # 延期 0 小时无意义，交由调用方提示
    targets.extend(_extract_at_users(event))
    return hours, targets, capped


def _extract_at_users(event: GroupMessageEvent) -> list[int]:
    """提取消息里 ``@`` 的成员 QQ 号（排除 ``@全体成员`` 与 ``@机器人`` 自身）。"""
    self_id = int(getattr(event, "self_id", 0) or 0)
    users: list[int] = []
    for segment in event.message:
        if segment.type != "at":
            continue
        qq = segment.data.get("qq")
        if qq is None or qq == "all":
            continue
        try:
            user_id = int(qq)
        except (TypeError, ValueError):
            continue
        if self_id and user_id == self_id:
            continue  # 排除 @机器人 自身
        users.append(user_id)
    return users


async def _send_extend_reply(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    reply: str,
) -> None:
    """发送延期相关回复（引用原命令消息）。"""
    await bot.send_group_msg(
        group_id=event.group_id,
        message=MessageSegment.reply(event.message_id) + reply,
    )


def _format_extend_result(
    results: list[Any],
    *,
    hours: int,
    capped: bool,
    max_hours: int,
    scoped_all: bool,
) -> str:
    """组装延期结果文案。"""
    now = datetime.now(UTC)
    lines = [
        f"QQ {record.user_id}（剩余 {_format_remaining(record.expires_at, now)}）"
        for record in results
    ]
    scope = "本群全部待审成员" if scoped_all else "指定成员"
    head = f"已为{scope}延期 {hours} 小时（从当前时间重新计时），共 {len(results)} 人"
    text = head + "：\n" + "\n".join(lines)
    if capped:
        text += f"\n注：单次延期上限 {max_hours} 小时，已按上限处理。"
    return text


async def _check_command_permission(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
) -> bool:
    """命令权限检查：允许群管理员时要求其具备管理权限，否则仅限配置管理员。"""
    from ......core.config import plugin_config

    if plugin_config.fanqie_allow_group_admin_commands:
        return await _is_privileged(bot, event)
    return _is_admin_user(event)


def _extend_targets(
    store: Any,
    group_id: str,
    targets: list[int],
    *,
    seconds: int,
) -> tuple[list[Any], list[str]]:
    """延期指定成员（按 QQ 号去重保序）。

    Returns:
        二元组：成功延期的会话记录列表、未延期（不在待管理员决策状态）的 QQ 号。

    """
    results: list[Any] = []
    skipped: list[str] = []
    for user_id in dict.fromkeys(targets):
        record = store.extend_awaiting(group_id, str(user_id), seconds=seconds)
        if record is None:
            skipped.append(str(user_id))
        else:
            results.append(record)
    return results, skipped


def _extend_all_awaiting(
    store: Any,
    group_id: str,
    records: tuple[Any, ...],
    *,
    seconds: int,
) -> list[Any]:
    """延期本群全部待管理员决策成员，返回成功延期的记录列表。"""
    results: list[Any] = []
    for record in records:
        updated = store.extend_awaiting(
            group_id,
            record.user_id,
            seconds=seconds,
        )
        if updated is not None:
            results.append(updated)
    return results


@_register(extend_cmd)
async def on_extend(
    bot: OneBot11Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    """延期：推迟「待管理员决策」成员的自动移出时间。

    带 ``@成员``/QQ 号时只延期指定成员，否则延期本群全部待审成员；不带
    时长时使用 ``FANQIE_EXTEND_DEFAULT_HOURS``，单次上限
    ``FANQIE_EXTEND_MAX_HOURS``（不限制累计次数）。
    """
    from ......core.config import plugin_config

    if not await _check_command_permission(bot, event):
        return
    if not plugin_config.fanqie_extend_enabled:
        await _send_extend_reply(
            bot,
            event,
            "延期功能已停用（FANQIE_EXTEND_ENABLED=false）。",
        )
        return
    max_hours = max(_EXTEND_MIN_HOURS, plugin_config.fanqie_extend_max_hours)
    hours, targets, capped = _parse_extend_args(
        args,
        event,
        plugin_config.fanqie_extend_default_hours,
        max_hours,
    )
    if hours < _EXTEND_MIN_HOURS:
        await _send_extend_reply(
            bot,
            event,
            f"延期时长需为不小于 {_EXTEND_MIN_HOURS} 的整数小时，"
            f"例如：延期 @成员 12（上限 {max_hours} 小时）",
        )
        return

    store = get_session_store()
    group_id = str(event.group_id)
    seconds = hours * _SECONDS_PER_HOUR
    if targets:
        results, skipped = _extend_targets(store, group_id, targets, seconds=seconds)
        if not results:
            await _send_extend_reply(
                bot,
                event,
                "指定成员均不在「待管理员决策」状态"
                "（可能已处理完毕，或仍在等待提交截图），未延期。",
            )
            return
        reply = _format_extend_result(
            results,
            hours=hours,
            capped=capped,
            max_hours=max_hours,
            scoped_all=False,
        )
        if skipped:
            reply += "\n未延期（不在待管理员决策状态）：" + "、".join(
                f"QQ {uid}" for uid in skipped
            )
    else:
        records = store.list_awaiting_admin(group_id)
        if not records:
            await _send_extend_reply(bot, event, "本群当前没有待管理员决策的成员。")
            return
        results = _extend_all_awaiting(store, group_id, records, seconds=seconds)
        if not results:
            await _send_extend_reply(bot, event, "没有可延期的成员。")
            return
        reply = _format_extend_result(
            results,
            hours=hours,
            capped=capped,
            max_hours=max_hours,
            scoped_all=True,
        )
    await _send_extend_reply(bot, event, reply)
