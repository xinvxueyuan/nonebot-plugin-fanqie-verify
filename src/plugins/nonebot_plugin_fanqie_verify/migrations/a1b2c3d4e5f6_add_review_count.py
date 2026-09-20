"""add review_count to verification sessions

迁移 ID: a1b2c3d4e5f6
父迁移: 1f2e3d4c5b6a
创建时间: 2026-08-24 16:00:00.000000

说明: 普通成员“重审”累计次数此前只存在于内存，重启即清零，可能绕过
FANQIE_REVIEW_MAX_TIMES 上限。本迁移为验证会话表新增 review_count 列，
以支持重启后保留重审上限计数。

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "1f2e3d4c5b6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    """检查表中是否已存在指定列（幂等保护）。"""
    bind = op.get_bind()
    try:
        columns = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    except Exception:  # noqa: BLE001 - 表尚不存在视为无列
        return False
    return any(row[1] == column for row in columns)


def upgrade(name: str = "") -> None:
    if name:
        return
    if not _has_column("fanqie_verification_sessions", "review_count"):
        op.add_column(
            "fanqie_verification_sessions",
            sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade(name: str = "") -> None:
    if name:
        return
    if _has_column("fanqie_verification_sessions", "review_count"):
        op.drop_column("fanqie_verification_sessions", "review_count")
