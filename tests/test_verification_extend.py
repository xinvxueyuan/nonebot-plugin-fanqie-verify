"""「延期」功能测试：推迟「待管理员决策」成员的自动移出时间。

覆盖三层：

- ``SessionStore.extend_awaiting`` 的语义（仅 awaiting_admin 可延期、从当前
  时间重新计时、不累加、重排超时任务、保留状态与 trace_id）；
- ``_parse_extend_args`` 的参数解析（小时数与 QQ 号区分、@成员、排除
  @机器人、上限裁剪、0 小时非法）；
- 「延期」命令的端到端行为（全群 / 指定成员 / 无待审成员 / 停用开关）。

"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
import time
from typing import Any

from nonebug import App
import pytest

from src.plugins.nonebot_plugin_fanqie_verify.handle.qq.adapters.onebot11.default import (
    verification as adapter_module,
)
from src.plugins.nonebot_plugin_fanqie_verify.handle.qq.commands import (
    verification as cmd_module,
)

_SELF_ID = 12345
_GROUP_ID = 123
_ADMIN_ID = 1330509996
_STORE_KEY = (str(_GROUP_ID), "10001")


@pytest.fixture(autouse=True)
def _fresh_store() -> Generator[None]:
    """每个测试使用独立的会话存储。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        session as session_module,
    )

    before = session_module._store
    session_module._store = None
    try:
        yield
    finally:
        session_module._store = before


def _new_store() -> Any:
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification.session import (
        SessionStore,
    )

    return SessionStore()


def _start(store: Any, user: str = "10001") -> None:
    """开启一个 waiting 会话。"""
    store.start(
        group_id=str(_GROUP_ID),
        user_id=user,
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )


def _start_awaiting(store: Any, user: str = "10001") -> Any:
    """开启会话并转入待管理员决策状态。"""
    _start(store, user)
    return store.await_admin(str(_GROUP_ID), user)


def _args(text: str) -> Any:
    """构造命令参数（Message）。"""
    from nonebot.adapters.onebot.v11.message import Message

    return Message(text) if text else Message()


def _event(raw: str, *, at: int | None = None, message_text: str = "") -> Any:
    """构造群消息事件（可附带一个 @成员）。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent
    from nonebot.adapters.onebot.v11.message import Message, MessageSegment

    segments: list[Any] = [MessageSegment.text(message_text or raw)]
    if at is not None:
        segments.append(MessageSegment.at(at))
    return GroupMessageEvent(
        time=int(time.time()),
        self_id=_SELF_ID,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        group_id=_GROUP_ID,
        user_id=_ADMIN_ID,
        anonymous=None,
        sender={"user_id": _ADMIN_ID, "nickname": "owner", "role": "owner"},
        raw_message=raw,
        message=Message(segments),
        font=0,
    )  # type: ignore[call-arg]


# ---------------------------------------------------------------- store 语义


@pytest.mark.asyncio
async def test_extend_pushes_deadline_from_now() -> None:
    """延期把截止时间改到「当前时间 + 时长」，而非在原截止上累加。"""
    store = _new_store()
    _start_awaiting(store)

    before = datetime.now(UTC)
    updated = store.extend_awaiting(str(_GROUP_ID), "10001", seconds=6 * 3600)

    assert updated is not None
    delta = (updated.expires_at - before).total_seconds()
    assert 6 * 3600 - 5 <= delta <= 6 * 3600 + 5
    store.close()


@pytest.mark.asyncio
async def test_extend_is_not_cumulative() -> None:
    """连续两次延期各按 6 小时算（不叠加成 12 小时）。"""
    store = _new_store()
    _start_awaiting(store)

    store.extend_awaiting(str(_GROUP_ID), "10001", seconds=6 * 3600)
    before = datetime.now(UTC)
    second = store.extend_awaiting(str(_GROUP_ID), "10001", seconds=6 * 3600)

    assert second is not None
    delta = (second.expires_at - before).total_seconds()
    assert delta <= 6 * 3600 + 5
    store.close()


@pytest.mark.asyncio
async def test_extend_rejects_waiting_status() -> None:
    """Waiting 状态不可延期（延期只作用于审核阶段）。"""
    store = _new_store()
    _start(store)

    assert store.extend_awaiting(str(_GROUP_ID), "10001", seconds=3600) is None
    store.close()


@pytest.mark.asyncio
async def test_extend_missing_session_returns_none() -> None:
    """会话不存在时返回 None。"""
    store = _new_store()

    assert store.extend_awaiting(str(_GROUP_ID), "99999", seconds=3600) is None
    store.close()


@pytest.mark.asyncio
async def test_extend_reschedules_timeout_task() -> None:
    """延期取消旧超时任务并新建（按新截止时间计时）。"""
    store = _new_store()
    _start_awaiting(store)
    old_task = store._timeout_tasks[_STORE_KEY]

    store.extend_awaiting(str(_GROUP_ID), "10001", seconds=6 * 3600)

    new_task = store._timeout_tasks[_STORE_KEY]
    assert new_task is not old_task
    await asyncio.sleep(0)
    assert old_task.cancelled() or old_task.done()
    store.close()


@pytest.mark.asyncio
async def test_extend_keeps_status_and_trace() -> None:
    """延期不改变状态与 trace_id（仍是同一次验证流程）。"""
    store = _new_store()
    record = _start_awaiting(store)

    updated = store.extend_awaiting(str(_GROUP_ID), "10001", seconds=3600)

    assert updated is not None
    assert updated.status == "awaiting_admin"
    assert updated.trace_id == record.trace_id
    store.close()


@pytest.mark.asyncio
async def test_extend_then_admin_timeout_fires() -> None:
    """延期后到新截止时间仍会触发管理员超时回调（不会因重排而丢失）。"""
    store = _new_store()
    fired: list[tuple[str, str]] = []

    async def callback(group_id: str, user_id: str) -> None:
        fired.append((group_id, user_id))

    store.set_admin_timeout_callback(callback)
    _start_awaiting(store)
    store.extend_awaiting(str(_GROUP_ID), "10001", seconds=6 * 3600)

    await store._run_timeout(_STORE_KEY, 0.0, "awaiting_admin")

    assert fired == [(str(_GROUP_ID), "10001")]
    store.close()


# ------------------------------------------------------------ 参数解析


def test_parse_defaults_to_configured_hours() -> None:
    """不带参数时用默认小时数，且不指定成员。"""
    hours, targets, capped = adapter_module._parse_extend_args(
        _args(""), _event("/延期"), 6, 48
    )
    assert hours == 6
    assert targets == []
    assert capped is False


def test_parse_small_number_is_hours() -> None:
    """小于 10000 的数字按小时数处理。"""
    hours, targets, _ = adapter_module._parse_extend_args(
        _args("12"), _event("/延期 12"), 6, 48
    )
    assert hours == 12
    assert targets == []


def test_parse_large_number_is_member_qq() -> None:
    """不小于 10000 的数字按成员 QQ 号处理（时长仍用默认值）。"""
    hours, targets, _ = adapter_module._parse_extend_args(
        _args("10001"), _event("/延期 10001"), 6, 48
    )
    assert hours == 6
    assert targets == [10001]


def test_parse_caps_hours_at_max() -> None:
    """超过单次上限时按上限取值并标记裁剪。"""
    hours, _, capped = adapter_module._parse_extend_args(
        _args("100"), _event("/延期 100"), 6, 48
    )
    assert hours == 48
    assert capped is True


def test_parse_zero_hours_is_invalid() -> None:
    """「延期 0」视为非法参数（返回 0 由调用方提示）。"""
    hours, _, _ = adapter_module._parse_extend_args(
        _args("0"), _event("/延期 0"), 6, 48
    )
    assert hours == 0


def test_parse_at_member_becomes_target() -> None:
    """@成员 视为指定成员。"""
    _, targets, _ = adapter_module._parse_extend_args(
        _args(""), _event("/延期", at=10001), 6, 48
    )
    assert targets == [10001]


def test_parse_excludes_bot_self_from_at() -> None:
    """@机器人 自身不算目标成员。"""
    _, targets, _ = adapter_module._parse_extend_args(
        _args(""), _event("/延期", at=_SELF_ID), 6, 48
    )
    assert targets == []


def test_parse_combines_at_and_qq() -> None:
    """@成员 与 QQ 号可同时给出（QQ 去重保序）。"""
    _, targets, _ = adapter_module._parse_extend_args(
        _args("20001"), _event("/延期 20001", at=10001), 6, 48
    )
    assert targets == [20001, 10001]


# ------------------------------------------------------------ 命令行为


@pytest.mark.asyncio
async def test_extend_cmd_extends_all_awaiting(app: App) -> None:
    """不带成员时延期本群全部待审成员。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        get_session_store,
    )

    store = get_session_store()
    for user in ("10001", "20001"):
        _start(store, user)
        store.await_admin(str(_GROUP_ID), user)

    async with app.test_matcher(cmd_module.extend_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=_ADMIN_ID,
            anonymous=None,
            sender={"user_id": _ADMIN_ID, "nickname": "owner", "role": "owner"},
            raw_message="/延期",
            message=Message([MessageSegment.text("/延期")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1)
                    + "已为本群全部待审成员延期 6 小时（从当前时间重新计时），共 2 人：\n"
                    "QQ 10001（剩余 6 小时）\n"
                    "QQ 20001（剩余 6 小时）"
                ),
            },
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_extend_cmd_extends_specified_member(app: App) -> None:
    """带 @成员 与小时数时只延期该成员。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        get_session_store,
    )

    store = get_session_store()
    for user in ("10001", "20001"):
        _start(store, user)
        store.await_admin(str(_GROUP_ID), user)

    async with app.test_matcher(cmd_module.extend_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=_ADMIN_ID,
            anonymous=None,
            sender={"user_id": _ADMIN_ID, "nickname": "owner", "role": "owner"},
            raw_message="/延期 3",
            message=Message([MessageSegment.text("/延期 3"), MessageSegment.at(10001)]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1)
                    + "已为指定成员延期 3 小时（从当前时间重新计时），共 1 人：\n"
                    "QQ 10001（剩余 3 小时）"
                ),
            },
        )
        ctx.receive_event(bot, event)

    # 另一名成员未被延期，仍是 16 小时的默认窗口
    other = store.get(str(_GROUP_ID), "20001")
    assert other is not None
    remaining = (other.expires_at - datetime.now(UTC)).total_seconds()
    assert remaining > 15 * 3600


@pytest.mark.asyncio
async def test_extend_cmd_without_awaiting(app: App) -> None:
    """本群无待审成员时给出提示。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    async with app.test_matcher(cmd_module.extend_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=_ADMIN_ID,
            anonymous=None,
            sender={"user_id": _ADMIN_ID, "nickname": "owner", "role": "owner"},
            raw_message="/延期",
            message=Message([MessageSegment.text("/延期")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1) + "本群当前没有待管理员决策的成员。"
                ),
            },
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_extend_cmd_disabled_switch(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """功能停用时提示且不延期。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        get_session_store,
    )

    monkeypatch.setattr(plugin_config, "fanqie_extend_enabled", False)
    store = get_session_store()
    _start_awaiting(store)

    async with app.test_matcher(cmd_module.extend_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=_ADMIN_ID,
            anonymous=None,
            sender={"user_id": _ADMIN_ID, "nickname": "owner", "role": "owner"},
            raw_message="/延期",
            message=Message([MessageSegment.text("/延期")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1)
                    + "延期功能已停用（FANQIE_EXTEND_ENABLED=false）。"
                ),
            },
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_extend_cmd_caps_and_reports(app: App) -> None:
    """请求超过单次上限时按上限延期并在回复中说明。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        get_session_store,
    )

    store = get_session_store()
    _start_awaiting(store)

    async with app.test_matcher(cmd_module.extend_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=_ADMIN_ID,
            anonymous=None,
            sender={"user_id": _ADMIN_ID, "nickname": "owner", "role": "owner"},
            raw_message="/延期 100",
            message=Message([MessageSegment.text("/延期 100")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1)
                    + "已为本群全部待审成员延期 48 小时（从当前时间重新计时），共 1 人：\n"
                    "QQ 10001（剩余 48 小时）\n"
                    "注：单次延期上限 48 小时，已按上限处理。"
                ),
            },
        )
        ctx.receive_event(bot, event)
