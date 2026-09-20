"""initial schema

迁移 ID: 1f2e3d4c5b6a
父迁移:
创建时间: 2026-08-12 10:00:00.000000

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "1f2e3d4c5b6a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("nonebot_plugin_fanqie_verify",)
depends_on: str | Sequence[str] | None = None


def upgrade(name: str = "") -> None:
    if name:
        return
    op.create_table(
        "fanqie_verification_sessions",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("platform_id", sa.String(length=64), nullable=False),
        sa.Column("adapter_id", sa.String(length=64), nullable=False),
        sa.Column("protocol_id", sa.String(length=64), nullable=True),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("bot_id", sa.String(length=128), nullable=False),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("is_muted", sa.Boolean(), nullable=False),
        sa.Column("last_extracted", sa.JSON(), nullable=True),
        sa.Column("trigger_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fanqie_verification_sessions")),
        sa.UniqueConstraint(
            "group_id",
            "user_id",
            name="uq_fanqie_verification_session_identity",
        ),
        info={"bind_key": "nonebot_plugin_fanqie_verify"},
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_platform_id"),
        "fanqie_verification_sessions",
        ["platform_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_adapter_id"),
        "fanqie_verification_sessions",
        ["adapter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_protocol_id"),
        "fanqie_verification_sessions",
        ["protocol_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_framework_id"),
        "fanqie_verification_sessions",
        ["framework_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_bot_id"),
        "fanqie_verification_sessions",
        ["bot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_group_id"),
        "fanqie_verification_sessions",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_user_id"),
        "fanqie_verification_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_status"),
        "fanqie_verification_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_is_muted"),
        "fanqie_verification_sessions",
        ["is_muted"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_trigger_time"),
        "fanqie_verification_sessions",
        ["trigger_time"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_expires_at"),
        "fanqie_verification_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_created_at"),
        "fanqie_verification_sessions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_sessions_updated_at"),
        "fanqie_verification_sessions",
        ["updated_at"],
        unique=False,
    )

    op.create_table(
        "fanqie_message_records",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("platform_id", sa.String(length=64), nullable=False),
        sa.Column("adapter_id", sa.String(length=64), nullable=False),
        sa.Column("protocol_id", sa.String(length=64), nullable=True),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("bot_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=True),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_category", sa.String(length=64), nullable=True),
        sa.Column("message_type", sa.String(length=64), nullable=True),
        sa.Column("text_summary", sa.Text(), nullable=True),
        sa.Column("raw_message", sa.Text(), nullable=True),
        sa.Column("raw_event", sa.Text(), nullable=True),
        sa.Column("process_status", sa.String(length=32), nullable=False),
        sa.Column("exception_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fanqie_message_records")),
        sa.UniqueConstraint(
            "platform_id",
            "adapter_id",
            "protocol_id",
            "bot_id",
            "conversation_id",
            "message_id",
            name="uq_fanqie_message_record_identity",
        ),
        info={"bind_key": "nonebot_plugin_fanqie_verify"},
    )
    op.create_index(
        op.f("ix_fanqie_message_records_platform_id"),
        "fanqie_message_records",
        ["platform_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_adapter_id"),
        "fanqie_message_records",
        ["adapter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_protocol_id"),
        "fanqie_message_records",
        ["protocol_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_framework_id"),
        "fanqie_message_records",
        ["framework_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_bot_id"),
        "fanqie_message_records",
        ["bot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_conversation_id"),
        "fanqie_message_records",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_user_id"),
        "fanqie_message_records",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_message_id"),
        "fanqie_message_records",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_event_type"),
        "fanqie_message_records",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_event_category"),
        "fanqie_message_records",
        ["event_category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_message_type"),
        "fanqie_message_records",
        ["message_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_process_status"),
        "fanqie_message_records",
        ["process_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_created_at"),
        "fanqie_message_records",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_message_records_updated_at"),
        "fanqie_message_records",
        ["updated_at"],
        unique=False,
    )

    op.create_table(
        "fanqie_audit_records",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("platform_id", sa.String(length=64), nullable=False),
        sa.Column("adapter_id", sa.String(length=64), nullable=False),
        sa.Column("protocol_id", sa.String(length=64), nullable=True),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("bot_id", sa.String(length=128), nullable=False),
        sa.Column("audit_type", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("data_summary", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("exception_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fanqie_audit_records")),
        info={"bind_key": "nonebot_plugin_fanqie_verify"},
    )
    op.create_index(
        op.f("ix_fanqie_audit_records_platform_id"),
        "fanqie_audit_records",
        ["platform_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_audit_records_adapter_id"),
        "fanqie_audit_records",
        ["adapter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_audit_records_protocol_id"),
        "fanqie_audit_records",
        ["protocol_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_audit_records_framework_id"),
        "fanqie_audit_records",
        ["framework_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_audit_records_bot_id"),
        "fanqie_audit_records",
        ["bot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_audit_records_audit_type"),
        "fanqie_audit_records",
        ["audit_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_audit_records_event_type"),
        "fanqie_audit_records",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_audit_records_created_at"),
        "fanqie_audit_records",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "fanqie_verification_events",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("platform_id", sa.String(length=64), nullable=False),
        sa.Column("adapter_id", sa.String(length=64), nullable=False),
        sa.Column("protocol_id", sa.String(length=64), nullable=True),
        sa.Column("bot_id", sa.String(length=128), nullable=False),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fanqie_verification_events")),
        info={"bind_key": "nonebot_plugin_fanqie_verify"},
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_platform_id"),
        "fanqie_verification_events",
        ["platform_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_adapter_id"),
        "fanqie_verification_events",
        ["adapter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_protocol_id"),
        "fanqie_verification_events",
        ["protocol_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_bot_id"),
        "fanqie_verification_events",
        ["bot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_group_id"),
        "fanqie_verification_events",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_user_id"),
        "fanqie_verification_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_event_type"),
        "fanqie_verification_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_success"),
        "fanqie_verification_events",
        ["success"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fanqie_verification_events_created_at"),
        "fanqie_verification_events",
        ["created_at"],
        unique=False,
    )


def downgrade(name: str = "") -> None:
    if name:
        return
    op.drop_index(
        op.f("ix_fanqie_verification_events_created_at"),
        table_name="fanqie_verification_events",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_events_success"),
        table_name="fanqie_verification_events",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_events_event_type"),
        table_name="fanqie_verification_events",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_events_user_id"),
        table_name="fanqie_verification_events",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_events_group_id"),
        table_name="fanqie_verification_events",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_events_bot_id"),
        table_name="fanqie_verification_events",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_events_protocol_id"),
        table_name="fanqie_verification_events",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_events_adapter_id"),
        table_name="fanqie_verification_events",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_events_platform_id"),
        table_name="fanqie_verification_events",
    )
    op.drop_table("fanqie_verification_events")

    op.drop_index(
        op.f("ix_fanqie_audit_records_created_at"),
        table_name="fanqie_audit_records",
    )
    op.drop_index(
        op.f("ix_fanqie_audit_records_event_type"),
        table_name="fanqie_audit_records",
    )
    op.drop_index(
        op.f("ix_fanqie_audit_records_audit_type"),
        table_name="fanqie_audit_records",
    )
    op.drop_index(
        op.f("ix_fanqie_audit_records_bot_id"),
        table_name="fanqie_audit_records",
    )
    op.drop_index(
        op.f("ix_fanqie_audit_records_framework_id"),
        table_name="fanqie_audit_records",
    )
    op.drop_index(
        op.f("ix_fanqie_audit_records_protocol_id"),
        table_name="fanqie_audit_records",
    )
    op.drop_index(
        op.f("ix_fanqie_audit_records_adapter_id"),
        table_name="fanqie_audit_records",
    )
    op.drop_index(
        op.f("ix_fanqie_audit_records_platform_id"),
        table_name="fanqie_audit_records",
    )
    op.drop_table("fanqie_audit_records")

    op.drop_index(
        op.f("ix_fanqie_message_records_updated_at"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_created_at"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_process_status"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_message_type"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_event_category"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_event_type"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_message_id"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_user_id"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_conversation_id"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_bot_id"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_framework_id"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_protocol_id"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_adapter_id"),
        table_name="fanqie_message_records",
    )
    op.drop_index(
        op.f("ix_fanqie_message_records_platform_id"),
        table_name="fanqie_message_records",
    )
    op.drop_table("fanqie_message_records")

    op.drop_index(
        op.f("ix_fanqie_verification_sessions_updated_at"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_created_at"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_expires_at"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_trigger_time"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_is_muted"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_status"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_user_id"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_group_id"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_bot_id"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_framework_id"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_protocol_id"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_adapter_id"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_index(
        op.f("ix_fanqie_verification_sessions_platform_id"),
        table_name="fanqie_verification_sessions",
    )
    op.drop_table("fanqie_verification_sessions")
