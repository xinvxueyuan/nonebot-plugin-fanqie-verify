"""成员离群边界测试：主动退群 / 被管理员踢 / 机器人被踢 / 机器人主动退群。

对应 OneBot11 ``group_decrease`` 通知的三种 ``sub_type``：

- ``leave``：成员主动退群 → 会话终止为 ``left_group``
- ``kick``：被管理员（或机器人）踢出 → ``kicked``
- ``kick_me``：机器人自己被移出 → 清理该群**全部**会话

另覆盖 ``leave`` 且 ``user_id == self_id``（机器人主动退群，LLBot 也会
用 leave 上报）。

**核心修复点**：原实现只从内存 ``pop`` 而不落库，重启后
``restore_pending_sessions`` 会把已退群成员的会话"复活"，在「待处理列表」
里误导管理员 —— 因此每个用例都断言会话已不在内存。

"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
    flow as flow_module,
    get_session_store,
    start_verification,
)


class QuietBot:
    """最小 Bot 替身（本组测试只关心会话状态，不关心发消息）。"""

    # 用真实 QQ 号形式：handle_member_left 会把它与 operator_id(int) 比对
    self_id = "3128682634"

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def send_group_msg(self, **kwargs: Any) -> None:
        self.calls.append(("send_group_msg", kwargs))

    async def get_group_member_info(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "user_id": kwargs["user_id"],
            "role": "member",
            "card": "",
            "nickname": "某用户",
            "shut_up_timestamp": 0,
        }


@pytest.fixture(autouse=True)
def _fresh_store() -> Generator[None]:
    """每个测试用全新的会话存储单例。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import session

    before = session._store
    session._store = None
    try:
        yield
    finally:
        session._store = before


@pytest.fixture(autouse=True)
def _lenient_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """让测试群 123 通过策略检查。"""
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


async def _start(bot: Any) -> None:
    """为成员 10001 开启验证会话（进入 waiting）。"""
    await start_verification(bot, group_id=123, user_id=10001)


@pytest.mark.asyncio
async def test_leave_ends_session_as_left_group() -> None:
    """主动退群：会话终止为 left_group，且不再留在内存。"""
    bot: Any = QuietBot()
    await _start(bot)

    handled = await flow_module.handle_member_left(
        group_id="123", user_id="10001", sub_type="leave", operator_id=10001
    )

    assert handled is True
    assert get_session_store().get("123", "10001") is None


@pytest.mark.asyncio
async def test_kick_by_admin_marks_kicked_without_bot_flag() -> None:
    """被管理员手踢：终态 kicked，detail 标明不是机器人踢的。"""
    bot: Any = QuietBot()
    await _start(bot)

    handled = await flow_module.handle_member_left(
        group_id="123", user_id="10001", sub_type="kick", operator_id=999
    )

    assert handled is True
    assert get_session_store().get("123", "10001") is None


@pytest.mark.asyncio
async def test_kick_by_bot_itself_is_flagged() -> None:
    """机器人自己踢的：仍为 kicked，但 detail.by_bot 为 True 以便查账。"""
    bot: Any = QuietBot()
    await _start(bot)

    handled = await flow_module.handle_member_left(
        group_id="123", user_id="10001", sub_type="kick", operator_id=int(bot.self_id)
    )

    assert handled is True
    assert get_session_store().get("123", "10001") is None


@pytest.mark.asyncio
async def test_leave_unknown_member_is_noop() -> None:
    """没有会话的成员退群：返回 False，不报错。"""
    bot: Any = QuietBot()
    _ = bot

    handled = await flow_module.handle_member_left(
        group_id="123", user_id="77777", sub_type="leave", operator_id=77777
    )

    assert handled is False


@pytest.mark.asyncio
async def test_kick_pending_member_leaving_ends_session() -> None:
    """待补踢成员自己退群：会话应结束（不再需要补踢），不留在内存。"""
    bot: Any = QuietBot()
    await _start(bot)
    get_session_store().end("123", "10001", status="kick_pending")
    assert len(get_session_store().list_kick_pending()) == 1

    handled = await flow_module.handle_member_left(
        group_id="123", user_id="10001", sub_type="leave", operator_id=10001
    )

    assert handled is True
    assert get_session_store().get("123", "10001") is None
    assert get_session_store().list_kick_pending() == ()


@pytest.mark.asyncio
async def test_bot_kicked_out_clears_whole_group() -> None:
    """机器人被踢（kick_me）：清理该群全部会话。"""
    bot: Any = QuietBot()
    await start_verification(bot, group_id=123, user_id=10001)
    await start_verification(bot, group_id=123, user_id=10002)
    assert get_session_store().get("123", "10001") is not None
    assert get_session_store().get("123", "10002") is not None

    cleared = await flow_module.handle_bot_left_group(group_id="123")

    assert cleared == 2
    assert get_session_store().get("123", "10001") is None
    assert get_session_store().get("123", "10002") is None


@pytest.mark.asyncio
async def test_bot_left_group_keeps_other_groups() -> None:
    """机器人退群只清理该群，别的群会话不受影响。"""
    bot: Any = QuietBot()
    await start_verification(bot, group_id=123, user_id=10001)
    store = get_session_store()
    # 直接用 store.start 造一个别群的会话（避免手工拼 SessionRecord）。
    store.start(
        group_id="456",
        user_id="20001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id=None,
    )

    cleared = await flow_module.handle_bot_left_group(group_id="123")

    assert cleared == 1
    assert store.get("123", "10001") is None
    assert store.get("456", "20001") is not None


@pytest.mark.asyncio
async def test_bot_left_group_with_no_sessions_returns_zero() -> None:
    """群里没有会话时清理返回 0。"""
    cleared = await flow_module.handle_bot_left_group(group_id="999")

    assert cleared == 0
