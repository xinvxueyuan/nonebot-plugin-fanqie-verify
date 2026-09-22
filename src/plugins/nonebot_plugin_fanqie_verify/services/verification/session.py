"""入群验证会话管理（FR1/FR7/FR8 状态与超时）。

PRD 第 8 节允许单机内存会话存储，重启丢失无关紧要。本模块在内存中
维护活跃验证会话（``(group_id, user_id)`` 为键），并为每个会话调度
一个超时协程（FR7）。会话状态变化时同步更新数据库以便审计回溯。

"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import uuid

from nonebot import logger

from ...core.config import plugin_config

_SessionKey = tuple[str, str]

TimeoutCallback = Callable[[str, str], Awaitable[None]]

# 待管理员决策成员被移出前的提醒回调：参数为 (group_id, user_id, 剩余秒数)。
ReminderCallback = Callable[[str, str, int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """内存中的活跃验证会话记录。"""

    group_id: str
    user_id: str
    bot_id: str
    platform_id: str
    adapter_id: str
    protocol_id: str | None
    trigger_time: datetime
    expires_at: datetime
    retry_count: int = 0
    review_count: int = 0
    is_muted: bool = False
    last_extracted: dict | None = None
    status: str = "waiting"
    trace_id: str | None = None

    def to_db_dict(self) -> dict:
        """转换为仓库层 upsert 所需的字段。"""
        return {
            "platform_id": self.platform_id,
            "adapter_id": self.adapter_id,
            "protocol_id": self.protocol_id,
            "bot_id": self.bot_id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "status": self.status,
            "retry_count": self.retry_count,
            "review_count": self.review_count,
            "is_muted": self.is_muted,
            "last_extracted": self.last_extracted,
            "trigger_time": self.trigger_time,
            "expires_at": self.expires_at,
            "trace_id": self.trace_id,
        }


def _new_trace_id() -> str:
    """生成一次验证流程的事务追踪标识（全局唯一、不含连字符）。"""
    return uuid.uuid4().hex


def _aware_expires(record: SessionRecord) -> datetime:
    """返回会话的 timestamp-aware 截止时间（naive 时归一化兜底）。"""
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at


class SessionStore:
    """活跃验证会话的内存存储与超时调度。"""

    def __init__(self) -> None:
        self._sessions: dict[_SessionKey, SessionRecord] = {}
        self._timeout_tasks: dict[_SessionKey, asyncio.Task] = {}
        self._reminder_tasks: dict[_SessionKey, list[asyncio.Task]] = {}
        self._timeout_callback: TimeoutCallback | None = None
        self._admin_timeout_callback: TimeoutCallback | None = None
        self._reminder_callback: ReminderCallback | None = None
        self._closed = False
        # 正在处理中的会话（防并发重复处理同一会话）。
        self._processing: set[_SessionKey] = set()
        # 私聊验证：账号 → 已选目标群（多群待验证时记录用户的选定群）。
        self._private_targets: dict[str, str] = {}
        # 成员最近一次提交图片的消息 id（供超时通报引用原消息）。
        self._last_image_msg: dict[_SessionKey, int] = {}
        # 待确认的补验候选（群号 → 成员 QQ 号），confirm_first 模式下暂存。
        self._pending_backfill: dict[str, list[int]] = {}

    def set_timeout_callback(self, callback: TimeoutCallback) -> None:
        """注册成员响应超时回调（由编排层注入，避免循环依赖）。"""
        self._timeout_callback = callback

    def set_admin_timeout_callback(self, callback: TimeoutCallback) -> None:
        """注册管理员决策超时回调（由编排层注入）。"""
        self._admin_timeout_callback = callback

    def set_reminder_callback(self, callback: ReminderCallback) -> None:
        """注册待管理员决策成员的移出前提醒回调（由编排层注入）。"""
        self._reminder_callback = callback

    def get(self, group_id: str, user_id: str) -> SessionRecord | None:
        """返回活跃会话记录；不存在时返回 ``None``。"""
        return self._sessions.get((group_id, user_id))

    def try_claim(self, group_id: str, user_id: str) -> bool:
        """原子标记会话为「处理中」防并发重复处理；已在处理时返回 False。"""
        key = (group_id, user_id)
        if key in self._processing:
            return False
        self._processing.add(key)
        return True

    def release(self, group_id: str, user_id: str) -> None:
        """清除会话的「处理中」标记。"""
        self._processing.discard((group_id, user_id))

    def is_waiting(self, group_id: str, user_id: str) -> bool:
        """该成员是否存在处于 waiting 状态的会话。"""
        record = self.get(group_id, user_id)
        return record is not None and record.status == "waiting"

    def list_waiting(self) -> tuple[SessionRecord, ...]:
        """返回所有 waiting 状态会话的快照。"""
        return tuple(
            record for record in self._sessions.values() if record.status == "waiting"
        )

    def list_waiting_by_group(self, group_id: str) -> tuple[SessionRecord, ...]:
        """返回某群所有 waiting 状态会话的快照（「处理中列表」用）。"""
        return tuple(
            record
            for record in self._sessions.values()
            if record.status == "waiting" and record.group_id == group_id
        )

    def list_waiting_by_user(self, user_id: str) -> tuple[SessionRecord, ...]:
        """返回某用户在全部群中的 waiting 会话（私聊验证查群用）。

        私聊消息没有群号，验证前先按账号找出该用户所有待验证群；
        恰好一个时直接验证，多个时列出群号供其选择。

        """
        return tuple(
            record
            for record in self._sessions.values()
            if record.status == "waiting" and record.user_id == user_id
        )

    def set_private_target(self, user_id: str, group_id: str) -> None:
        """记录某用户私聊验证时选定的目标群。"""
        self._private_targets[user_id] = group_id

    def get_private_target(self, user_id: str) -> str | None:
        """返回某用户私聊验证时选定的目标群。"""
        return self._private_targets.get(user_id)

    def clear_private_target(self, user_id: str) -> None:
        """清除某用户的私聊验证目标群选择。"""
        self._private_targets.pop(user_id, None)

    def set_last_image_message(
        self,
        group_id: str,
        user_id: str,
        message_id: int,
    ) -> None:
        """记录成员最近一次提交图片的消息 id（供超时通报引用原消息）。"""
        self._last_image_msg[(group_id, user_id)] = message_id

    def get_last_image_message(self, group_id: str, user_id: str) -> int | None:
        """返回成员最近一次提交图片的消息 id；无记录时返回 ``None``。"""
        return self._last_image_msg.get((group_id, user_id))

    def set_pending_backfill(self, group_id: str, user_ids: list[int]) -> None:
        """暂存待管理员确认的补验候选成员（「补验确认」命令读取）。"""
        self._pending_backfill[group_id] = list(user_ids)

    def pop_pending_backfill(self, group_id: str) -> list[int]:
        """取出并清除某群待确认的补验候选成员。"""
        return self._pending_backfill.pop(group_id, [])

    def list_awaiting_admin(
        self,
        group_id: str | None = None,
    ) -> tuple[SessionRecord, ...]:
        """返回待管理员决策会话的快照（可选按群过滤）。"""
        return tuple(
            record
            for record in self._sessions.values()
            if record.status == "awaiting_admin"
            and (group_id is None or record.group_id == group_id)
        )

    def start(
        self,
        *,
        group_id: str,
        user_id: str,
        bot_id: str,
        platform_id: str,
        adapter_id: str,
        protocol_id: str | None,
    ) -> SessionRecord:
        """开启（或重置）一个验证会话并调度超时。

        PRD 10：同一用户在同一群仅保留最新会话，旧会话超时任务取消。

        """
        key = (group_id, user_id)
        self._cancel_timeout(key)

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=plugin_config.fanqie_response_timeout)
        record = SessionRecord(
            group_id=group_id,
            user_id=user_id,
            bot_id=bot_id,
            platform_id=platform_id,
            adapter_id=adapter_id,
            protocol_id=protocol_id,
            trigger_time=now,
            expires_at=expires_at,
            trace_id=_new_trace_id(),
        )
        self._sessions[key] = record
        self._schedule_timeout(key)
        return record

    def mark_retry(self, group_id: str, user_id: str) -> SessionRecord | None:
        """识别失败一次，重试计数 +1 并重置超时窗口。

        每次失败重试都重新计算超时截止时间（``expires_at``）并重新调度
        超时任务：固定窗口会随失败次数被逐步吃掉，导致成员来不及完成验证
        （超时未与重试机制绑定）。重置后成员每次失败都有完整的新窗口。

        """
        key = (group_id, user_id)
        record = self.get(group_id, user_id)
        if record is None:
            return None
        self._cancel_timeout(key)
        data = record.to_db_dict()
        data["retry_count"] = record.retry_count + 1
        data["expires_at"] = datetime.now(UTC) + timedelta(
            seconds=plugin_config.fanqie_response_timeout
        )
        updated = SessionRecord(**data)
        self._sessions[key] = updated
        self._schedule_timeout(key)
        return updated

    def mark_review(self, group_id: str, user_id: str) -> SessionRecord | None:
        """重审计数 +1（仅普通成员自审时消耗；管理员发起的重审不计次）。"""
        record = self.get(group_id, user_id)
        if record is None:
            return None
        data = record.to_db_dict()
        data["review_count"] = record.review_count + 1
        updated = SessionRecord(**data)
        self._sessions[(group_id, user_id)] = updated
        return updated

    def set_review_count(
        self, group_id: str, user_id: str, review_count: int
    ) -> SessionRecord | None:
        """设置会话的重审计数（重审重开流程后写回计数用）。"""
        record = self.get(group_id, user_id)
        if record is None:
            return None
        data = record.to_db_dict()
        data["review_count"] = review_count
        updated = SessionRecord(**data)
        self._sessions[(group_id, user_id)] = updated
        return updated

    def update_last_extracted(
        self,
        group_id: str,
        user_id: str,
        last_extracted: dict | None,
        *,
        is_muted: bool | None = None,
        status: str | None = None,
    ) -> SessionRecord | None:
        """更新会话的提取结果、禁言状态或状态。"""
        record = self.get(group_id, user_id)
        if record is None:
            return None
        data = record.to_db_dict()
        data["last_extracted"] = last_extracted
        if is_muted is not None:
            data["is_muted"] = is_muted
        if status is not None:
            data["status"] = status
        updated = SessionRecord(**data)
        self._sessions[(group_id, user_id)] = updated
        return updated

    def end(self, group_id: str, user_id: str, *, status: str) -> SessionRecord | None:
        """结束会话：标记终态并取消超时任务。"""
        key = (group_id, user_id)
        record = self.get(group_id, user_id)
        if record is None:
            return None
        self._cancel_timeout(key)
        data = record.to_db_dict()
        data["status"] = status
        updated = SessionRecord(**data)
        self._sessions[key] = updated
        return updated

    def set_muted(
        self,
        group_id: str,
        user_id: str,
        *,
        is_muted: bool,
    ) -> SessionRecord | None:
        """同步会话的禁言状态（由群禁言事件驱动）。"""
        record = self.get(group_id, user_id)
        if record is None:
            return None
        data = record.to_db_dict()
        data["is_muted"] = is_muted
        updated = SessionRecord(**data)
        self._sessions[(group_id, user_id)] = updated
        return updated

    def remove_group(self, group_id: str) -> tuple[SessionRecord, ...]:
        """移除某群的全部会话并取消对应超时任务。

        用于机器人被移出群或群解散时清理该群遗留状态。

        Args:
            group_id: 群号。

        Returns:
            被移除的会话记录。

        """
        removed: list[SessionRecord] = []
        for key, record in list(self._sessions.items()):
            if key[0] == group_id:
                self._cancel_timeout(key)
                self._sessions.pop(key, None)
                removed.append(record)
        return tuple(removed)

    def remove(self, group_id: str, user_id: str) -> SessionRecord | None:
        """从内存移除会话（用于清理终态）。"""
        key = (group_id, user_id)
        self._cancel_timeout(key)
        return self._sessions.pop(key, None)

    def _schedule_timeout(self, key: _SessionKey) -> None:
        """按会话当前状态调度对应超时协程。"""
        if self._closed:
            return
        record = self._sessions[key]
        if record.status not in ("waiting", "awaiting_admin"):
            return  # 终态无需调度
        expires_at = _aware_expires(record)
        delay = max(0.0, (expires_at - datetime.now(UTC)).total_seconds())
        task_name = f"fanqie-timeout:{key[0]}:{key[1]}"
        task = asyncio.create_task(
            self._run_timeout(key, delay, record.status),
            name=task_name,
        )
        self._timeout_tasks[key] = task
        if record.status == "awaiting_admin":
            self._schedule_reminders(key)

    def _cancel_timeout(self, key: _SessionKey) -> None:
        task = self._timeout_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
        reminder_tasks = self._reminder_tasks.pop(key, None)
        if reminder_tasks:
            for reminder_task in reminder_tasks:
                if not reminder_task.done():
                    reminder_task.cancel()

    def _schedule_reminders(self, key: _SessionKey) -> None:
        """为待管理员决策会话调度被移出前的提醒协程。

        在每个配置的提前量（``fanqie_remind_before_kick``）到达时触发
        提醒回调；若提前量已过（剩余时间不足）则跳过该次提醒。重入时
        先取消既有提醒任务（如 restore 重复调度）。
        """
        if self._closed or self._reminder_callback is None:
            return
        previous = self._reminder_tasks.pop(key, None)
        if previous:
            for task in previous:
                if not task.done():
                    task.cancel()
        record = self._sessions[key]
        now = datetime.now(UTC)
        for ahead in sorted(
            (int(v) for v in plugin_config.fanqie_remind_before_kick), reverse=True
        ):
            if ahead <= 0:
                continue
            expires_at = _aware_expires(record)
            fire_at = expires_at - timedelta(seconds=ahead)
            delay = (fire_at - now).total_seconds()
            if delay <= 0:
                continue  # 该提前量已过，跳过
            task_name = f"fanqie-remind:{key[0]}:{key[1]}:{ahead}"
            task = asyncio.create_task(
                self._run_reminder(key, ahead),
                name=task_name,
            )
            self._reminder_tasks.setdefault(key, []).append(task)

    async def _run_reminder(self, key: _SessionKey, ahead: int) -> None:
        """等待到应提醒时刻，仍处于待管理员决策则触发提醒回调。"""
        record = self._sessions[key]
        fire_at = _aware_expires(record) - timedelta(seconds=ahead)
        delay = max(0.0, (fire_at - datetime.now(UTC)).total_seconds())
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        record = self._sessions.get(key)
        if record is None or record.status != "awaiting_admin":
            return
        remaining = int((_aware_expires(record) - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            return  # 已到移出时刻，交由超时回调处理
        callback = self._reminder_callback
        if callback is None:
            return
        try:
            await callback(record.group_id, record.user_id, remaining)
        except Exception:
            logger.exception("处理移出前提醒失败: {}", key)

    async def _run_timeout(self, key: _SessionKey, delay: float, status: str) -> None:
        """等待超时并触发对应回调（若会话仍处于该状态）。"""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._timeout_tasks.pop(key, None)
        record = self._sessions.get(key)
        if record is None or record.status != status:
            return
        if status == "waiting":
            callback = self._timeout_callback
        elif status == "awaiting_admin":
            callback = self._admin_timeout_callback
        else:
            return
        if callback is None:
            logger.warning("未配置超时回调，会话 {} 无法自动处理", key)
            return
        try:
            await callback(*key)
        except Exception:
            logger.exception("处理会话超时失败: {}", key)

    def extend_awaiting(
        self,
        group_id: str,
        user_id: str,
        *,
        seconds: int,
    ) -> SessionRecord | None:
        """延期待管理员决策会话的移出时间（从当前时间重新计时）。

        管理员暂时无法处理时推迟自动移出：新截止时间 = 「当前时间 + 延期
        时长」，而非在原截止上累加（原截止已临近或已过时更直观）。延期后
        重新调度超时任务与被移出前提醒。

        Args:
            group_id: 群号。
            user_id: 成员 QQ 号。
            seconds: 延期时长（秒），调用方负责按配置上限裁剪。

        Returns:
            更新后的会话记录；会话不存在或不在 ``awaiting_admin`` 状态时
            返回 ``None``。

        """
        key = (group_id, user_id)
        record = self.get(group_id, user_id)
        if record is None or record.status != "awaiting_admin":
            return None
        self._cancel_timeout(key)
        data = record.to_db_dict()
        data["expires_at"] = datetime.now(UTC) + timedelta(seconds=seconds)
        updated = SessionRecord(**data)
        self._sessions[key] = updated
        self._schedule_timeout(key)
        return updated

    def await_admin(
        self,
        group_id: str,
        user_id: str,
        *,
        last_extracted: dict | None = None,
    ) -> SessionRecord | None:
        """把会话转入待管理员决策状态并调度管理决策超时。

        Args:
            group_id: 群号。
            user_id: 成员 QQ 号。
            last_extracted: 需要保留的最近一次提取结果。

        Returns:
            更新后的会话记录；会话不存在时返回 ``None``。

        """
        key = (group_id, user_id)
        record = self.get(group_id, user_id)
        if record is None:
            return None
        self._cancel_timeout(key)
        data = record.to_db_dict()
        data["status"] = "awaiting_admin"
        data["expires_at"] = datetime.now(UTC) + timedelta(
            seconds=plugin_config.fanqie_admin_decision_timeout
        )
        if last_extracted is not None:
            data["last_extracted"] = last_extracted
        updated = SessionRecord(**data)
        self._sessions[key] = updated
        self._schedule_timeout(key)
        return updated

    def list_kick_pending(self) -> tuple[SessionRecord, ...]:
        """返回待补踢会话的快照（踢人失败或 bot 掉线时积压）。

        这些成员已过管理员决策超时、应被移出，但因机器人不在线或踢出失败
        而未完成；机器人重连后由 :func:`retry_pending_kicks` 补偿。

        """
        return tuple(
            record
            for record in self._sessions.values()
            if record.status == "kick_pending"
        )

    def restore(self, record: SessionRecord) -> SessionRecord:
        """把持久化的会话记录恢复到内存并恢复超时调度（重启恢复）。"""
        key = (record.group_id, record.user_id)
        self._sessions[key] = record
        if record.status in ("waiting", "awaiting_admin"):
            self._schedule_timeout(key)
        return record

    def close(self) -> None:
        """取消所有超时、提醒任务（停机时调用）。"""
        self._closed = True
        for task in self._timeout_tasks.values():
            if not task.done():
                task.cancel()
        self._timeout_tasks.clear()
        for tasks in self._reminder_tasks.values():
            for task in tasks:
                if not task.done():
                    task.cancel()
        self._reminder_tasks.clear()


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """返回全局会话存储单例。"""
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def _reset_store() -> None:
    """重置全局会话存储（主要供测试使用）。"""
    global _store
    if _store is not None:
        _store.close()
    _store = None


__all__ = [
    "SessionRecord",
    "SessionStore",
    "get_session_store",
]
