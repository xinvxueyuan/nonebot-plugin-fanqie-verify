"""插件配置单例。

在模块导入时由 NoneBot 解析 ``Config``，供各模块直接引用。
"""

from __future__ import annotations

from nonebot import get_plugin_config

from ..config import Config

plugin_config: Config = get_plugin_config(Config)

__all__ = ["plugin_config"]
