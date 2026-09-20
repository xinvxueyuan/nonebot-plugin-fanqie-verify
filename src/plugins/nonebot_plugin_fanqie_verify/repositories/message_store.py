"""消息、审计与验证事件的仓库层操作（SQLite 专用）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..database.models import (
    AuditRecord,
    MessageRecord,
    VerificationEventRecord,
    VerificationSession,
)
from ..database.orm_crud import (
    create,
    delete,
    get_one,
    list_items,
    update,
    upsert,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_scoped_session


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """审计事件的写入请求对象。"""

    platform_id: str
    adapter_id: str
    bot_id: str
    api_name: str
    data_summary: str | None
    result_summary: str | None
    exception_summary: str | None
    protocol_id: str | None = None
    framework_id: str = "nonebot"
    audit_type: str = "api_call"
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationEventWrite:
    """验证流程关键事件的写入请求对象。"""

    platform_id: str
    adapter_id: str
    bot_id: str
    group_id: str
    user_id: str
    event_type: str
    protocol_id: str | None = None
    success: bool | None = None
    detail: dict[str, Any] | None = None
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationSessionWrite:
    """验证会话记录的写入请求对象。"""

    platform_id: str
    adapter_id: str
    bot_id: str
    group_id: str
    user_id: str
    protocol_id: str | None = None
    framework_id: str = "nonebot"


def _event_category_from_type(event_type: str) -> str | None:
    head = event_type.split(".", maxsplit=1)[0].strip()
    return head or None


async def record_event_received(
    session: AsyncSession | async_scoped_session[AsyncSession],
    *,
    platform_id: str,
    adapter_id: str,
    protocol_id: str | None = None,
    bot_id: str,
    conversation_id: str | None,
    user_id: str | None,
    message_id: str | None,
    event_type: str,
    message_type: str | None,
    text_summary: str | None,
    raw_message: str | None,
    raw_event: str | None,
    event_category: str | None = None,
    framework_id: str = "nonebot",
) -> MessageRecord:
    """创建或更新一条收到消息的记录。"""
    now = datetime.now(UTC)
    insert_values: dict[str, Any] = {
        "platform_id": platform_id,
        "adapter_id": adapter_id,
        "protocol_id": protocol_id,
        "framework_id": framework_id,
        "bot_id": bot_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "message_id": message_id,
        "event_type": event_type,
        "event_category": event_category or _event_category_from_type(event_type),
        "message_type": message_type,
        "text_summary": text_summary,
        "raw_message": raw_message,
        "raw_event": raw_event,
        "process_status": "received",
        "exception_summary": None,
        "created_at": now,
        "updated_at": now,
    }
    if message_id is None:
        return await create(session, MessageRecord, **insert_values)
    return await upsert(
        session,
        MessageRecord,
        insert_values,
        conflict_fields=[
            "platform_id",
            "adapter_id",
            "protocol_id",
            "bot_id",
            "conversation_id",
            "message_id",
        ],
    )


async def record_matcher_result(
    session: AsyncSession | async_scoped_session[AsyncSession],
    *,
    platform_id: str,
    adapter_id: str,
    protocol_id: str | None = None,
    bot_id: str,
    conversation_id: str | None,
    message_id: str | None,
    process_status: str,
    exception_summary: str | None = None,
) -> bool:
    """更新已存储消息记录的处理状态。"""
    if message_id is None:
        return False
    filters: dict[str, Any] = {
        "platform_id": platform_id,
        "adapter_id": adapter_id,
        "bot_id": bot_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }
    if protocol_id is not None:
        filters["protocol_id"] = protocol_id
    record = await get_one(session, MessageRecord, filters)
    if record is None:
        return False
    await update(
        session,
        MessageRecord,
        {"id": record.id},
        {
            "process_status": process_status,
            "exception_summary": exception_summary,
            "updated_at": datetime.now(UTC),
        },
    )
    return True


async def record_api_call(
    session: AsyncSession | async_scoped_session[AsyncSession],
    event: AuditEvent,
) -> AuditRecord:
    """记录一条平台 API 或生命周期事件为审计记录。"""
    return await create(
        session,
        AuditRecord,
        platform_id=event.platform_id,
        adapter_id=event.adapter_id,
        protocol_id=event.protocol_id,
        framework_id=event.framework_id,
        bot_id=event.bot_id,
        audit_type=event.audit_type,
        event_type=event.api_name,
        trace_id=event.trace_id,
        data_summary=event.data_summary,
        result_summary=event.result_summary,
        exception_summary=event.exception_summary,
        created_at=datetime.now(UTC),
    )


async def record_verification_event(
    session: AsyncSession | async_scoped_session[AsyncSession],
    event: VerificationEventWrite,
) -> VerificationEventRecord:
    """记录一条验证流程关键事件。"""
    return await create(
        session,
        VerificationEventRecord,
        platform_id=event.platform_id,
        adapter_id=event.adapter_id,
        protocol_id=event.protocol_id,
        bot_id=event.bot_id,
        group_id=event.group_id,
        user_id=event.user_id,
        event_type=event.event_type,
        success=event.success,
        trace_id=event.trace_id,
        detail=event.detail or None,
        created_at=datetime.now(UTC),
    )


async def upsert_verification_session(
    session: AsyncSession | async_scoped_session[AsyncSession],
    session_write: VerificationSessionWrite,
    *,
    status: str,
    retry_count: int = 0,
    review_count: int = 0,
    is_muted: bool = False,
    last_extracted: dict[str, Any] | None = None,
    trigger_time: datetime | None = None,
    expires_at: datetime | None = None,
    trace_id: str | None = None,
) -> VerificationSession:
    """按 (group_id, user_id) upsert 验证会话。"""
    now = datetime.now(UTC)
    insert_values: dict[str, Any] = {
        "platform_id": session_write.platform_id,
        "adapter_id": session_write.adapter_id,
        "protocol_id": session_write.protocol_id,
        "framework_id": session_write.framework_id,
        "bot_id": session_write.bot_id,
        "group_id": session_write.group_id,
        "user_id": session_write.user_id,
        "status": status,
        "retry_count": retry_count,
        "review_count": review_count,
        "is_muted": is_muted,
        "last_extracted": last_extracted,
        "trace_id": trace_id,
        "trigger_time": trigger_time or now,
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
    }
    return await upsert(
        session,
        VerificationSession,
        insert_values,
        conflict_fields=["group_id", "user_id"],
    )


async def list_active_sessions(
    session: AsyncSession | async_scoped_session[AsyncSession],
    *,
    group_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
) -> list[VerificationSession]:
    """列出活跃（waiting）的验证会话。"""
    filters: dict[str, Any] = {"status": "waiting"}
    if group_id is not None:
        filters["group_id"] = group_id
    if user_id is not None:
        filters["user_id"] = user_id
    return await list_items(
        session,
        VerificationSession,
        filters,
        order_by=["-trigger_time"],
        limit=limit,
    )


async def list_pending_sessions(
    session: AsyncSession | async_scoped_session[AsyncSession],
    *,
    limit: int = 500,
) -> list[VerificationSession]:
    """列出待处理的验证会话（waiting 与 awaiting_admin）。

    用于重启后恢复会话状态，确保验证中的成员不会被当作已通过。

    """
    return await list_items(
        session,
        VerificationSession,
        None,
        conditions=[VerificationSession.status.in_(("waiting", "awaiting_admin"))],
        order_by=["-trigger_time"],
        limit=limit,
    )


async def cleanup_expired_sessions(
    session: AsyncSession | async_scoped_session[AsyncSession],
    *,
    retention_days: int,
) -> tuple[int, bool]:
    """删除超过保留期的验证会话（含事件/审计记录）。"""
    if retention_days <= 0:
        return (0, True)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    session_count, session_known = await delete(
        session,
        VerificationSession,
        {},
        conditions=[VerificationSession.trigger_time < cutoff],
    )
    event_count, event_known = await delete(
        session,
        VerificationEventRecord,
        {},
        conditions=[VerificationEventRecord.created_at < cutoff],
    )
    audit_count, audit_known = await delete(
        session,
        AuditRecord,
        {},
        conditions=[AuditRecord.created_at < cutoff],
    )
    message_count, message_known = await delete(
        session,
        MessageRecord,
        {},
        conditions=[MessageRecord.created_at < cutoff],
    )
    return (
        session_count + event_count + audit_count + message_count,
        session_known and event_known and audit_known and message_known,
    )


__all__ = [
    "AuditEvent",
    "VerificationEventWrite",
    "VerificationSessionWrite",
    "cleanup_expired_sessions",
    "list_active_sessions",
    "list_pending_sessions",
    "record_api_call",
    "record_event_received",
    "record_matcher_result",
    "record_verification_event",
    "upsert_verification_session",
]
