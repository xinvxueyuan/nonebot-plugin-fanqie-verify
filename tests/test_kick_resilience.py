"""踢人容灾测试：网络异常捕获、失败状态、重试与重连补偿。

覆盖「掉线 / 重启」场景下踢人逻辑的三个问题：

1. ``kick_member`` 只捕获 ``ActionFailed``，网络类异常会冒泡 —— 导致调用方
   的 ``store.end(...)`` 不执行，会话永久卡在 ``awaiting_admin``。
2. 踢人失败时仍被标记 ``kicked``，状态与实际不一致。
3. bot 不在线（``_get_bot`` 返回 ``None``）或踢人失败后没有补偿路径。

"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
    actions as actions_module,
    flow as flow_module,
    get_session_store,
    handle_admin_decision_timeout,
    start_verification,
)


class KickBot:
    """可配置踢人结果与成员是否在群的假 Bot。"""

    self_id = "bot1"

    def __init__(self, *, fail_times: int = 0, in_group: bool = True) -> None:
        self.fail_times = fail_times
        self.in_group = in_group
        self.kick_attempts = 0
        self.calls: list[Any] = []

    async def send_group_msg(self, **kwargs: Any) -> None:
        self.calls.append(("send_group_msg", kwargs))

    async def get_group_member_info(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_group_member_info", kwargs))
        if not self.in_group:
            from nonebot.adapters.onebot.v11.exception import ActionFailed

            raise ActionFailed(retcode=100, retmsg="member not found", data=None)
        return {
            "user_id": kwargs["user_id"],
            "role": "member",
            "card": "",
            "nickname": "某用户",
            "shut_up_timestamp": 0,
        }

    async def set_group_kick(self, **kwargs: Any) -> None:
        self.kick_attempts += 1
        self.calls.append(("set_group_kick", kwargs))
        if self.kick_attempts <= self.fail_times:
            # 模拟掉线 / 连接中断：抛非 ActionFailed 的异常。
            raise RuntimeError("connection closed")


@pytest.fixture(autouse=True)
def _fresh_store() -> Generator[None]:
    """每个测试用全新的会话存储单例，避免跨用例污染。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import session

    before = session._store
    session._store = None
    try:
        yield
    finally:
        session._store = before


@pytest.fixture(autouse=True)
def _lenient_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """让测试群 123 通过策略检查（配置群节点后才执行验证）。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="阿百川大鬼"),),
                ),
            },
        ),
    )


async def _to_awaiting_admin(bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """把成员推进到 awaiting_admin，并把 ``_get_bot`` 指向给定 bot。"""

    async def fake_get_bot(bot_id: str) -> Any:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    await start_verification(bot, group_id=123, user_id=10001)
    get_session_store().await_admin("123", "10001")


@pytest.mark.asyncio
async def test_kick_member_swallows_network_error() -> None:
    """网络类异常不应冒泡，否则调用方的状态落库会被跳过。"""
    bot: Any = KickBot(fail_times=1)

    assert await actions_module.kick_member(bot, 555, 777) is False


@pytest.mark.asyncio
async def test_kick_member_returns_true_when_member_gone() -> None:
    """成员已不在群视为达成目标状态（回归守卫）。"""
    bot: Any = KickBot(in_group=False)

    assert await actions_module.kick_member(bot, 555, 777) is True


@pytest.mark.asyncio
async def test_admin_timeout_kick_failure_marks_kick_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """踢人失败时会话应为 kick_pending，而不是误标 kicked。"""
    bot: Any = KickBot(fail_times=99)
    await _to_awaiting_admin(bot, monkeypatch)

    await handle_admin_decision_timeout("123", "10001")

    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "kick_pending", "踢人失败不应标记为 kicked"


@pytest.mark.asyncio
async def test_admin_timeout_kick_success_marks_kicked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """踢人成功时仍应正常结束为 kicked（对照）。"""
    bot: Any = KickBot(fail_times=0)
    await _to_awaiting_admin(bot, monkeypatch)

    await handle_admin_decision_timeout("123", "10001")

    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "kicked"
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert len(kicks) == 1


@pytest.mark.asyncio
async def test_admin_timeout_bot_offline_marks_kick_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bot 不在线时不能直接放过成员，应记 kick_pending 等重连补踢。"""

    async def no_bot(bot_id: str) -> None:
        _ = bot_id

    monkeypatch.setattr(flow_module, "_get_bot", no_bot)
    bot: Any = KickBot()
    await start_verification(bot, group_id=123, user_id=10001)
    get_session_store().await_admin("123", "10001")

    await handle_admin_decision_timeout("123", "10001")

    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "kick_pending"


@pytest.mark.asyncio
async def test_retry_pending_kicks_kicks_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重连补偿：kick_pending 的成员应被补踢并转为 kicked。"""
    bot: Any = KickBot(fail_times=0)
    await _to_awaiting_admin(bot, monkeypatch)
    # 先制造一次失败，把会话推进到 kick_pending。
    failing: Any = KickBot(fail_times=99)

    async def failing_bot(bot_id: str) -> Any:
        _ = bot_id
        return failing

    monkeypatch.setattr(flow_module, "_get_bot", failing_bot)
    await handle_admin_decision_timeout("123", "10001")
    pending = get_session_store().get("123", "10001")
    assert pending is not None
    assert pending.status == "kick_pending"

    # 重连后补踢
    kicked = await flow_module.retry_pending_kicks(bot)

    assert kicked == 1
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "kicked"


@pytest.mark.asyncio
async def test_retry_pending_kicks_skips_when_member_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重连补偿：成员已不在群时直接完结为 kicked，不报错。"""
    bot: Any = KickBot(fail_times=99)
    await _to_awaiting_admin(bot, monkeypatch)
    await handle_admin_decision_timeout("123", "10001")
    pending = get_session_store().get("123", "10001")
    assert pending is not None
    assert pending.status == "kick_pending"

    # 成员已退群
    gone: Any = KickBot(fail_times=0, in_group=False)
    kicked = await flow_module.retry_pending_kicks(gone)

    assert kicked == 1
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "kicked"


@pytest.mark.asyncio
async def test_retry_pending_kicks_noop_when_none_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有待补踢成员时返回 0。"""
    bot: Any = KickBot()
    _ = monkeypatch

    assert await flow_module.retry_pending_kicks(bot) == 0


@pytest.mark.asyncio
async def test_retry_pending_kicks_once_finds_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试循环无需调用方提供 bot：自行按记录的 bot_id 查找。"""
    failing: Any = KickBot(fail_times=99)
    await _to_awaiting_admin(failing, monkeypatch)
    await handle_admin_decision_timeout("123", "10001")
    pending = get_session_store().get("123", "10001")
    assert pending is not None
    assert pending.status == "kick_pending"

    recovered: Any = KickBot(fail_times=0)

    async def recovered_bot(bot_id: str) -> Any:
        _ = bot_id
        return recovered

    monkeypatch.setattr(flow_module, "_get_bot", recovered_bot)

    assert await flow_module.retry_pending_kicks_once() == 1
    after = get_session_store().get("123", "10001")
    assert after is not None
    assert after.status == "kicked"


@pytest.mark.asyncio
async def test_schedule_kick_retry_gives_up_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试用尽仍失败时不抛异常，会话保持 kick_pending 等待重连补偿。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config

    failing: Any = KickBot(fail_times=99)
    await _to_awaiting_admin(failing, monkeypatch)
    await handle_admin_decision_timeout("123", "10001")

    monkeypatch.setattr(plugin_config, "fanqie_kick_retry_times", 1)
    monkeypatch.setattr(plugin_config, "fanqie_kick_retry_delay", 1)
    # 后台循环里调用的补踢仍是同一个失败的 bot
    monkeypatch.setattr(flow_module, "retry_pending_kicks_once", _noop_retry)

    await flow_module.schedule_kick_retry()

    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "kick_pending"


async def _noop_retry() -> int:
    """供重试用尽的用例使用：模拟一轮什么都没踢成。"""
    return 0
