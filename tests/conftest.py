"""pytest 配置与 NoneBot 初始化。

此模块负责配置 pytest 测试框架与 NoneBot 异步驱动程序。

"""

from __future__ import annotations

import os
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from nonebug import NONEBOT_START_LIFESPAN
import pytest

_PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"
_LOCALSTORE_ROOT = Path(".pytest-localstore") / "master"


def _disable_nonebug_auto_lifespan(config: pytest.Config) -> None:
    """Keep nonebug from starting the full driver lifespan for every test run."""
    config.stash[NONEBOT_START_LIFESPAN] = True


def pytest_configure(config: pytest.Config) -> None:
    """在收集测试之前初始化 NoneBot 驱动。

    Args:
        config: pytest 的配置对象。

    """
    os.environ.setdefault("ENVIRONMENT", "test")
    _disable_nonebug_auto_lifespan(config)

    try:
        nonebot.get_driver()
    except ValueError:
        pass
    else:
        return

    os.environ.setdefault("ALEMBIC_STARTUP_CHECK", "false")
    os.environ.setdefault("SQLALCHEMY_DATABASE_URL", "sqlite+aiosqlite:///fanqie.db")
    os.environ.setdefault("SUPERUSERS", '["1330509996"]')
    os.environ.setdefault("FANQIE_ADMIN_IDS", "[1330509996]")
    # 补验默认关闭：on_bot_connect 的补验会在后台调用群成员列表等 API，
    # 干扰测试对 API 调用序列的断言。补验相关测试自行开启并 mock。
    os.environ.setdefault("FANQIE_BACKFILL_ENABLED", "false")
    # COMMAND_START 必须走环境变量（JSON 形式），nonebot 才能保留空字符串前缀；
    # 直接以 Python list 参数传入时空字符串会被丢弃，导致裸命令（无 / 前缀）不匹配。
    os.environ.setdefault("COMMAND_START", '["", "/"]')

    nonebot.init(
        DRIVER="~fastapi+~httpx+~websockets",
        localstore_cache_dir=_LOCALSTORE_ROOT / "cache",
        localstore_config_dir=_LOCALSTORE_ROOT / "config",
        localstore_data_dir=_LOCALSTORE_ROOT / "data",
        localstore_use_cwd=False,
    )

    driver = nonebot.get_driver()
    driver.register_adapter(adapter=ONEBOT_V11Adapter)

    nonebot.load_from_toml(file_path=str(_PYPROJECT))


@pytest.fixture(autouse=True)
def _fresh_config_files() -> None:
    """每个测试前删除策略文件，保证从代码默认值重新生成。"""
    from nonebot_plugin_localstore import get_config_file

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification.policy import (
        _PLUGIN_NAME,
        _POLICY_FILENAME,
    )

    get_config_file(_PLUGIN_NAME, _POLICY_FILENAME).unlink(missing_ok=True)


def pytest_unconfigure() -> None:
    """清理 NoneBot 驱动状态。

    防止残留的 NoneBot 驱动实例对后续测试或其他进程造成干扰。

    """
    try:
        nonebot.get_driver()
    except ValueError:
        return
    nonebot._driver = None
