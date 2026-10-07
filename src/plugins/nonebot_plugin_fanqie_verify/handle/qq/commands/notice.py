"""群公告相关命令的响应器定义。

用户 2026-10-07 要求「额外需要实现『获取群公告列表』handle」—— 因为**公告 id
在 QQ 客户端是隐藏的**，不把 id 打出来就没法绑定。

命令（均限**超管**，与用户拍板一致）：

* ``获取群公告列表`` → 出图列出本群公告的 ``notice_id``（标注哪条能当验证公告）
* ``查看验证公告``   → 列出本群已绑定的验证公告
* ``设为验证公告 <id...>`` → 绑定（**逐个校验是否带确认环节**，不合格的跳过并汇报）
* ``取消验证公告 <id...|全部>`` → 解绑

绑定落在独立的 ``fanqie_verification_notices.toml``，**不写进策略文件**（理由见
``services/verification/notices.py`` 模块 docstring）。
"""

from nonebot import on_command
from nonebot.permission import SUPERUSER

# 获取群公告列表：把隐藏的公告 id 打出来（图片渲染）。
notice_list_cmd = on_command(
    "获取群公告列表",
    aliases={"群公告列表", "公告列表", "查看群公告", "/公告列表"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 查看验证公告：本群已绑定的验证公告。
notice_show_cmd = on_command(
    "查看验证公告",
    aliases={"验证公告", "公告绑定", "/验证公告"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 设为验证公告：绑定公告 id（可多个）。
notice_bind_cmd = on_command(
    "设为验证公告",
    aliases={"绑定公告", "添加验证公告", "/设为验证公告"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 取消验证公告：解绑。
notice_unbind_cmd = on_command(
    "取消验证公告",
    aliases={"解绑公告", "移除验证公告", "/取消验证公告"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


__all__ = [
    "notice_bind_cmd",
    "notice_list_cmd",
    "notice_show_cmd",
    "notice_unbind_cmd",
]
