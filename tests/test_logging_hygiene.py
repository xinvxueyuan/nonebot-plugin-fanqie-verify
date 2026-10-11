"""静态守卫：loguru 日志里禁止 %-style 占位符（参数会被静默丢弃）。

背景：`from nonebot import logger` 得到的是 loguru logger，插值走 ``{}``；
写成 stdlib 风格的 ``logger.warning("x: %s", exc)`` 时消息原样打印、实参被
静默忽略 —— 2026-10-11 视觉故障排查因此丢失了异常原文。本守卫用 AST 扫描
所有 loguru 文件，杜绝再犯。
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "plugins"
    / "nonebot_plugin_fanqie_verify"
)
LOG_LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
PLACEHOLDER = re.compile(r"%[sdrif]")


def _uses_loguru(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = {alias.name for alias in node.names}
            if node.module == "nonebot" and "logger" in names:
                return True
            if node.module.startswith("nonebot.log"):
                return True
    return False


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if not _uses_loguru(tree):
        return []
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in LOG_LEVELS:
            continue
        value = func.value
        is_logger = isinstance(value, ast.Name) and value.id == "logger"
        is_opt_chain = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "logger"
            and value.func.attr == "opt"
        )
        if not (is_logger or is_opt_chain):
            continue
        # 无额外实参时，%s 只是字面文本；真正丢参数的是「消息 + 实参」形态。
        if len(node.args) < 2:
            continue
        first = node.args[0]
        if (
            isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and PLACEHOLDER.search(first.value)
        ):
            bad.append(f"{path.name}:{node.lineno}: {first.value[:70]}")
    return bad


def test_loguru_files_scanned_minimum() -> None:
    """扫描覆盖数不得静默坍缩（守卫自身的防失效断言）。"""
    scanned = [
        p
        for p in ROOT.rglob("*.py")
        if _uses_loguru(ast.parse(p.read_text(encoding="utf-8")))
    ]
    assert len(scanned) >= 9, f"loguru 文件仅扫到 {len(scanned)} 个，守卫失效"


def test_no_percent_placeholders_in_loguru_calls() -> None:
    violations = [v for p in ROOT.rglob("*.py") for v in _violations(p)]
    assert not violations, "loguru 调用中禁止 %-style 占位符：\n" + "\n".join(
        violations
    )
