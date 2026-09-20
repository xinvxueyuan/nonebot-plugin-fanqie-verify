"""插件加载冒烟测试：验证插件元数据、事件响应器注册与配置生成。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.nonebot_plugin_fanqie_verify import __plugin_meta__
from src.plugins.nonebot_plugin_fanqie_verify.config import Config
from src.plugins.nonebot_plugin_fanqie_verify.handle.qq.commands import (
    verification as cmd_module,
)

pytestmark = pytest.mark.smoke


def test_plugin_metadata() -> None:
    """插件元数据应完整。"""
    assert __plugin_meta__.name == "番茄读书入群验证"
    assert __plugin_meta__.description
    assert __plugin_meta__.usage
    assert __plugin_meta__.supported_adapters == {"nonebot.adapters.onebot.v11"}


def test_config_defaults() -> None:
    """关键配置项应带有合理默认值。"""
    from nonebot import get_plugin_config

    cfg = get_plugin_config(Config)
    assert cfg.fanqie_response_timeout > 0
    assert cfg.fanqie_max_attempts > 0
    assert cfg.fanqie_ocr_model == "PaddleOCR-VL-1.6"


def test_verification_matchers_registered() -> None:
    """核心事件响应器应已创建。"""
    for matcher in (
        cmd_module.group_increase,
        cmd_module.group_decrease,
        cmd_module.group_ban,
        cmd_module.group_admin_change,
        cmd_module.image_submission,
        cmd_module.kick_cmd,
        cmd_module.keep_cmd,
        cmd_module.reload_config_cmd,
    ):
        assert matcher is not None
        assert matcher.handlers


def test_policy_toml_generates(tmp_path: Path) -> None:
    """默认策略 TOML 应能生成并加载。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        load_policy,
    )

    path = tmp_path / "verification_policy.toml"
    policy = load_policy(path)
    assert policy.require_all is False
    assert policy.required_elements == frozenset({"book_name", "author"})
    assert policy.groups == {}
