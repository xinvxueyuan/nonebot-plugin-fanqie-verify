"""toml_store 包的异步读写与异常测试。"""

from __future__ import annotations

from pathlib import Path

import aiofiles
import pytest
import rtoml

from src.plugins.nonebot_plugin_fanqie_verify.database.toml_store import (
    InvalidTOMLRootTypeError,
    TOMLFileReadError,
    TOMLSerializationError,
    ensure_toml_dict_file_async,
    ensure_toml_dict_file_sync,
    load_toml_dict_async,
    load_toml_dict_sync,
    write_toml_dict_file_async,
)


@pytest.mark.asyncio
async def test_load_toml_dict_async_default(tmp_path: Path) -> None:
    """文件不存在时返回默认值。"""
    result = await load_toml_dict_async(
        tmp_path / "missing.toml",
        default={"a": 1},
    )
    assert result == {"a": 1}


@pytest.mark.asyncio
async def test_write_and_load_roundtrip(tmp_path: Path) -> None:
    """写入后应能原样读回。"""
    path = tmp_path / "data.toml"
    data = {"book": {"name": "番茄小说", "chapter": "第1章"}, "read_minutes": 12}
    await write_toml_dict_file_async(path, data)
    loaded = await load_toml_dict_async(path)
    assert loaded == data


@pytest.mark.asyncio
async def test_ensure_toml_dict_file_async(tmp_path: Path) -> None:
    """确保文件存在，不存在时写入默认值。"""
    path = await ensure_toml_dict_file_async(
        tmp_path / "nested" / "default.toml",
        {"enabled": True},
        schema_basename="default.schema.json",
    )
    assert path.exists()
    async with aiofiles.open(path, encoding="utf-8") as file:
        content = await file.read()
    assert content.startswith("#:schema ./default.schema.json")
    assert rtoml.loads(content) == {"enabled": True}


@pytest.mark.asyncio
async def test_ensure_existing_file_not_overwritten(tmp_path: Path) -> None:
    """已存在的文件不应被默认值覆盖。"""
    path = tmp_path / "existing.toml"
    path.write_text("custom = 42\n", encoding="utf-8")
    await ensure_toml_dict_file_async(path, {"default": True})
    assert rtoml.loads(path.read_text(encoding="utf-8")) == {"custom": 42}


def test_load_toml_dict_sync(tmp_path: Path) -> None:
    """同步读取应返回文件内容。"""
    path = tmp_path / "sync.toml"
    path.write_text("name = 'sync'\n", encoding="utf-8")
    assert load_toml_dict_sync(path) == {"name": "sync"}


def test_ensure_toml_dict_file_sync(tmp_path: Path) -> None:
    """同步 ensure 应创建文件。"""
    path = ensure_toml_dict_file_sync(
        tmp_path / "sync-default.toml",
        {"x": 1},
    )
    assert path.exists()
    assert load_toml_dict_sync(path) == {"x": 1}


def _fake_loads_as_list(_content: str, _none_value: object = None) -> list[str]:
    """模拟 rtoml.loads 返回列表（触发根类型错误）。"""
    return ["not", "a", "dict"]


@pytest.mark.asyncio
async def test_invalid_toml_root_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """根对象不是字典时应抛出 InvalidTOMLRootTypeError。"""
    path = tmp_path / "array.toml"
    path.write_text("a = 1\n", encoding="utf-8")
    monkeypatch.setattr("rtoml.loads", _fake_loads_as_list)
    with pytest.raises(InvalidTOMLRootTypeError):
        await load_toml_dict_async(path)


@pytest.mark.asyncio
async def test_read_error_on_invalid_toml(tmp_path: Path) -> None:
    """非法 TOML 内容应抛出 TOMLFileReadError。"""
    path = tmp_path / "bad.toml"
    path.write_text("== not valid toml ==", encoding="utf-8")
    with pytest.raises(TOMLFileReadError):
        await load_toml_dict_async(path)


@pytest.mark.asyncio
async def test_none_inside_list_raises(tmp_path: Path) -> None:
    """列表内的 None 无法表示，应抛出 TOMLSerializationError。"""
    with pytest.raises(TOMLSerializationError):
        await write_toml_dict_file_async(tmp_path / "x.toml", {"items": [None]})
