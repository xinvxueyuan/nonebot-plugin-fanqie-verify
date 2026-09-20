"""运行时钩子注册包。

导入本包即注册钩子适配层与各处理器模块。
"""

from __future__ import annotations

from . import (
    adapters as adapters,
    handlers as handlers,
    interfaces as interfaces,
)

__all__ = ["adapters", "handlers", "interfaces"]
