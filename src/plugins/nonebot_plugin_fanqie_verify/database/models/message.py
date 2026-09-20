"""消息与审计记录 ORM 模型（SQLite 专用）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nonebot import require

require("nonebot_plugin_orm")
from nonebot_plugin_orm import Model
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .time_utils import utc_now


class MessageRecord(Model):
    """收到的真实消息事件，存储在全局 ORM 数据库中。

    用于验证会话审计：记录触发验证前的相关入群事件与聊天记录。
    """

    __tablename__ = "fanqie_message_records"
    __table_args__ = (
        UniqueConstraint(
            "platform_id",
            "adapter_id",
            "protocol_id",
            "bot_id",
            "conversation_id",
            "message_id",
            name="uq_fanqie_message_record_identity",
        ),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    platform_id: Mapped[str] = mapped_column(String(64), index=True)
    adapter_id: Mapped[str] = mapped_column(String(64), index=True)
    protocol_id: Mapped[str | None] = mapped_column(String(64), index=True)
    framework_id: Mapped[str] = mapped_column(String(64), default="nonebot", index=True)
    bot_id: Mapped[str] = mapped_column(String(128), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    event_category: Mapped[str | None] = mapped_column(String(64), index=True)
    message_type: Mapped[str | None] = mapped_column(String(64), index=True)
    text_summary: Mapped[str | None] = mapped_column(Text)
    raw_message: Mapped[str | None] = mapped_column(Text)
    raw_event: Mapped[str | None] = mapped_column(Text)
    process_status: Mapped[str] = mapped_column(
        String(32),
        default="received",
        index=True,
    )
    exception_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )


class AuditRecord(Model):
    """API 调用与机器人生命周期事件的审计记录。"""

    __tablename__ = "fanqie_audit_records"
    __table_args__ = ({"extend_existing": True},)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    platform_id: Mapped[str] = mapped_column(String(64), index=True)
    adapter_id: Mapped[str] = mapped_column(String(64), index=True)
    protocol_id: Mapped[str | None] = mapped_column(String(64), index=True)
    framework_id: Mapped[str] = mapped_column(String(64), default="nonebot", index=True)
    bot_id: Mapped[str] = mapped_column(String(128), index=True)
    audit_type: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    data_summary: Mapped[str | None] = mapped_column(Text)
    result_summary: Mapped[str | None] = mapped_column(Text)
    exception_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
    )


class VerificationEventRecord(Model):
    """验证流程中的关键事件（入群触发、OCR 结果、判定结果、超时踢出）。

    覆盖 PRD 第 4 节“全流程记录”需求，为管理员提供可回溯的验证日志。
    """

    __tablename__ = "fanqie_verification_events"
    __table_args__ = ({"extend_existing": True},)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    platform_id: Mapped[str] = mapped_column(String(64), index=True)
    adapter_id: Mapped[str] = mapped_column(String(64), index=True)
    protocol_id: Mapped[str | None] = mapped_column(String(64), index=True)
    bot_id: Mapped[str] = mapped_column(String(128), index=True)
    group_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool | None] = mapped_column(Boolean, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
    )
