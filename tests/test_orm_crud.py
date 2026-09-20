"""orm_crud 包在 SQLite 上的 CRUD 操作测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from nonebot import require
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

require("nonebot_plugin_orm")

from src.plugins.nonebot_plugin_fanqie_verify.database.models import (
    VerificationSession,
)
from src.plugins.nonebot_plugin_fanqie_verify.database.orm_crud import (
    count,
    create,
    delete,
    exists,
    get_one,
    get_or_create,
    list_items,
    update,
    upsert,
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """创建内存 SQLite 引擎与表结构的异步会话。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(VerificationSession.metadata.drop_all)
        await conn.run_sync(VerificationSession.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _sample(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "platform_id": "qq",
        "adapter_id": "~onebot.v11",
        "protocol_id": "default",
        "framework_id": "nonebot",
        "bot_id": "10001",
        "group_id": "20001",
        "user_id": "30001",
        "status": "waiting",
        "retry_count": 0,
        "is_muted": False,
        "last_extracted": {"book_name": "番茄小说", "chapter": "第1章"},
        "trigger_time": datetime.now(UTC),
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_create_and_get_one(session: AsyncSession) -> None:
    """Create 后可经 get_one 按身份字段取回。"""
    obj = await create(session, VerificationSession, **_sample())
    found = await get_one(
        session,
        VerificationSession,
        {"group_id": "20001", "user_id": "30001"},
    )
    assert found is not None
    assert found.id == obj.id
    assert found.status == "waiting"


@pytest.mark.asyncio
async def test_get_one_returns_none_when_missing(session: AsyncSession) -> None:
    """不存在的记录应返回 None。"""
    found = await get_one(session, VerificationSession, {"user_id": "nobody"})
    assert found is None


@pytest.mark.asyncio
async def test_update(session: AsyncSession) -> None:
    """Update 应返回受影响行数并更新字段。"""
    await create(session, VerificationSession, **_sample())
    affected, known = await update(
        session,
        VerificationSession,
        {"group_id": "20001"},
        {"status": "approved", "is_muted": True},
    )
    assert known
    assert affected == 1
    updated = await get_one(session, VerificationSession, {"group_id": "20001"})
    assert updated is not None
    assert updated.status == "approved"
    assert updated.is_muted is True


@pytest.mark.asyncio
async def test_delete(session: AsyncSession) -> None:
    """Delete 应移除匹配记录。"""
    await create(session, VerificationSession, **_sample())
    affected, known = await delete(session, VerificationSession, {"user_id": "30001"})
    assert known
    assert affected == 1
    assert await count(session, VerificationSession) == 0


@pytest.mark.asyncio
async def test_exists_and_count(session: AsyncSession) -> None:
    """Exists 与 count 应反映记录状态。"""
    assert await exists(session, VerificationSession, {"user_id": "30001"}) is False
    await create(session, VerificationSession, **_sample())
    assert await exists(session, VerificationSession, {"user_id": "30001"}) is True
    assert await count(session, VerificationSession) == 1


@pytest.mark.asyncio
async def test_get_or_create_existing(session: AsyncSession) -> None:
    """记录已存在时 get_or_create 应返回已有对象与 created=False。"""
    await create(session, VerificationSession, **_sample())
    obj, created = await get_or_create(
        session,
        VerificationSession,
        defaults={"status": "waiting"},
        group_id="20001",
        user_id="30001",
    )
    assert created is False
    assert obj.user_id == "30001"
    assert await count(session, VerificationSession) == 1


@pytest.mark.asyncio
async def test_get_or_create_new(session: AsyncSession) -> None:
    """记录不存在时 get_or_create 应创建并返回 created=True。"""
    obj, created = await get_or_create(
        session,
        VerificationSession,
        defaults={**_sample(), "status": "waiting"},
        group_id="20001",
        user_id="30001",
    )
    assert created is True
    assert obj.id is not None


@pytest.mark.asyncio
async def test_upsert_insert(session: AsyncSession) -> None:
    """首次 upsert 应插入记录。"""
    obj = await upsert(
        session,
        VerificationSession,
        _sample(),
        conflict_fields=["group_id", "user_id"],
    )
    assert obj.id is not None
    assert await count(session, VerificationSession) == 1


@pytest.mark.asyncio
async def test_upsert_conflict_updates(session: AsyncSession) -> None:
    """冲突时 upsert 应更新已有记录而非新增。"""
    await upsert(
        session,
        VerificationSession,
        _sample(),
        conflict_fields=["group_id", "user_id"],
    )
    updated = await upsert(
        session,
        VerificationSession,
        _sample(status="approved", retry_count=2),
        conflict_fields=["group_id", "user_id"],
    )
    assert updated.status == "approved"
    assert updated.retry_count == 2
    assert await count(session, VerificationSession) == 1


@pytest.mark.asyncio
async def test_list_items_ordering(session: AsyncSession) -> None:
    """list_items 应按排序字段返回结果。"""
    await create(session, VerificationSession, **_sample(user_id="30001"))
    await create(session, VerificationSession, **_sample(user_id="30002"))
    await create(session, VerificationSession, **_sample(user_id="30003"))
    items = await list_items(
        session,
        VerificationSession,
        order_by=["-user_id"],
        limit=2,
    )
    assert [item.user_id for item in items] == ["30003", "30002"]


@pytest.mark.asyncio
async def test_list_pending_sessions_filters_status(session: AsyncSession) -> None:
    """list_pending_sessions 应只返回 waiting 与 awaiting_admin。"""
    from src.plugins.nonebot_plugin_fanqie_verify.repositories import (
        message_store as repo,
    )

    await create(session, VerificationSession, **_sample(user_id="30001"))
    await create(
        session,
        VerificationSession,
        **_sample(user_id="30002", status="awaiting_admin"),
    )
    await create(
        session,
        VerificationSession,
        **_sample(user_id="30003", status="approved"),
    )
    await create(
        session,
        VerificationSession,
        **_sample(user_id="30004", status="kicked"),
    )

    pending = await repo.list_pending_sessions(session)
    assert {item.user_id for item in pending} == {"30001", "30002"}


@pytest.mark.asyncio
async def test_create_rejects_unknown_column(session: AsyncSession) -> None:
    """未知字段应抛出 ValueError。"""
    with pytest.raises(ValueError):
        await create(session, VerificationSession, **{**_sample(), "nope": 1})
