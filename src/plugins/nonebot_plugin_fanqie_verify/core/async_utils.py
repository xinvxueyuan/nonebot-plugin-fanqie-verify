"""异步工具：fire-and-forget 后台任务管理。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from nonebot import logger

_background_tasks: set[asyncio.Task[Any]] = set()
_BACKGROUND_TASK_DRAIN_TIMEOUT_SECONDS = 10.0


def get_background_tasks() -> tuple[asyncio.Task[Any], ...]:
    """返回当前注册的后台任务快照。"""
    return tuple(sorted(_background_tasks, key=lambda task: task.get_name()))


def fire_and_forget(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str = "fire_and_forget",
) -> asyncio.Task[Any]:
    """调度一个协程作为受跟踪的后台任务。

    Args:
        coro: 要调度的协程对象。
        name: 后台任务的可读名称。

    Returns:
        创建的 :class:`asyncio.Task` 对象，便于调用方按需 await。

    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    logger.debug("Registered background task {}", task.get_name())
    task.add_done_callback(_on_background_task_done)
    return task


def _on_background_task_done(task: asyncio.Task[Any]) -> None:
    """移除已完成任务的引用并记录异常。"""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.exception("Background task %s failed", task.get_name(), exc_info=exc)


async def drain_background_tasks(
    *,
    drain_timeout: float = _BACKGROUND_TASK_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """在有界超时内等待后台任务完成。

    Args:
        drain_timeout: 等待后台任务完成的最大秒数。

    Raises:
        ValueError: drain_timeout 不大于 0 时。

    """
    if drain_timeout <= 0:
        raise ValueError

    current_task = asyncio.current_task()
    deadline = asyncio.get_running_loop().time() + drain_timeout
    while tasks := get_background_tasks():
        pending = tuple(task for task in tasks if task is not current_task)
        if not pending:
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            unfinished = pending
        else:
            _, unfinished = await asyncio.wait(pending, timeout=remaining)
        if unfinished:
            for task in unfinished:
                task.cancel()
            logger.warning(
                "Timed out draining background tasks; cancelled {} task(s)",
                len(unfinished),
            )
            await asyncio.sleep(0)
            return
        await asyncio.sleep(0)
