"""钩子处理器子包。

导入本子包即注册所有运行时钩子。
"""

from __future__ import annotations

from . import (
    api_audit as api_audit,
    bot_connection as bot_connection,
    lifecycle as lifecycle,
    message_store as message_store,
)

__all__ = ["api_audit", "bot_connection", "lifecycle", "message_store"]
