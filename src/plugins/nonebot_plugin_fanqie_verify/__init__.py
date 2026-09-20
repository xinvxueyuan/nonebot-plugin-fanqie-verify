"""番茄读书群入群验证插件主模块。

此模块是 ocr-fanqie-novel 插件的入口点，负责：
- 定义和导出 NoneBot 插件元数据
- 加载插件配置
- 挂载运行时钩子（生命周期、机器人连接、消息存储、API 审计）
- 注册事件响应器（新人入群、图片提交、管理员命令）

"""

from nonebot import require

require("nonebot_plugin_localstore")
require("nonebot_plugin_orm")

from nonebot.plugin import PluginMetadata

from . import handle as handle, hooks as hooks
from .config import Config
from .core.config import plugin_config as config

__plugin_meta__ = PluginMetadata(
    name="番茄读书入群验证",
    description=(
        "入群后要求新成员发送自己发布的番茄小说书评详情截图，"
        "OCR 识别后自动放行或通知管理员决策"
    ),
    usage=(
        "新成员入群后会自动收到验证提示，发送自己发布的番茄小说书评详情截图即可。\n"
        "OCR 识别书评读者、书名与作者，综合判断后放行；失败则私信管理员决定通过或踢出。"
    ),
    type="application",
    homepage="",
    config=Config,
    supported_adapters={"nonebot.adapters.onebot.v11"},
    extra={
        "priority": 50,
        "startup": True,
        "shutdown": True,
    },
)

__all__ = [
    "Config",
    "__plugin_meta__",
    "config",
    "handle",
    "hooks",
]
