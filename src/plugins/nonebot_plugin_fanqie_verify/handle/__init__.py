"""事件响应器注册包。

导入本包即注册全部群管理处理器（新人入群、图片提交、管理员命令）。
"""

from __future__ import annotations

from . import qq as qq

__all__ = ["qq"]
