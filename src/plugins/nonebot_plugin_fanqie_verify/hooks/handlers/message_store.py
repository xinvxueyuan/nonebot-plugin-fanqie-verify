"""消息存储运行时钩子处理。"""

from __future__ import annotations

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.message import (
    event_postprocessor,
    event_preprocessor,
    run_postprocessor,
    run_preprocessor,
)
from nonebot.typing import T_State

from ...core.async_utils import fire_and_forget
from ...core.config import plugin_config
from ...services.message_store import (
    STATE_KEY,
    handle_event_received,
    handle_matcher_result,
)
from ..adapters import MessageIdentity, normalize_message_event


@event_preprocessor
async def message_store_preprocessor(
    bot: Bot,
    event: Event,
    state: T_State,
) -> None:
    """匹配器处理前存储收到的消息事件元数据。"""
    if not plugin_config.fanqie_message_store_enabled:
        return
    normalized = normalize_message_event(bot, event)
    if normalized is None:
        return
    state[STATE_KEY] = normalized.identity
    fire_and_forget(handle_event_received(normalized), name="record_event_received")


@event_postprocessor
async def message_store_postprocessor(
    bot: Bot,
    event: Event,
    state: T_State,
) -> None:
    """预留的事件后处理钩子，用于未来的聚合更新。"""
    _ = (bot, event, state)


@run_preprocessor
async def message_store_run_preprocessor(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    state: T_State,
) -> None:
    """预留的匹配器运行前钩子。"""
    _ = (matcher, bot, event, state)


@run_postprocessor
async def message_store_run_postprocessor(
    matcher: Matcher,
    exception: Exception | None,
    bot: Bot,
    event: Event,
    state: T_State,
) -> None:
    """匹配器运行后更新消息处理状态。"""
    _ = (bot, event)
    if not plugin_config.fanqie_message_store_enabled:
        return
    identity = state.get(STATE_KEY)
    if not isinstance(identity, MessageIdentity):
        return
    fire_and_forget(
        handle_matcher_result(identity, matcher, exception),
        name="record_matcher_result",
    )
