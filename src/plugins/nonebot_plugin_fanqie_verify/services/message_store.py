"""消息、审计与验证流程的服务层业务接口。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nonebot import require

require("nonebot_plugin_orm")
from nonebot_plugin_orm import get_session

from ..core.config import plugin_config
from ..database.orm_crud import DatabaseError
from ..hooks.adapters import (
    MessageIdentity,
    NormalizedMessageEvent,
    PlatformContext,
    resolve_platform_context,
)
from ..repositories import message_store as repository

if TYPE_CHECKING:
    from nonebot.adapters import Bot
    from nonebot.matcher import Matcher

logger = logging.getLogger(__name__)
STATE_KEY = "_fanqie_message_record_identity"
SUMMARY_LIMIT = 500


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


async def initialize_message_store() -> None:
    """初始化消息存储运行时资源。"""
    if not plugin_config.fanqie_message_store_enabled:
        logger.info("消息存储已禁用")
        return
    logger.info("消息存储初始化完成")
    # 启动时执行一次过期清理（删除超过保留期的历史记录），避免依赖关闭时清理。
    await cleanup_expired_messages()


async def shutdown_message_store() -> None:
    """关闭消息存储，执行轻量维护。"""
    if not plugin_config.fanqie_message_store_enabled:
        return
    await cleanup_expired_messages()


async def cleanup_expired_messages() -> tuple[int, bool]:
    """按配置清理过期记录。"""
    if (
        not plugin_config.fanqie_message_store_enabled
        or not plugin_config.fanqie_message_store_cleanup_enabled
    ):
        return (0, True)
    try:
        async with get_session() as session, session.begin():
            return await repository.cleanup_expired_sessions(
                session,
                retention_days=plugin_config.fanqie_message_store_retention_days,
            )
    except DatabaseError:
        logger.exception("清理过期验证记录失败")
        return (0, False)


async def record_bot_lifecycle(bot: Bot, event_type: str) -> bool:
    """把机器人连接/断开生命周期记录为审计事件。"""
    if not plugin_config.fanqie_message_store_enabled:
        return False
    platform_context = resolve_platform_context(bot)
    if platform_context is None:
        return False
    try:
        async with get_session() as session, session.begin():
            await repository.record_api_call(
                session,
                repository.AuditEvent(
                    platform_id=platform_context.platform_id,
                    adapter_id=platform_context.adapter_id,
                    protocol_id=platform_context.protocol_id,
                    bot_id=platform_context.bot_id,
                    api_name=event_type,
                    data_summary=None,
                    result_summary=None,
                    exception_summary=None,
                    audit_type="lifecycle",
                ),
            )
    except DatabaseError:
        logger.exception("记录机器人生命周期事件失败: %s", event_type)
        return False
    return True


async def handle_event_received(normalized: NormalizedMessageEvent) -> None:
    """持久化一条收到的规范化消息事件。"""
    if not plugin_config.fanqie_message_store_enabled:
        return
    identity = normalized.identity
    try:
        async with get_session() as session, session.begin():
            await repository.record_event_received(
                session,
                platform_id=identity.platform_id,
                adapter_id=identity.adapter_id,
                protocol_id=identity.protocol_id,
                framework_id=identity.framework_id,
                bot_id=identity.bot_id,
                conversation_id=identity.conversation_id,
                user_id=normalized.user_id,
                message_id=identity.message_id,
                event_type=normalized.event_type,
                event_category=normalized.event_category,
                message_type=normalized.message_type,
                text_summary=normalized.text_summary,
                raw_message=normalized.raw_message,
                raw_event=normalized.raw_event,
            )
    except DatabaseError:
        logger.exception("记录收到消息事件失败")


async def handle_matcher_result(
    identity: MessageIdentity,
    matcher: Matcher,
    exception: Exception | None,
) -> bool:
    """更新已存储消息记录的处理状态。"""
    if not plugin_config.fanqie_message_store_enabled:
        return False
    status = "handled" if exception is None else "failed"
    if getattr(matcher, "block", False):
        status = f"{status}:blocked"
    try:
        async with get_session() as session, session.begin():
            return await repository.record_matcher_result(
                session,
                platform_id=identity.platform_id,
                adapter_id=identity.adapter_id,
                protocol_id=identity.protocol_id,
                bot_id=identity.bot_id,
                conversation_id=identity.conversation_id,
                message_id=identity.message_id,
                process_status=status,
                exception_summary=_stringify(exception),
            )
    except DatabaseError:
        logger.exception("更新消息处理状态失败")
        return False


async def handle_api_called(
    platform_context: PlatformContext,
    exception: Exception | None,
    api: str,
    data: dict[str, Any],
    result: Any,
) -> None:
    """记录一条平台 API 调用结果。"""
    if (
        not plugin_config.fanqie_message_store_enabled
        or not plugin_config.fanqie_message_store_record_api_calls
    ):
        return
    try:
        async with get_session() as session, session.begin():
            await repository.record_api_call(
                session,
                repository.AuditEvent(
                    platform_id=platform_context.platform_id,
                    adapter_id=platform_context.adapter_id,
                    protocol_id=platform_context.protocol_id,
                    bot_id=platform_context.bot_id,
                    api_name=api,
                    data_summary=_stringify(data),
                    result_summary=_stringify(result),
                    exception_summary=_stringify(exception),
                ),
            )
    except DatabaseError:
        logger.exception("记录平台 API 调用失败: %s", api)
