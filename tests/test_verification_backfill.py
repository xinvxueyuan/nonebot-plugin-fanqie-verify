"""补验功能测试（错过入群事件的成员补进验证流程）。"""

from __future__ import annotations

import time
from typing import Any

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
    backfill,
)

_NOW = int(time.time())
_GROUP_ID = 123


class _FakeBot:
    """模拟 OneBot11 Bot：返回预设群成员列表并记录发出的群消息。"""

    self_id = "3128682634"

    def __init__(self, members: list[dict[str, Any]] | None = None) -> None:
        self.members = members or []
        self.group_msgs: list[str] = []

    async def get_group_member_list(self, **kwargs: Any) -> list[dict[str, Any]]:
        _ = kwargs
        return self.members

    async def send_group_msg(self, **kwargs: Any) -> None:
        self.group_msgs.append(str(kwargs["message"]))


def _member(
    user_id: int,
    *,
    role: str = "member",
    join_time: int | None = None,
    nickname: str = "",
) -> dict[str, Any]:
    """构造一条群成员信息。"""
    return {
        "user_id": user_id,
        "role": role,
        "nickname": nickname or f"用户{user_id}",
        "join_time": join_time,
    }


def _patch_known(
    monkeypatch: pytest.MonkeyPatch,
    known: set[str] | None = None,
) -> None:
    """替换「已有验证记录」查询，避免测试依赖数据库。"""
    known_ids = known or set()

    async def fake_known(group_id: int) -> set[str]:
        _ = group_id
        return known_ids

    monkeypatch.setattr(backfill, "_known_user_ids", fake_known)


@pytest.mark.asyncio
async def test_collect_candidates_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描只保留时间窗口内、非管理/机器人、无验证记录的成员。"""
    bot: Any = _FakeBot([
        _member(10001, join_time=_NOW - 60),  # 候选
        _member(10002, join_time=_NOW - 3600 * 48),  # 窗口外
        _member(10003, join_time=_NOW - 60, role="admin"),  # 群管理员
        _member(10004, join_time=_NOW - 60, role="owner"),  # 群主
        _member(int(_FakeBot.self_id), join_time=_NOW - 60),  # 机器人自身
        _member(10006, join_time=_NOW - 60),  # 已有验证记录
        _member(10007),  # 无入群时间
    ])
    _patch_known(monkeypatch, {"10006"})

    candidates = await backfill.collect_candidates(bot, group_id=_GROUP_ID, hours=24)

    assert [candidate.user_id for candidate in candidates] == [10001]
    assert candidates[0].nickname == "用户10001"


@pytest.mark.asyncio
async def test_collect_candidates_targets_ignore_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员指定成员时忽略时间窗口（老成员也能补验）。"""
    bot: Any = _FakeBot([
        _member(10001, join_time=0),  # 很早入群，窗口外
        _member(10002, join_time=0),
    ])
    _patch_known(monkeypatch)

    candidates = await backfill.collect_candidates(
        bot,
        group_id=_GROUP_ID,
        hours=24,
        targets=[10001],
    )

    assert [candidate.user_id for candidate in candidates] == [10001]


@pytest.mark.asyncio
async def test_collect_candidates_sorted_by_join_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选按入群时间升序排列（先入群的先补）。"""
    bot: Any = _FakeBot([
        _member(10003, join_time=_NOW - 10),
        _member(10001, join_time=_NOW - 300),
        _member(10002, join_time=_NOW - 100),
    ])
    _patch_known(monkeypatch)

    candidates = await backfill.collect_candidates(bot, group_id=_GROUP_ID, hours=24)

    assert [candidate.user_id for candidate in candidates] == [10001, 10002, 10003]


@pytest.mark.asyncio
async def test_collect_candidates_api_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拉取群成员列表失败时返回空列表（不抛异常）。"""
    bot: Any = _FakeBot()

    async def boom(**kwargs: Any) -> list[dict[str, Any]]:
        _ = kwargs
        raise RuntimeError("api down")

    monkeypatch.setattr(bot, "get_group_member_list", boom)

    assert await backfill.collect_candidates(bot, group_id=_GROUP_ID, hours=24) == []


@pytest.mark.asyncio
async def test_run_backfill_starts_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_backfill 逐个调用正常入群流程开启验证。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        flow,
    )

    started: list[int] = []

    async def fake_start(bot: Any, *, group_id: int, user_id: int) -> Any:
        _ = (bot, group_id)
        started.append(user_id)
        return object()

    monkeypatch.setattr(flow, "start_verification", fake_start)

    candidates = [
        backfill.BackfillCandidate(10001, "甲", _NOW),
        backfill.BackfillCandidate(10002, "乙", _NOW),
    ]
    fake_bot: Any = _FakeBot()
    count = await backfill.run_backfill(
        fake_bot,
        group_id=_GROUP_ID,
        candidates=candidates,
        max_batch=10,
    )

    assert count == 2
    assert started == [10001, 10002]


@pytest.mark.asyncio
async def test_run_backfill_respects_max_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单次补验人数不超过 max_batch（防刷屏）。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        flow,
    )

    started: list[int] = []

    async def fake_start(bot: Any, *, group_id: int, user_id: int) -> Any:
        _ = (bot, group_id)
        started.append(user_id)
        return object()

    monkeypatch.setattr(flow, "start_verification", fake_start)

    candidates = [
        backfill.BackfillCandidate(user_id, "n", _NOW) for user_id in (1, 2, 3)
    ]
    fake_bot: Any = _FakeBot()
    count = await backfill.run_backfill(
        fake_bot,
        group_id=_GROUP_ID,
        candidates=candidates,
        max_batch=2,
    )

    assert count == 2
    assert started == [1, 2]


@pytest.mark.asyncio
async def test_handle_reconnect_off_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重连模式 off 时不查群、不发消息。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_backfill_enabled", True)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_reconnect_mode", "off")

    bot: Any = _FakeBot([_member(10001, join_time=_NOW - 60)])
    await backfill.handle_reconnect(bot)

    assert bot.group_msgs == []


@pytest.mark.asyncio
async def test_handle_reconnect_disabled_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补验总开关关闭时即使模式为 auto 也不处理。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_backfill_enabled", False)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_reconnect_mode", "auto")

    bot: Any = _FakeBot([_member(10001, join_time=_NOW - 60)])
    await backfill.handle_reconnect(bot)

    assert bot.group_msgs == []


@pytest.mark.asyncio
async def test_handle_reconnect_notify_only_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重连模式 notify（默认）只群内提醒管理员，不开启验证。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        flow,
    )

    monkeypatch.setattr(plugin_config, "fanqie_backfill_enabled", True)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_reconnect_mode", "notify")
    monkeypatch.setattr(backfill, "monitored_group_ids", lambda: [_GROUP_ID])
    _patch_known(monkeypatch)

    started: list[int] = []

    async def fake_start(bot: Any, *, group_id: int, user_id: int) -> Any:
        _ = (bot, group_id)
        started.append(user_id)
        return object()

    monkeypatch.setattr(flow, "start_verification", fake_start)

    bot: Any = _FakeBot([_member(10001, join_time=_NOW - 60)])
    await backfill.handle_reconnect(bot)

    assert started == []  # 未开启验证
    assert len(bot.group_msgs) == 1
    assert "补验" in bot.group_msgs[0]


@pytest.mark.asyncio
async def test_handle_reconnect_auto_backfills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重连模式 auto 自动开启验证并群内通报。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        flow,
    )

    monkeypatch.setattr(plugin_config, "fanqie_backfill_enabled", True)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_reconnect_mode", "auto")
    monkeypatch.setattr(plugin_config, "fanqie_backfill_max_batch", 20)
    monkeypatch.setattr(backfill, "monitored_group_ids", lambda: [_GROUP_ID])
    _patch_known(monkeypatch)

    started: list[int] = []

    async def fake_start(bot: Any, *, group_id: int, user_id: int) -> Any:
        _ = (bot, group_id)
        started.append(user_id)
        return object()

    monkeypatch.setattr(flow, "start_verification", fake_start)

    bot: Any = _FakeBot([
        _member(10001, join_time=_NOW - 60),
        _member(10002, join_time=_NOW - 30),
    ])
    await backfill.handle_reconnect(bot)

    assert started == [10001, 10002]
    assert len(bot.group_msgs) == 1
    assert "已自动为 2 名" in bot.group_msgs[0]


@pytest.mark.asyncio
async def test_handle_reconnect_no_candidates_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无漏验成员时不发任何消息（避免每次重连都刷屏）。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_backfill_enabled", True)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_reconnect_mode", "notify")
    monkeypatch.setattr(backfill, "monitored_group_ids", lambda: [_GROUP_ID])
    _patch_known(monkeypatch)

    bot: Any = _FakeBot([_member(10001, join_time=0)])  # 老成员，窗口外
    await backfill.handle_reconnect(bot)

    assert bot.group_msgs == []


def test_format_candidate_list() -> None:
    """候选名单文本包含人数与成员信息。"""
    text = backfill.format_candidate_list(
        _GROUP_ID,
        [
            backfill.BackfillCandidate(10001, "甲", _NOW),
            backfill.BackfillCandidate(10002, "", _NOW),
        ],
    )

    assert "待补验成员 2 人" in text
    assert "QQ 10001（甲）" in text
    assert "QQ 10002" in text
