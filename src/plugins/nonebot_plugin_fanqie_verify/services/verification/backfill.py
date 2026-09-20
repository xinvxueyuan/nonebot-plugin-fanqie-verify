"""补验：把错过入群事件的成员补进验证流程。

LLBot（OneBot 实现）掉线期间，QQ 不会补发 ``group_increase`` 事件，导致这
期间入群的成员不会被验证。本模块提供三条途径把这些成员补进正常验证流程：

- ``collect_candidates``：按「入群时间在最近 N 小时内 + 数据库无验证记录」
  扫描候选，也支持管理员直接指定成员；
- ``run_backfill``：复用正常入群流程（``flow.start_verification``）逐个开启验证；
- ``handle_reconnect``：LLBot 重连时按配置提醒管理员或自动补验。

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from nonebot import logger
from nonebot_plugin_orm import get_session

from ...core.config import plugin_config
from ...database.models.session import VerificationSession
from ...database.orm_crud import list_items

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot

_ROLE_OWNER = "owner"
_ROLE_ADMIN = "admin"
# 单次读取的既有验证记录上限（够覆盖一个群的成员规模）。
_KNOWN_LIMIT = 5000
_SECONDS_PER_HOUR = 3600


@dataclass(frozen=True, slots=True)
class BackfillCandidate:
    """一个待补验的成员。

    Attributes:
        user_id: 成员 QQ 号。
        nickname: 群名片或昵称（用于名单展示）。
        join_time: 入群时间戳（秒）；无法获取时为 ``None``。

    """

    user_id: int
    nickname: str
    join_time: int | None


async def _known_user_ids(group_id: int) -> set[str]:
    """返回该群已有验证记录的成员 QQ 号集合。

    已有记录（无论最终通过与否）说明该成员此前进过验证流程，不属于
    「掉线期间被漏掉」的情况，因此不参与补验。

    Args:
        group_id: 群号。

    Returns:
        已有验证记录的成员 QQ 号集合；查询失败时返回空集合。

    """
    try:
        async with get_session() as session:
            rows = await list_items(
                session,
                VerificationSession,
                {"group_id": str(group_id)},
                limit=_KNOWN_LIMIT,
            )
    except Exception:  # noqa: BLE001 - 查询失败按「无记录」保守处理
        logger.exception("补验：读取群 {} 的验证记录失败", group_id)
        return set()
    return {row.user_id for row in rows}


def _member_user_id(member: dict[str, Any]) -> int | None:
    """从群成员信息里解析 QQ 号。"""
    raw = member.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_admin_role(member: dict[str, Any]) -> bool:
    """该成员是否为群主/管理员（无需验证）。"""
    return str(member.get("role") or "member") in (_ROLE_OWNER, _ROLE_ADMIN)


async def collect_candidates(
    bot: OneBot11Bot,
    *,
    group_id: int,
    hours: int,
    targets: list[int] | None = None,
) -> list[BackfillCandidate]:
    """收集需要补验的成员。

    候选条件：在群内、非机器人自身、非群主/管理员、数据库中无验证记录，
    并且满足以下之一——由 ``targets`` 直接指定，或入群时间落在最近
    ``hours`` 小时内。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        hours: 扫描窗口（小时），仅在未指定 ``targets`` 时用于筛选。
        targets: 管理员直接指定的成员 QQ 号；给定时忽略时间窗口。

    Returns:
        按入群时间升序排列的候选成员；拉取成员列表失败时返回空列表。

    """
    try:
        members = await bot.get_group_member_list(group_id=group_id)
    except Exception:  # noqa: BLE001 - 拉取失败时按「无候选」处理并记录
        logger.exception("补验：拉取群 {} 成员列表失败", group_id)
        return []
    if not isinstance(members, list):
        return []

    bot_id = int(getattr(bot, "self_id", 0) or 0)
    known = await _known_user_ids(group_id)
    target_set = set(targets) if targets else None
    cutoff = int(datetime.now(UTC).timestamp()) - max(1, hours) * _SECONDS_PER_HOUR

    candidates: list[BackfillCandidate] = []
    for raw in members:
        if not isinstance(raw, dict):
            continue
        user_id = _member_user_id(raw)
        if user_id is None or user_id == bot_id or _is_admin_role(raw):
            continue
        if str(user_id) in known:
            continue
        join_time = raw.get("join_time")
        if target_set is not None:
            if user_id not in target_set:
                continue
        elif not isinstance(join_time, int) or join_time < cutoff:
            continue
        candidates.append(
            BackfillCandidate(
                user_id=user_id,
                nickname=str(raw.get("card") or raw.get("nickname") or ""),
                join_time=join_time if isinstance(join_time, int) else None,
            )
        )
    candidates.sort(key=lambda candidate: candidate.join_time or 0)
    return candidates


async def run_backfill(
    bot: OneBot11Bot,
    *,
    group_id: int,
    candidates: list[BackfillCandidate],
    max_batch: int | None = None,
) -> int:
    """对候选成员逐个开启验证，返回成功开启的数量。

    复用正常入群流程（``flow.start_verification``）：发送引导、排超时，
    后续失败同样转管理员决策。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        candidates: 待补验成员。
        max_batch: 本次最多处理人数；``None`` 时取配置
            ``fanqie_backfill_max_batch``。

    Returns:
        成功开启验证会话的成员数量。

    """
    from . import flow

    limit = (
        max_batch if max_batch is not None else plugin_config.fanqie_backfill_max_batch
    )
    batch = candidates[: max(1, limit)]
    started = 0
    for candidate in batch:
        record = await flow.start_verification(
            bot,
            group_id=group_id,
            user_id=candidate.user_id,
        )
        if record is not None:
            started += 1
    return started


def monitored_group_ids() -> list[int]:
    """返回当前策略中受监控（配置了群节点）的群号列表。"""
    from . import policy

    return list(policy.get_policy().groups.keys())


def format_candidate_list(group_id: int, candidates: list[BackfillCandidate]) -> str:
    """把候选成员格式化为可读名单。

    Args:
        group_id: 群号。
        candidates: 候选成员。

    Returns:
        多行名单文本。

    """
    lines = [f"群 {group_id} 待补验成员 {len(candidates)} 人："]
    for candidate in candidates:
        name = f"（{candidate.nickname}）" if candidate.nickname else ""
        lines.append(f"QQ {candidate.user_id}{name}")
    return "\n".join(lines)


async def notify_group_pending(
    bot: OneBot11Bot,
    group_id: int,
    candidates: list[BackfillCandidate],
) -> None:
    """在群内提醒管理员存在漏验成员（不自动开启验证）。"""
    message = (
        f"检测到 {len(candidates)} 名成员可能漏过了入群验证"
        "（机器人掉线期间入群）。\n"
        "管理员可发送「补验」将其纳入验证流程。"
    )
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
    except Exception:  # noqa: BLE001 - 提醒失败不影响主流程
        logger.warning("补验：群 {} 提醒管理员失败", group_id)


async def notify_group_backfilled(
    bot: OneBot11Bot,
    group_id: int,
    started: int,
) -> None:
    """在群内通报已自动补验的成员数量。"""
    if started <= 0:
        return
    message = f"已自动为 {started} 名漏验成员开启验证，请相关成员按提示提交书评截图。"
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
    except Exception:  # noqa: BLE001 - 通报失败不影响主流程
        logger.warning("补验：群 {} 通报失败", group_id)


async def handle_reconnect(bot: OneBot11Bot) -> None:
    """机器人重连时按配置处理漏验成员。

    ``fanqie_backfill_reconnect_mode`` 取值：``notify``（默认，仅群内提醒
    管理员）/ ``auto``（自动开启验证）/ ``off``（不做任何事）。补验总开关
    ``fanqie_backfill_enabled`` 为 False 时不处理。

    Args:
        bot: 刚重连的 Bot 实例。

    """
    mode = str(plugin_config.fanqie_backfill_reconnect_mode or "").strip().lower()
    if not plugin_config.fanqie_backfill_enabled or mode == "off":
        return
    if mode not in ("auto", "notify"):
        logger.warning("补验：未知的重连模式 {}，按 notify 处理", mode)
        mode = "notify"

    hours = max(1, plugin_config.fanqie_backfill_default_hours)
    for group_id in monitored_group_ids():
        candidates = await collect_candidates(bot, group_id=group_id, hours=hours)
        if not candidates:
            continue
        logger.info(
            "补验（重连模式 {}）：群 {} 发现 {} 名漏验成员",
            mode,
            group_id,
            len(candidates),
        )
        if mode == "auto":
            started = await run_backfill(bot, group_id=group_id, candidates=candidates)
            await notify_group_backfilled(bot, group_id, started)
        else:
            await notify_group_pending(bot, group_id, candidates)


__all__ = [
    "BackfillCandidate",
    "collect_candidates",
    "format_candidate_list",
    "handle_reconnect",
    "monitored_group_ids",
    "notify_group_backfilled",
    "notify_group_pending",
    "run_backfill",
]
