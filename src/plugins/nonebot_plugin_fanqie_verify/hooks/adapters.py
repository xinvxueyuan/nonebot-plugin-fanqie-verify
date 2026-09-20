"""平台适配器解析与事件规范化，供运行时钩子使用。

本项目仅支持 QQ onebot.v11 适配器，因此适配器到平台标识的映射
采用固定表，不提供运行时注册表。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ..core.config import plugin_config
from .interfaces import MessageIdentity, NormalizedMessageEvent, PlatformContext

if TYPE_CHECKING:
    from nonebot.adapters import Bot, Event

logger = logging.getLogger(__name__)
SUMMARY_LIMIT = 500
RAW_PAYLOAD_MAX_DEPTH = 8
DEFAULT_PROTOCOL_ID = "default"

# 固定支持：QQ + onebot.v11。
SUPPORTED_ADAPTER_NAMES = ("OneBot V11", "onebot.v11")
PLATFORM_ID = "qq"


def _truncate(value: str | None, limit: int | None = None) -> str | None:
    if value is None:
        return None
    size = (
        limit if limit is not None else plugin_config.fanqie_message_store_summary_limit
    )
    if size <= 0 or len(value) <= size:
        return value
    return f"{value[:size]}..."


def _stringify(value: Any, *, limit: int = SUMMARY_LIMIT) -> str | None:
    if value is None:
        return None
    return _truncate(str(value), limit)


def _first_attr(obj: Any, *names: str) -> Any:
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return None


def _safe_call(obj: Any, method_name: str) -> Any:
    method = getattr(obj, method_name, None)
    if method is None:
        return None
    try:
        return method()
    except (AttributeError, TypeError, ValueError):
        return None


def _adapter_name(bot: Bot) -> str:
    adapter = getattr(bot, "adapter", None)
    get_name = getattr(adapter, "get_name", None)
    if get_name is None:
        return "unknown"
    try:
        return str(get_name())
    except (AttributeError, TypeError, ValueError):
        return "unknown"


def _adapter_identity(adapter_name: str) -> tuple[str, str] | None:
    if adapter_name in SUPPORTED_ADAPTER_NAMES:
        return (PLATFORM_ID, "~onebot.v11")
    return None


def _event_data(event: Event) -> Any:
    return getattr(event, "data", None)


def _event_type(event: Event) -> str:
    return str(
        _safe_call(event, "get_event_name")
        or _safe_call(event, "get_type")
        or type(event).__name__
    )


def _event_category(event: Event) -> str | None:
    value = _safe_call(event, "get_type")
    return str(value) if value is not None else None


def _message_type(event: Event) -> str | None:
    value = _first_attr(event, "message_type", "post_type")
    if value is None:
        value = _first_attr(_event_data(event), "message_type", "post_type")
    return _stringify(value, limit=64)


def _conversation_id(event: Event) -> str | None:
    chat_id = getattr(getattr(event, "chat", None), "id", None)
    if isinstance(chat_id, (str, int)):
        return _stringify(chat_id, limit=128)
    value = _first_attr(
        event,
        "group_id",
        "guild_id",
        "channel_id",
        "peer_id",
        "session_id",
    )
    if not isinstance(value, (str, int)):
        value = None
    if value is None:
        value = _first_attr(
            _event_data(event),
            "group_id",
            "guild_id",
            "channel_id",
            "peer_id",
            "session_id",
        )
        if not isinstance(value, (str, int)):
            value = None
    if value is None:
        value = _safe_call(event, "get_session_id")
    return _stringify(value, limit=128)


def _user_id(event: Event) -> str | None:
    value = _safe_call(event, "get_user_id")
    if value is None:
        value = _first_attr(event, "user_id", "sender_id")
    if value is None:
        value = _first_attr(_event_data(event), "user_id", "sender_id")
    if value is None:
        value = getattr(getattr(event, "from_", None), "id", None)
    return _stringify(value, limit=128)


def _message_id(event: Event) -> str | None:
    value = _first_attr(event, "message_id", "id")
    if value is None:
        value = _first_attr(_event_data(event), "message_id", "message_seq", "id")
    return _stringify(value, limit=128)


def _plain_text(event: Event) -> str | None:
    value = _safe_call(event, "get_plaintext")
    if value:
        return _truncate(str(value))
    message = _safe_call(event, "get_message")
    if message is None:
        message = _first_attr(event, "message")
    if message is None:
        message = _first_attr(_event_data(event), "message", "segments")
    return _truncate(str(message)) if message is not None else None


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth > RAW_PAYLOAD_MAX_DEPTH:
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item, depth=depth + 1) for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item, depth=depth + 1) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump) and type(value).__module__ != "unittest.mock":
        try:
            return _jsonable(
                model_dump(mode="json", by_alias=True, exclude_none=False),
                depth=depth + 1,
            )
        except (TypeError, ValueError, AttributeError):
            pass

    data = getattr(value, "data", None)
    if data is not None and data is not value:
        return _jsonable(data, depth=depth + 1)

    if hasattr(value, "__dict__") and type(value).__module__ != "unittest.mock":
        raw = {
            key: item for key, item in vars(value).items() if not key.startswith("_")
        }
        if raw:
            return _jsonable(raw, depth=depth + 1)

    return str(value)


def _json_summary(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _raw_message(event: Event) -> str | None:
    message = _safe_call(event, "get_message")
    if message is None:
        message = _first_attr(event, "message")
    if message is None:
        message = _first_attr(_event_data(event), "message", "segments")
    return _json_summary(message)


def _raw_event(event: Event) -> str | None:
    return _json_summary(event)


def resolve_platform_context(bot: Bot) -> PlatformContext | None:
    """把 Bot 实例解析为平台上下文。

    适配器不被支持时返回 None。
    """
    adapter = _adapter_name(bot)
    identity = _adapter_identity(adapter)
    if identity is None:
        return None
    platform_id, adapter_id = identity
    return PlatformContext(
        platform_id=platform_id,
        adapter_id=adapter_id,
        bot_id=_stringify(getattr(bot, "self_id", None), limit=128) or "unknown",
        protocol_id=DEFAULT_PROTOCOL_ID,
    )


def normalize_message_event(bot: Bot, event: Event) -> NormalizedMessageEvent | None:
    """把适配器事件规范化为消息存储元数据。"""
    adapter = _adapter_name(bot)
    adapter_identity = _adapter_identity(adapter)
    if adapter_identity is None:
        return None
    platform_id, adapter_id = adapter_identity
    bot_id = _stringify(getattr(bot, "self_id", None), limit=128) or "unknown"
    identity = MessageIdentity(
        platform_id=platform_id,
        adapter_id=adapter_id,
        protocol_id=DEFAULT_PROTOCOL_ID,
        framework_id="nonebot",
        bot_id=bot_id,
        conversation_id=_conversation_id(event),
        message_id=_message_id(event),
    )
    return NormalizedMessageEvent(
        identity=identity,
        user_id=_user_id(event),
        event_type=_event_type(event),
        event_category=_event_category(event),
        message_type=_message_type(event),
        text_summary=_plain_text(event),
        raw_message=_raw_message(event),
        raw_event=_raw_event(event),
    )


__all__ = [
    "DEFAULT_PROTOCOL_ID",
    "SUPPORTED_ADAPTER_NAMES",
    "MessageIdentity",
    "NormalizedMessageEvent",
    "_adapter_identity",
    "_adapter_name",
    "_conversation_id",
    "_event_category",
    "_event_data",
    "_event_type",
    "_first_attr",
    "_json_summary",
    "_jsonable",
    "_message_id",
    "_message_type",
    "_plain_text",
    "_raw_event",
    "_raw_message",
    "_safe_call",
    "_stringify",
    "_user_id",
    "normalize_message_event",
    "resolve_platform_context",
]
