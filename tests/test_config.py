"""番茄读书入群验证插件配置测试。"""

from __future__ import annotations

from nonebot import get_plugin_config
import pytest

from src.plugins.nonebot_plugin_fanqie_verify import config as plugin_config
from src.plugins.nonebot_plugin_fanqie_verify.config import Config


def test_plugin_config_defaults() -> None:
    """Config 模型应带有合理默认值（不含环境变量覆盖）。"""
    cfg = Config()
    assert cfg.fanqie_response_timeout == 600
    assert cfg.fanqie_max_attempts == 3
    assert cfg.fanqie_notify_channel == "group"
    assert cfg.fanqie_admin_ids == set()
    assert cfg.fanqie_book_name_max_len == 100
    assert cfg.fanqie_remind_before_kick == (3600, 300)
    assert cfg.fanqie_allow_group_admin_commands is True


def test_plugin_config_from_global() -> None:
    """插件模块级 config 应能正常解析且与默认值一致。"""
    assert plugin_config.fanqie_response_timeout == 600
    assert plugin_config.fanqie_welcome_message


@pytest.mark.parametrize(
    "key,expected",
    [
        ("fanqie_verify_groups", set()),
        ("fanqie_ocr_api_url", ""),
        ("fanqie_ocr_timeout", 15.0),
    ],
)
def test_plugin_config_field_values(key: str, expected: object) -> None:
    """插件的关键配置字段应保持预期默认值。"""
    cfg = get_plugin_config(Config)
    assert getattr(cfg, key) == expected
