"""插件启动逻辑：加载配置并初始化运行时服务。"""

from __future__ import annotations

from nonebot import logger, require

require("nonebot_plugin_orm")

from ..services.message_store import (
    initialize_message_store,
)
from ..services.verification import (
    get_session_store,
    handle_admin_decision_timeout,
    handle_reminder,
    handle_timeout,
    restore_pending_sessions,
)

__all__ = ["startup"]


async def startup() -> None:
    """加载运行时状态并初始化各服务。"""
    await initialize_message_store()
    store = get_session_store()
    store.set_timeout_callback(handle_timeout)
    store.set_admin_timeout_callback(handle_admin_decision_timeout)
    store.set_reminder_callback(handle_reminder)
    await restore_pending_sessions()
    logger.info("番茄读书验证插件启动完成")


async def shutdown() -> None:
    """关闭运行时服务，取消未完成的超时任务。"""
    get_session_store().close()
    logger.info("番茄读书验证插件已停止")
