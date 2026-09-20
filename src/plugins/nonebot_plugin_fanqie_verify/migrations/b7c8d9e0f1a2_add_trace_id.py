"""add trace_id to session/event/audit tables

迁移 ID: b7c8d9e0f1a2
父迁移: a1b2c3d4e5f6
创建时间: 2026-08-26 22:00:00.000000

说明: 审计增强要求「事务追踪 id 全程携带、全信息入库可追溯」。本期为
验证会话、验证事件、审计记录三张表各新增 trace_id 列，同一验证流程内
所有记录共享同一 trace（生成于会话开启时），便于按 trace 串联「入群触发
→ OCR 识别 → 判定 → 放行/拒绝/踢出」的完整链路。

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRACE_TABLES: tuple[tuple[str, str], ...] = (
    ("fanqie_verification_sessions", "trace_id"),
    ("fanqie_verification_events", "trace_id"),
    ("fanqie_audit_records", "trace_id"),
)


def _has_column(table: str, column: str) -> bool:
    """检查表中是否已存在指定列（幂等保护）。"""
    bind = op.get_bind()
    try:
        columns = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    except Exception:  # noqa: BLE001 - 表尚不存在视为无列
        return False
    return any(row[1] == column for row in columns)


def _has_index(table: str, index_name: str) -> bool:
    """检查指定索引是否已存在（幂等保护）。"""
    bind = op.get_bind()
    try:
        rows = bind.execute(sa.text(f"PRAGMA index_list({table})")).fetchall()
    except Exception:  # noqa: BLE001 - 表尚不存在视为无索引
        return False
    return any(row[1] == index_name for row in rows)


def upgrade(name: str = "") -> None:
    if name:
        return
    for table, column in _TRACE_TABLES:
        if not _has_column(table, column):
            op.add_column(
                table,
                sa.Column(
                    column, sa.String(length=64), nullable=True, server_default=None
                ),
            )
    # trace_id 列在 ORM 中声明 index=True，需补建索引，否则启动检查拦截。
    for table, column in _TRACE_TABLES:
        index_name = f"ix_{table}_{column}"
        if not _has_index(table, index_name):
            op.create_index(index_name, table, [column])


def downgrade(name: str = "") -> None:
    if name:
        return
    for table, column in _TRACE_TABLES:
        index_name = f"ix_{table}_{column}"
        if _has_index(table, index_name):
            op.drop_index(index_name, table_name=table)
        if _has_column(table, column):
            op.drop_column(table, column)
