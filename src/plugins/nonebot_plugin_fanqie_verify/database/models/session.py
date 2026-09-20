"""待验证成员会话 ORM 模型。

对应 PRD 第 8 节的数据结构：每个待验证用户维护一个会话，记录触发时间、
重试计数、是否已禁言与最近一次提取结果。同一用户在同一个群内仅允许一个
活跃验证会话。
"""

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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .time_utils import utc_now


class VerificationSession(Model):
    """一个待验证成员的验证会话。

    Attributes:
        platform_id: 平台标识（如 ``qq``）。
        adapter_id: 适配器标识（如 ``~onebot.v11``）。
        bot_id: 处理该会话的机器人 self_id。
        group_id: 群号。
        user_id: 被验证用户 QQ 号。
        status: 会话状态（waiting / awaiting_admin / approved / rejected /
            kicked / expired / failed / cleared）。
        retry_count: 识别失败的累计次数。
        review_count: 普通成员通过“重审”命令重新发起验证的累计次数（管理员
            发起不计），用于重启后保留重审上限计数。
        is_muted: 该成员当前是否已被禁言。
        last_extracted: 最近一次 OCR 提取结果（书名、章节、阅读时间）。
        trigger_time: 触发入群验证的时间。
        expires_at: 超时踢出截止时间。
    """

    __tablename__ = "fanqie_verification_sessions"
    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "user_id",
            name="uq_fanqie_verification_session_identity",
        ),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    platform_id: Mapped[str] = mapped_column(String(64), index=True)
    adapter_id: Mapped[str] = mapped_column(String(64), index=True)
    protocol_id: Mapped[str | None] = mapped_column(String(64), index=True)
    framework_id: Mapped[str] = mapped_column(String(64), default="nonebot", index=True)
    bot_id: Mapped[str] = mapped_column(String(128), index=True)
    group_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="waiting", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_extracted: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    trigger_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
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
