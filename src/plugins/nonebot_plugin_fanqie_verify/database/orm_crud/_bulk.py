"""批量与 upsert 操作：bulk_create、upsert、list_items。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from nonebot import require

require("nonebot_plugin_orm")
from nonebot_plugin_orm import Model
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from ._base import (
    DatabaseError,
    _combined_conditions,
    _get_column_map,
    _orders,
    _validate_column_values,
    logger,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_scoped_session
    from sqlalchemy.sql.elements import ColumnElement


async def bulk_create[T: Model](
    session: AsyncSession | async_scoped_session[AsyncSession],
    model: type[T],
    objs: list[dict[str, Any]],
    *,
    commit: bool = True,
    partial: bool = False,
) -> tuple[list[T], list[tuple[int, str]]]:
    """批量创建多条记录。

    Args:
        session: 异步会话。
        model: ORM 模型类。
        objs: 待创建字段字典列表。
        commit: 是否在 flush 后刷新对象。
        partial: 是否允许部分成功，跳过失败行继续。

    Returns:
        (成功对象列表, 失败项列表)。

    Raises:
        DatabaseError: partial 为 False 且批量创建失败时。
    """
    validated_objs = [_validate_column_values(model, fields) for fields in objs]
    if not partial:
        instances = [model(**fields) for fields in validated_objs]
        session.add_all(instances)
        try:
            await session.flush()
            if commit:
                for obj in instances:
                    await session.refresh(obj)
        except SQLAlchemyError as e:
            raise DatabaseError("Bulk create failed") from e
        return instances, []

    # 部分成功模式：逐条使用 savepoint 隔离失败。
    created: list[T] = []
    failed: list[tuple[int, str]] = []
    for idx, fields in enumerate(validated_objs):
        savepoint = await session.begin_nested()
        try:
            obj = model(**fields)
            session.add(obj)
            await savepoint.commit()
            created.append(obj)
        except SQLAlchemyError as exc:
            await savepoint.rollback()
            msg = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Skipped item %d in bulk_create (partial=True): %s", idx, msg
            )
            failed.append((idx, msg))
    try:
        await session.flush()
        if commit:
            for obj in created:
                await session.refresh(obj)
    except SQLAlchemyError as e:
        raise DatabaseError("Bulk create failed") from e
    return created, failed


async def upsert[T: Model](
    session: AsyncSession | async_scoped_session[AsyncSession],
    model: type[T],
    insert_values: dict[str, Any],
    *,
    conflict_fields: Sequence[str],
    update_values: dict[str, Any] | None = None,
) -> T:
    """执行 SQLite 方言的原子 upsert（``INSERT ... ON CONFLICT``）。

    Args:
        session: 异步会话。
        model: ORM 模型类。
        insert_values: 插入字段。
        conflict_fields: 唯一冲突字段。
        update_values: 冲突时更新的字段；默认使用插入值本身。

    Returns:
        upsert 后返回的 ORM 对象（SQLite 3.35+ 支持 RETURNING）。

    Raises:
        ValueError: 参数或字段非法时。
        DatabaseError: 数据库执行失败时。
    """
    if not insert_values:
        raise ValueError("insert_values cannot be empty")
    if not conflict_fields:
        raise ValueError("An upsert conflict target is required")

    insert_values = _validate_column_values(model, insert_values)
    columns = _get_column_map(model)
    conflict_keys = list(conflict_fields)
    for key in conflict_keys:
        if key not in columns:
            raise ValueError(f"Unknown column '{key}' for model '{model.__name__}'")

    if update_values is not None:
        explicit_update_values = _validate_column_values(model, update_values)
        if not explicit_update_values:
            raise ValueError("At least one update column is required for upsert")
    else:
        update_keys = [key for key in insert_values if key not in conflict_keys]
        if not update_keys:
            raise ValueError("At least one update column is required for upsert")
        explicit_update_values = {
            key: getattr(sqlite_insert(model).values(**insert_values).excluded, key)
            for key in update_keys
        }

    stmt = sqlite_insert(model).values(**insert_values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[columns[key] for key in conflict_keys],
        set_=explicit_update_values,
    )
    stmt = stmt.returning(model)

    try:
        result = await session.execute(stmt)
        obj = result.scalar_one()
        await session.flush()
    except SQLAlchemyError as e:
        raise DatabaseError("Upsert failed") from e
    return obj


async def list_items[T: Model](
    session: AsyncSession | async_scoped_session[AsyncSession],
    model: type[T],
    filters: dict[str, Any] | None = None,
    order_by: Sequence[str] | None = None,
    *,
    conditions: Sequence[ColumnElement[bool]] | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[T]:
    """列出符合条件的记录。

    Args:
        session: 异步会话。
        model: ORM 模型类。
        filters: 筛选条件。
        order_by: 排序字段。
        conditions: 额外的 SQLAlchemy 列条件。
        offset: 偏移量。
        limit: 限制数量。

    Returns:
        模型对象列表。

    Raises:
        DatabaseError: 查询失败时。
    """
    cs = _combined_conditions(
        model,
        filters,
        conditions,
        require_non_empty=False,
    )
    try:
        stmt = select(model)
        if cs:
            stmt = stmt.where(*cs)
        os = _orders(model, order_by)
        if os:
            stmt = stmt.order_by(*os)
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())
    except SQLAlchemyError as e:
        raise DatabaseError("Failed to list records") from e
