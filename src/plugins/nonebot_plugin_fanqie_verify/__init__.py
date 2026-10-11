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
        # ── 信息插件（命令「功能」）读的帮助元数据 ──
        "menu_type": "管理",
        "order": 30,
        # 命令表 = 本仓真实注册的命令（handle/qq/commands/*.py 的 on_command），
        # 全部为超管命令；入群验证流程本身是被动的（自动提示成员发截图）。
        "commands": [
            {"command": "验证 <群号>", "desc": "多群待验证时私聊选群"},
            {"command": "keep / 保留", "desc": "管理员放行成员（超管）"},
            {"command": "通过 / 批准", "desc": "插入直接批准（超管）"},
            {"command": "待处理列表 / 处理中列表", "desc": "待决策/待截图成员（超管）"},
            {"command": "查看白名单", "desc": "本群作者与作品（超管）"},
            {"command": "补验 / 延期 / 重审", "desc": "补扫漏验 / 延时 / 重审（超管）"},
            {"command": "重载番茄OCR配置", "desc": "热重载放行策略（超管）"},
            {"command": "设为验证公告", "desc": "公告绑定与查看（超管）"},
        ],
    },
)

__all__ = [
    "Config",
    "__plugin_meta__",
    "config",
    "handle",
    "hooks",
]
