"""TOML 字典文件的同步与异步读写助手。"""

from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
import os
from pathlib import Path
import tempfile
from typing import Any

import aiofiles
import aiofiles.os
import rtoml

from ._helpers import _deepcopy_async, _toml_dumps, _toml_dumps_async, _toml_loads_async
from .exceptions import (
    InvalidTOMLRootTypeError,
    TOMLFileReadError,
    TOMLSerializationError,
)


def load_toml_dict_sync(
    file_path: str | Path,
    *,
    default: dict[str, Any] | None = None,
    merge_default: bool = False,
) -> dict[str, Any]:
    """同步读取 TOML 表，用于导入期初始化。

    Args:
        file_path: TOML 文件路径。
        default: 文件不存在时的默认值。
        merge_default: 是否与默认值合并（默认值优先被覆盖）。

    Returns:
        读取到的字典。

    Raises:
        TOMLFileReadError: 读取或解析失败时。
        InvalidTOMLRootTypeError: 根对象不是字典时。
    """
    path = Path(file_path)
    default_copy = deepcopy(default) if default is not None else {}
    if not path.exists():
        return default_copy
    try:
        content = path.read_text(encoding="utf-8")
        loaded: Any = rtoml.loads(content) if content.strip() else default_copy
    except (OSError, ValueError) as exc:
        raise TOMLFileReadError(path, exc) from exc
    if not isinstance(loaded, dict):
        raise InvalidTOMLRootTypeError(path, type(loaded))
    return default_copy | loaded if merge_default else loaded


async def load_toml_dict_async(
    file_path: str | Path,
    *,
    default: dict[str, Any] | None = None,
    merge_default: bool = False,
) -> dict[str, Any]:
    """异步读取 TOML 表，避免阻塞事件循环。

    Args:
        file_path: TOML 文件路径。
        default: 文件不存在时的默认值。
        merge_default: 是否与默认值合并（默认值优先被覆盖）。

    Returns:
        读取到的字典。

    Raises:
        TOMLFileReadError: 读取或解析失败时。
        InvalidTOMLRootTypeError: 根对象不是字典时。
    """
    path = Path(file_path)
    default_copy = await _deepcopy_async(default if default is not None else {})
    if not await aiofiles.os.path.exists(path):
        return default_copy
    try:
        async with aiofiles.open(path, encoding="utf-8") as file:
            content = await file.read()
        loaded: Any = (
            await _toml_loads_async(content) if content.strip() else default_copy
        )
    except (OSError, ValueError) as exc:
        raise TOMLFileReadError(path, exc) from exc
    if not isinstance(loaded, dict):
        raise InvalidTOMLRootTypeError(path, type(loaded))
    return default_copy | loaded if merge_default else loaded


def ensure_toml_dict_file_sync(
    file_path: str | Path,
    default: dict[str, Any],
    *,
    schema_basename: str | None = None,
) -> Path:
    """同步确保 TOML 文件存在，不存在时用默认值原子写入。

    Args:
        file_path: TOML 文件路径。
        default: 默认内容。
        schema_basename: 若有则写入 ``#:schema`` 注释头。

    Returns:
        文件路径。

    Raises:
        TOMLSerializationError: 序列化失败时。
        TOMLFileReadError: 写入失败时。
    """
    path = Path(file_path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        content = _toml_dumps(default, schema_basename=schema_basename)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        temp_path.replace(path)
    except TOMLSerializationError:
        with contextlib.suppress(OSError):
            if temp_path is not None:
                temp_path.unlink()
        raise
    except (OSError, TypeError, ValueError) as exc:
        with contextlib.suppress(OSError):
            if temp_path is not None:
                temp_path.unlink()
        raise TOMLFileReadError(path, exc) from exc
    return path


async def ensure_toml_dict_file_async(
    file_path: str | Path,
    default: dict[str, Any],
    *,
    schema_basename: str | None = None,
) -> Path:
    """异步确保 TOML 文件存在，不存在时用默认值原子写入。

    Args:
        file_path: TOML 文件路径。
        default: 默认内容。
        schema_basename: 若有则写入 ``#:schema`` 注释头。

    Returns:
        文件路径。

    Raises:
        TOMLSerializationError: 序列化失败时。
        TOMLFileReadError: 写入失败时。
    """
    path = Path(file_path)
    if await aiofiles.os.path.exists(path):
        return path
    await aiofiles.os.makedirs(path.parent, exist_ok=True)
    temp_path: Path | None = None
    try:
        content = await _toml_dumps_async(default, schema_basename=schema_basename)
        fd, temp_name = await asyncio.to_thread(
            tempfile.mkstemp,
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(os.fsdecode(temp_name))
        async with aiofiles.open(fd, "w", encoding="utf-8") as file:
            await file.write(content)
        await aiofiles.os.replace(temp_path, path)
    except TOMLSerializationError:
        with contextlib.suppress(OSError):
            if temp_path is not None:
                await aiofiles.os.unlink(temp_path)
        raise
    except (OSError, TypeError, ValueError) as exc:
        with contextlib.suppress(OSError):
            if temp_path is not None:
                await aiofiles.os.unlink(temp_path)
        raise TOMLFileReadError(path, exc) from exc
    return path


async def write_toml_dict_file_async(
    file_path: str | Path,
    data: dict[str, Any],
    *,
    schema_basename: str | None = None,
) -> Path:
    """异步原子写入 TOML 文件。

    Args:
        file_path: TOML 文件路径。
        data: 待写入内容。
        schema_basename: 若有则写入 ``#:schema`` 注释头。

    Returns:
        文件路径。

    Raises:
        TOMLSerializationError: 序列化失败时。
        TOMLFileReadError: 写入失败时。
    """
    path = Path(file_path)
    await aiofiles.os.makedirs(path.parent, exist_ok=True)
    temp_path: Path | None = None
    try:
        content = await _toml_dumps_async(data, schema_basename=schema_basename)
        fd, temp_name = await asyncio.to_thread(
            tempfile.mkstemp,
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(os.fsdecode(temp_name))
        async with aiofiles.open(fd, "w", encoding="utf-8") as file:
            await file.write(content)
        await aiofiles.os.replace(temp_path, path)
    except TOMLSerializationError:
        with contextlib.suppress(OSError):
            if temp_path is not None:
                await aiofiles.os.unlink(temp_path)
        raise
    except (OSError, TypeError, ValueError) as exc:
        with contextlib.suppress(OSError):
            if temp_path is not None:
                await aiofiles.os.unlink(temp_path)
        raise TOMLFileReadError(path, exc) from exc
    return path
