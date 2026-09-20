"""数据库 CRUD 共享基础：DatabaseError 与内部辅助函数。"""

from __future__ import annotations

from collections.abc import Sequence
import logging
from typing import TYPE_CHECKING, Any

from nonebot import require

require("nonebot_plugin_orm")
from nonebot_plugin_orm import Model
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.inspection import inspect

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement, UnaryExpression

logger = logging.getLogger(__name__)
ROWCOUNT_UNKNOWN = -1


class DatabaseError(Exception):
    """数据库操作失败时使用的统一异常。

    该异常只表示持久化层操作失败，不区分具体数据库驱动，也不封装
    业务语义。调用方应将其视为“数据库请求失败”，而不是“记录不存在”
    或“参数校验失败”。
    """


def _is_fk_constraint_violation(e: IntegrityError) -> bool:
    """判断完整性错误是否为外键约束冲突。

    Args:
        e: SQLAlchemy 完整性错误对象。

    Returns:
        若判断为外键冲突则返回 True，否则返回 False。
    """
    orig = e.orig
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate is not None:
        try:
            return str(sqlstate) == "23503"
        except (AttributeError, TypeError):
            pass
    msg = str(orig).lower()
    return "foreign key" in msg


def _conds[T: Model](
    model: type[T],
    filters: dict[str, Any] | None,
) -> list[ColumnElement[bool]]:
    """构造筛选条件表达式列表。

    Args:
        model: ORM 模型类。
        filters: 字段到筛选值的映射，None 表示不加条件。

    Returns:
        SQLAlchemy 布尔表达式列表。

    Raises:
        ValueError: 筛选字段不存在时。
    """
    if not filters:
        return []
    columns = _get_column_map(model)
    c: list[ColumnElement[bool]] = []
    for k, v in filters.items():
        col = columns.get(k)
        if col is None:
            raise ValueError(f"Unknown column '{k}' for model '{model.__name__}'")
        if v is None:
            c.append(col.is_(None))
        elif isinstance(v, Sequence) and not isinstance(v, (str, bytes)):
            if len(v) == 0:
                logger.warning(
                    "Filter '%s' uses an empty sequence; will generate `col.in_([])`, "
                    "which never matches any records",
                    k,
                )
            c.append(col.in_(list(v)))
        else:
            c.append(col == v)
    return c


def _orders[T: Model](
    model: type[T],
    order_by: Sequence[str] | None,
) -> list[UnaryExpression[Any]]:
    """构造排序表达式列表。

    Args:
        model: ORM 模型类。
        order_by: 排序字段列表，前缀为 - 表示降序。

    Returns:
        排序表达式列表。
    """
    if not order_by:
        return []
    o: list[UnaryExpression[Any]] = []
    for key in order_by:
        if key.startswith("-"):
            col = getattr(model, key[1:], None)
            if col is not None:
                o.append(col.desc())
            else:
                logger.warning(
                    "Sort field '%s' not found on model '%s', ignored",
                    key[1:],
                    model.__name__,
                )
        else:
            col = getattr(model, key, None)
            if col is not None:
                o.append(col.asc())
            else:
                logger.warning(
                    "Sort field '%s' not found on model '%s', ignored",
                    key,
                    model.__name__,
                )
    return o


def _get_column_map[T: Model](model: type[T]) -> dict[str, Any]:
    """获取模型可用的数据库列映射。

    Args:
        model: ORM 模型类。

    Returns:
        列名到列对象的映射。
    """
    try:
        mapper = inspect(model)
        return {c.key: getattr(model, c.key) for c in mapper.columns}
    except (AttributeError, SQLAlchemyError, TypeError):
        logger.debug(
            "Cannot inspect columns for model %s; falling back to class attributes",
            model.__name__,
        )

    annotations = getattr(model, "__annotations__", {})
    return {
        name: getattr(model, name)
        for name in annotations
        if not name.startswith("_") and hasattr(model, name)
    }


def _combined_conditions[T: Model](
    model: type[T],
    filters: dict[str, Any] | None,
    conditions: Sequence[ColumnElement[bool]] | None = None,
    *,
    require_non_empty: bool,
) -> list[ColumnElement[bool]]:
    """合并字典筛选条件和直接传入的 SQLAlchemy 条件。"""
    combined = _conds(model, filters)
    if conditions:
        combined.extend(conditions)
    if require_non_empty and not combined:
        raise ValueError(f"At least one condition is required for {model.__name__}")
    return combined


def _validate_column_values[T: Model](
    model: type[T],
    values: dict[str, Any],
) -> dict[str, Any]:
    """校验待写入字段均为模型列。"""
    columns = _get_column_map(model)
    for key in values:
        if key not in columns:
            raise ValueError(f"Unknown column '{key}' for model '{model.__name__}'")
    return dict(values)
