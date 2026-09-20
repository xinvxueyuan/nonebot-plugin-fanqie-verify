"""运行时钩子共享的接口类型定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlatformContext:
    """解析 Bot 实例后得到的平台上下文。"""

    platform_id: str
    adapter_id: str
    protocol_id: str | None
    bot_id: str


@dataclass(frozen=True, slots=True)
class MessageIdentity:
    """收到消息事件的稳定标识。"""

    platform_id: str
    adapter_id: str
    protocol_id: str | None
    framework_id: str
    bot_id: str
    conversation_id: str | None
    message_id: str | None


@dataclass(frozen=True, slots=True)
class NormalizedMessageEvent:
    """适配器无关的消息事件数据，供下游消费者使用。"""

    identity: MessageIdentity
    user_id: str | None
    event_type: str
    event_category: str | None
    message_type: str | None
    text_summary: str | None
    raw_message: str | None
    raw_event: str | None


__all__ = [
    "MessageIdentity",
    "NormalizedMessageEvent",
    "PlatformContext",
]
