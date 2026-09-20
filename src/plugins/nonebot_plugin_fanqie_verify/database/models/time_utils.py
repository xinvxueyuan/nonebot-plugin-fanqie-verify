"""共享时间工具与 ORM 模型定义。"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """返回带时区的 UTC 时间戳。"""
    return datetime.now(UTC)
