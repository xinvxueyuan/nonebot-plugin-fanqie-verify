"""驱动生命周期钩子处理。"""

from __future__ import annotations

import asyncio

from nonebot import get_driver, logger

from ...core.async_utils import drain_background_tasks
from ...services.message_store import shutdown_message_store
from ...start.startup import shutdown, startup

driver = get_driver()


@driver.on_startup
async def on_startup() -> None:
    """NoneBot 驱动启动时初始化插件运行时服务。"""
    await startup()


@driver.on_shutdown
async def on_shutdown() -> None:
    """NoneBot 驱动停止时关闭插件运行时服务。"""
    services = (
        ("消息存储", shutdown_message_store),
        ("验证会话", shutdown),
        ("后台任务", drain_background_tasks),
    )

    async def _close_services() -> asyncio.CancelledError | None:
        service_cancellation: asyncio.CancelledError | None = None
        for name, shutdown_fn in services:
            result = (await asyncio.gather(shutdown_fn(), return_exceptions=True))[0]
            if isinstance(result, asyncio.CancelledError):
                service_cancellation = service_cancellation or result
            elif isinstance(result, BaseException):
                logger.error("关闭服务失败 {}: {}", name, type(result).__name__)
        return service_cancellation

    cleanup_task = asyncio.create_task(_close_services())
    external_cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            service_cancellation = await asyncio.shield(cleanup_task)
            break
        except asyncio.CancelledError as exc:
            external_cancellation = external_cancellation or exc
            if cleanup_task.done():
                service_cancellation = cleanup_task.result()
                break

    cancellation = external_cancellation or service_cancellation
    if cancellation is not None:
        raise cancellation
