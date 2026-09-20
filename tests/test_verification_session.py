"""入群验证会话管理与超时调度测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.verification.session import (
    SessionRecord,
    SessionStore,
    get_session_store,
)


@pytest.fixture(autouse=True)
def _fresh_store() -> Generator[None]:
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        session,
    )

    before = session._store
    session._store = None
    try:
        yield
    finally:
        session._store = before


def _start(store: SessionStore, *, user: str = "10001") -> SessionRecord:
    return store.start(
        group_id="123",
        user_id=user,
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )


@pytest.mark.asyncio
async def test_start_creates_waiting_session() -> None:
    store = SessionStore()
    record = _start(store)

    assert record.status == "waiting"
    assert record.retry_count == 0
    assert record.is_muted is False
    assert record.trigger_time.tzinfo is UTC
    assert record.expires_at > datetime.now(UTC)
    assert store.is_waiting("123", "10001") is True
    store.close()


@pytest.mark.asyncio
async def test_mark_retry_increments() -> None:
    store = SessionStore()
    _start(store)

    updated = store.mark_retry("123", "10001")
    assert updated is not None
    assert updated.retry_count == 1
    after = store.get("123", "10001")
    assert after is not None
    assert after.retry_count == 1
    store.close()


@pytest.mark.asyncio
async def test_end_cancels_timeout() -> None:
    store = SessionStore()
    _start(store)
    assert len(store._timeout_tasks) == 1

    store.end("123", "10001", status="approved")
    ended = store.get("123", "10001")
    assert ended is not None
    assert ended.status == "approved"
    assert len(store._timeout_tasks) == 0
    store.close()


@pytest.mark.asyncio
async def test_remove_clears_session() -> None:
    store = SessionStore()
    _start(store)

    removed = store.remove("123", "10001")
    assert removed is not None
    assert store.get("123", "10001") is None
    store.close()


@pytest.mark.asyncio
async def test_timeout_callback_fires() -> None:
    store = SessionStore()
    fired: list[tuple[str, str]] = []

    async def callback(group_id: str, user_id: str) -> None:
        fired.append((group_id, user_id))

    store.set_timeout_callback(callback)
    _start(store)
    await store._run_timeout(("123", "10001"), 0.0, "waiting")

    assert fired == [("123", "10001")]
    store.close()


@pytest.mark.asyncio
async def test_timeout_does_not_fire_after_end() -> None:
    store = SessionStore()
    fired: list[tuple[str, str]] = []

    async def callback(group_id: str, user_id: str) -> None:
        fired.append((group_id, user_id))

    store.set_timeout_callback(callback)
    record = _start(store)
    store.end(record.group_id, record.user_id, status="approved")

    await store._run_timeout(("123", "10001"), 0.0, "waiting")
    assert fired == []
    store.close()


@pytest.mark.asyncio
async def test_start_overwrites_previous_session() -> None:
    store = SessionStore()
    _start(store, user="10001")
    _start(store, user="10001")

    assert len(store._sessions) == 1
    assert len(store._timeout_tasks) == 1
    store.close()


@pytest.mark.asyncio
async def test_await_admin_transitions_and_schedules() -> None:
    """转入待管理员决策应更新状态并调度管理决策超时。"""
    store = SessionStore()
    _start(store)
    assert len(store._timeout_tasks) == 1

    updated = store.await_admin("123", "10001")
    assert updated is not None
    assert updated.status == "awaiting_admin"
    assert updated.expires_at > datetime.now(UTC)
    assert len(store._timeout_tasks) == 1
    assert store.list_awaiting_admin() == (updated,)
    assert store.list_awaiting_admin("999") == ()
    store.close()


@pytest.mark.asyncio
async def test_await_admin_missing_session_returns_none() -> None:
    store = SessionStore()
    assert store.await_admin("123", "99999") is None


@pytest.mark.asyncio
async def test_admin_timeout_callback_fires() -> None:
    store = SessionStore()
    fired: list[tuple[str, str]] = []

    async def callback(group_id: str, user_id: str) -> None:
        fired.append((group_id, user_id))

    store.set_admin_timeout_callback(callback)
    _start(store)
    store.await_admin("123", "10001")
    await store._run_timeout(("123", "10001"), 0.0, "awaiting_admin")

    assert fired == [("123", "10001")]
    store.close()


@pytest.mark.asyncio
async def test_admin_timeout_does_not_fire_when_member_timeout_pending() -> None:
    """awaiting_admin 状态不会触发成员响应超时回调。"""
    store = SessionStore()
    member_fired: list[tuple[str, str]] = []
    admin_fired: list[tuple[str, str]] = []

    async def member_cb(group_id: str, user_id: str) -> None:
        member_fired.append((group_id, user_id))

    async def admin_cb(group_id: str, user_id: str) -> None:
        admin_fired.append((group_id, user_id))

    store.set_timeout_callback(member_cb)
    store.set_admin_timeout_callback(admin_cb)
    _start(store)
    store.await_admin("123", "10001")

    await store._run_timeout(("123", "10001"), 0.0, "waiting")
    assert member_fired == []
    assert admin_fired == []
    store.close()


@pytest.mark.asyncio
async def test_restore_reschedules_timeout() -> None:
    """Restore 应把持久化会话恢复并重建对应超时调度。"""
    from datetime import timedelta

    store = SessionStore()

    async def member_cb(group_id: str, user_id: str) -> None:
        _ = (group_id, user_id)

    async def admin_cb(group_id: str, user_id: str) -> None:
        _ = (group_id, user_id)

    store.set_timeout_callback(member_cb)
    store.set_admin_timeout_callback(admin_cb)

    now = datetime.now(UTC)
    waiting = SessionRecord(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
        trigger_time=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=4),
        status="waiting",
    )
    awaiting = SessionRecord(
        group_id="123",
        user_id="20001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
        trigger_time=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=15),
        status="awaiting_admin",
    )

    store.restore(waiting)
    store.restore(awaiting)
    assert len(store._timeout_tasks) == 2
    assert store.get("123", "10001").status == "waiting"  # type: ignore[union-attr]
    assert store.get("123", "20001").status == "awaiting_admin"  # type: ignore[union-attr]
    store.close()


def test_get_session_store_singleton() -> None:
    assert get_session_store() is get_session_store()


@pytest.mark.asyncio
async def test_scheduled_timeout_cancelled_by_end() -> None:
    """已调度超时任务在会话结束后不应触发。"""
    store = SessionStore()
    fired: list[tuple[str, str]] = []

    async def callback(group_id: str, user_id: str) -> None:
        fired.append((group_id, user_id))

    store.set_timeout_callback(callback)
    _start(store)
    store.end("123", "10001", status="approved")

    # 给取消时间片
    await asyncio.sleep(0)
    await asyncio.sleep(0.01)
    assert fired == []
    store.close()


@pytest.mark.asyncio
async def test_set_muted_updates_state() -> None:
    store = SessionStore()
    _start(store)

    updated = store.set_muted("123", "10001", is_muted=True)
    assert updated is not None
    assert updated.is_muted is True
    after_set = store.get("123", "10001")
    assert after_set is not None
    assert after_set.is_muted is True

    store.set_muted("123", "10001", is_muted=False)
    after_unset = store.get("123", "10001")
    assert after_unset is not None
    assert after_unset.is_muted is False
    store.close()


@pytest.mark.asyncio
async def test_set_muted_missing_session_returns_none() -> None:
    store = SessionStore()
    assert store.set_muted("123", "99999", is_muted=True) is None


@pytest.mark.asyncio
async def test_remove_group_clears_all_sessions() -> None:
    store = SessionStore()
    _start(store, user="10001")
    _start(store, user="10002")
    _start(store, user="10003")
    _start(store, user="10004")

    removed = store.remove_group("123")
    assert len(removed) == 4
    assert store.get("123", "10001") is None
    assert store.get("123", "10002") is None
    assert store.get("123", "10003") is None
    assert store.get("123", "10004") is None
    assert store.list_waiting() == ()
    assert len(store._timeout_tasks) == 0
    store.close()


@pytest.mark.asyncio
async def test_mark_retry_resets_timeout_window() -> None:
    """失败重试应重置超时截止时间并重新调度超时任务。"""
    store = SessionStore()
    record = _start(store)
    old_expires = record.expires_at

    updated = store.mark_retry("123", "10001")
    assert updated is not None
    assert updated.retry_count == 1
    assert updated.expires_at > old_expires  # 窗口被重置到新的完整时长
    assert len(store._timeout_tasks) == 1  # 超时任务重新调度（无重复）
    store.close()


@pytest.mark.asyncio
async def test_mark_review_increments() -> None:
    """重审计数累加。"""
    store = SessionStore()
    _start(store)

    updated = store.mark_review("123", "10001")
    assert updated is not None
    assert updated.review_count == 1
    updated = store.mark_review("123", "10001")
    assert updated is not None
    assert updated.review_count == 2
    store.close()


@pytest.mark.asyncio
async def test_set_review_count_writes_back() -> None:
    """重审重开流程后写回计数。"""
    store = SessionStore()
    _start(store)
    store.mark_review("123", "10001")

    updated = store.set_review_count("123", "10001", 5)
    assert updated is not None
    assert updated.review_count == 5
    store.close()


def _waiting_record(*, expires_at: datetime) -> SessionRecord:
    return SessionRecord(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
        trigger_time=datetime.now(UTC),
        expires_at=expires_at,
        status="awaiting_admin",
    )


@pytest.mark.asyncio
async def test_await_admin_schedules_reminders() -> None:
    """转入待管理员决策应调度被移出前提醒任务（默认 3600/300s）。"""
    store = SessionStore()
    fired: list[tuple[str, str, int]] = []

    async def reminder_cb(group_id: str, user_id: str, remaining: int) -> None:
        fired.append((group_id, user_id, remaining))

    store.set_reminder_callback(reminder_cb)
    _start(store)
    store.await_admin("123", "10001")

    assert len(store._reminder_tasks["123", "10001"]) == 2  # 1h 与 5min
    store.close()


@pytest.mark.asyncio
async def test_reminder_skipped_when_already_passed() -> None:
    """剩余时间不足的提前量不应调度提醒任务。"""
    from datetime import timedelta

    store = SessionStore()
    fired: list[tuple[str, str, int]] = []

    async def reminder_cb(group_id: str, user_id: str, remaining: int) -> None:
        fired.append((group_id, user_id, remaining))

    store.set_reminder_callback(reminder_cb)
    _start(store)
    store.await_admin("123", "10001")

    # 剩余仅 2 分钟（< 5min），1h 与 5min 两个提前量均已过，不调度提醒
    now = datetime.now(UTC)
    store.restore(_waiting_record(expires_at=now + timedelta(minutes=2)))
    assert store._reminder_tasks.get(("123", "10001"), []) == []
    store.close()


@pytest.mark.asyncio
async def test_run_reminder_fires_callback() -> None:
    """触发提醒任务应调用提醒回调（仍在 awaiting_admin）。"""
    from datetime import timedelta

    store = SessionStore()
    fired: list[tuple[str, str, int]] = []

    async def reminder_cb(group_id: str, user_id: str, remaining: int) -> None:
        fired.append((group_id, user_id, remaining))

    store.set_reminder_callback(reminder_cb)
    now = datetime.now(UTC)
    store.restore(_waiting_record(expires_at=now + timedelta(seconds=30)))

    await store._run_reminder(("123", "10001"), ahead=3600)  # 提前量>剩余→立即触发
    assert len(fired) == 1
    assert fired[0][0] == "123" and fired[0][1] == "10001"
    store.close()


@pytest.mark.asyncio
async def test_run_reminder_no_fire_after_end() -> None:
    """会话结束后提醒不应触发。"""
    from datetime import timedelta

    store = SessionStore()
    fired: list[tuple[str, str, int]] = []

    async def reminder_cb(group_id: str, user_id: str, remaining: int) -> None:
        fired.append((group_id, user_id, remaining))

    store.set_reminder_callback(reminder_cb)
    now = datetime.now(UTC)
    store.restore(_waiting_record(expires_at=now + timedelta(seconds=30)))
    store.end("123", "10001", status="kicked")

    await store._run_reminder(("123", "10001"), ahead=3600)
    assert fired == []
    store.close()


@pytest.mark.asyncio
async def test_reminder_tasks_cancelled_on_end() -> None:
    """会话结束应取消已调度的提醒任务。"""
    store = SessionStore()

    async def reminder_cb(group_id: str, user_id: str, remaining: int) -> None:
        _ = (group_id, user_id, remaining)

    store.set_reminder_callback(reminder_cb)
    _start(store)
    store.await_admin("123", "10001")
    assert len(store._reminder_tasks["123", "10001"]) == 2

    store.end("123", "10001", status="approved")
    assert ("123", "10001") not in store._reminder_tasks
    store.close()


@pytest.mark.asyncio
async def test_list_waiting_by_user_filters() -> None:
    """按账号列出某用户所有待验证群。"""
    store = SessionStore()
    _start(store, user="10001")
    _start(store, user="10002")

    rows = store.list_waiting_by_user("10001")
    assert len(rows) == 1
    assert rows[0].user_id == "10001"
    store.close()


@pytest.mark.asyncio
async def test_list_waiting_by_group_filters() -> None:
    """按群列出 waiting 会话（处理中列表用）。"""
    store = SessionStore()
    _start(store, user="10001")  # group 123
    store.start(
        group_id="456",
        user_id="20001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    store.await_admin("123", "10001")  # 转 awaiting_admin，不再是 waiting

    assert store.list_waiting_by_group("123") == ()
    rows = store.list_waiting_by_group("456")
    assert len(rows) == 1
    assert rows[0].user_id == "20001"
    store.close()


@pytest.mark.asyncio
async def test_try_claim_and_release() -> None:
    """try_claim 应原子防并发：首次成功，处理中再取失败，release 后可再取。"""
    store = SessionStore()
    assert store.try_claim("123", "10001") is True
    assert store.try_claim("123", "10001") is False
    store.release("123", "10001")
    assert store.try_claim("123", "10001") is True
    store.close()


@pytest.mark.asyncio
async def test_private_target_get_set_clear() -> None:
    """私聊验证目标群选择状态的存取与清除。"""
    store = SessionStore()
    assert store.get_private_target("10001") is None

    store.set_private_target("10001", "456")
    assert store.get_private_target("10001") == "456"

    store.clear_private_target("10001")
    assert store.get_private_target("10001") is None
    store.close()


def test_last_image_message_roundtrip() -> None:
    """记录/读取成员最后提交的图片消息 id（供超时通报引用原消息）。"""
    store = SessionStore()
    assert store.get_last_image_message("123", "10001") is None

    store.set_last_image_message("123", "10001", 42)
    assert store.get_last_image_message("123", "10001") == 42

    # 覆盖写：同一会话只保留最新一条
    store.set_last_image_message("123", "10001", 43)
    assert store.get_last_image_message("123", "10001") == 43

    # 其他会话互不影响
    assert store.get_last_image_message("123", "20002") is None
