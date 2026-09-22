"""管理员通知渠道测试（FR7：群内 / 私聊 / 关闭）。

通知渠道由 ``fanqie_notify_channel`` 控制：

- ``group``：向群内发**一条**消息，@ 全部管理员，可引用成员原消息。
- ``private``：逐个私聊管理员；私聊失败时回退群内 @ 该管理员。
- ``none``：完全不发送。

"""

from __future__ import annotations

from typing import Any

import pytest


class NotifyBot:
    """记录 send_group_msg / send_private_msg 调用的假 Bot。"""

    self_id = "bot1"

    def __init__(self, *, private_fails: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.private_fails = private_fails

    async def send_group_msg(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("send_group_msg", kwargs))
        return {"message_id": 1}

    async def send_private_msg(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("send_private_msg", kwargs))
        if self.private_fails:
            from nonebot.adapters.onebot.v11.exception import ActionFailed

            raise ActionFailed(retcode=100, retmsg="private blocked", data=None)
        return {"message_id": 2}

    @property
    def group_calls(self) -> list[dict[str, Any]]:
        return [kw for kind, kw in self.calls if kind == "send_group_msg"]

    @property
    def private_calls(self) -> list[dict[str, Any]]:
        return [kw for kind, kw in self.calls if kind == "send_private_msg"]


def _fix_channel(
    monkeypatch: pytest.MonkeyPatch, channel: str, admin_ids: set[int] | None = None
) -> None:
    """把通知渠道与管理员列表固定为测试值。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_notify_channel", channel)
    monkeypatch.setattr(
        plugin_config,
        "fanqie_admin_ids",
        {1001, 1002} if admin_ids is None else admin_ids,
    )


def _mentions(kwargs: dict[str, Any]) -> list[str]:
    """取出消息里所有 at 段的目标 QQ。"""
    return sorted(str(seg.data["qq"]) for seg in kwargs["message"] if seg.type == "at")


def _seg_types(kwargs: dict[str, Any]) -> list[str]:
    return [seg.type for seg in kwargs["message"]]


@pytest.mark.asyncio
async def test_group_channel_sends_single_message_mentioning_all_admins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group 渠道：只发一条群消息，且 @ 全部管理员（不发私聊）。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        actions as actions_module,
    )

    _fix_channel(monkeypatch, "group")
    bot: Any = NotifyBot()

    sent = await actions_module.notify_admins(
        bot,
        group_id=555,
        user_id=777,
        reply_message_id=None,
        message="【验证失败】测试",
    )

    assert sent == 1, "group 渠道应只发一条消息"
    assert len(bot.calls) == 1, "不应额外发送私聊"
    assert not bot.private_calls
    (kwargs,) = bot.group_calls
    assert kwargs["group_id"] == 555
    assert _mentions(kwargs) == ["1001", "1002"]
    assert "reply" not in _seg_types(kwargs), "未给 reply_message_id 时不应有引用段"
    assert "【验证失败】测试" in str(kwargs["message"])


@pytest.mark.asyncio
async def test_group_channel_quotes_original_message_when_id_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group 渠道：给了 reply_message_id 时前置引用段，且引用段在最前面。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        actions as actions_module,
    )

    _fix_channel(monkeypatch, "group")
    bot: Any = NotifyBot()

    await actions_module.notify_admins(
        bot,
        group_id=555,
        user_id=777,
        reply_message_id=4242,
        message="【验证失败】测试",
    )

    (kwargs,) = bot.group_calls
    types = _seg_types(kwargs)
    assert types[0] == "reply", "引用段应在消息最前"
    assert kwargs["message"][0].data["id"] == "4242"


@pytest.mark.asyncio
async def test_private_channel_sends_one_message_per_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Private 渠道：每个管理员一条私聊，不发群消息。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        actions as actions_module,
    )

    _fix_channel(monkeypatch, "private")
    bot: Any = NotifyBot()

    sent = await actions_module.notify_admins(
        bot,
        group_id=555,
        user_id=777,
        reply_message_id=4242,
        message="【验证失败】测试",
    )

    assert sent == 2
    assert not bot.group_calls
    assert sorted(kw["user_id"] for kw in bot.private_calls) == [1001, 1002]
    assert "【验证失败】测试" in str(bot.private_calls[0]["message"])


@pytest.mark.asyncio
async def test_private_channel_falls_back_to_group_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Private 渠道：私聊失败时回退到群内 @ 该管理员。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        actions as actions_module,
    )

    _fix_channel(monkeypatch, "private")
    bot: Any = NotifyBot(private_fails=True)

    await actions_module.notify_admins(
        bot,
        group_id=555,
        user_id=777,
        reply_message_id=None,
        message="【验证失败】测试",
    )

    assert len(bot.group_calls) == 2, "两个管理员各回退一条群消息"
    assert sorted(_mentions(kw)[0] for kw in bot.group_calls) == ["1001", "1002"]


@pytest.mark.asyncio
async def test_none_channel_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """None 渠道：不发群消息也不发私聊，返回 0。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        actions as actions_module,
    )

    _fix_channel(monkeypatch, "none")
    bot: Any = NotifyBot()

    sent = await actions_module.notify_admins(
        bot,
        group_id=555,
        user_id=777,
        reply_message_id=None,
        message="【验证失败】测试",
    )

    assert sent == 0
    assert bot.calls == []


@pytest.mark.asyncio
async def test_group_channel_with_empty_admin_list_still_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group 渠道：管理员列表为空时仍发一条通知（不 @ 任何人），不静默丢弃。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        actions as actions_module,
    )

    _fix_channel(monkeypatch, "group", admin_ids=set())
    bot: Any = NotifyBot()

    sent = await actions_module.notify_admins(
        bot,
        group_id=555,
        user_id=777,
        reply_message_id=None,
        message="【验证失败】测试",
    )

    assert sent == 1
    assert len(bot.group_calls) == 1
    assert _mentions(bot.group_calls[0]) == []
    assert "【验证失败】测试" in str(bot.group_calls[0]["message"])
