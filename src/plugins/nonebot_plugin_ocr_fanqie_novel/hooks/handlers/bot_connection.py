"""机器人连接/断开钩子处理。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from nonebot import get_driver
from nonebot.adapters import Bot

from ...core.async_utils import fire_and_forget
from ...core.config import plugin_config
from ...services.message_store import record_bot_lifecycle
from ...services.verification import backfill

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot

driver = get_driver()

#: 触发补验的重连模式（off 与未知值不做处理）。
_BACKFILL_RECONNECT_MODES = ("auto", "notify")


@driver.on_bot_connect
async def on_bot_connect(bot: Bot) -> None:
    """机器人连接时记录生命周期事件，并按配置补验漏掉的入群成员。

    机器人（如 LLBot）掉线期间 QQ 不会补发 ``group_increase`` 事件，这期间
    入群的成员不会被验证。重连后按 ``fanqie_backfill_reconnect_mode`` 处理：
    ``notify``（默认）仅在群内提醒管理员，``auto`` 自动开启验证，``off``
    不做处理。

    """
    fire_and_forget(
        record_bot_lifecycle(bot, "bot_connected"), name="record_bot_lifecycle"
    )
    if not plugin_config.fanqie_backfill_enabled:
        return
    mode = str(plugin_config.fanqie_backfill_reconnect_mode or "").strip().lower()
    if mode not in _BACKFILL_RECONNECT_MODES:
        return
    fire_and_forget(
        backfill.handle_reconnect(cast("OneBot11Bot", bot)),
        name="backfill_on_reconnect",
    )


@driver.on_bot_disconnect
async def on_bot_disconnect(bot: Bot) -> None:
    """机器人断开时记录生命周期事件。"""
    fire_and_forget(
        record_bot_lifecycle(bot, "bot_disconnected"), name="record_bot_lifecycle"
    )
