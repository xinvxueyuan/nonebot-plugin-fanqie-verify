"""群公告命令处理器的行为测试（真 matcher、真 handler）。

覆盖四件事：

1. 「获取群公告列表」把**隐藏的公告 id** 真发出去，并区分「可作验证公告 / 未开确认」；
2. 「设为验证公告」**逐个校验确认环节**，不合格的**跳过并在回复里汇报**
   （用户 2026-10-07 明确要求），且**只有合格的真正写进绑定**；
3. 「取消验证公告」支持按 id 解绑与「全部」清空；
4. 公告接口不可用时**报错**而不是静默（否则管理员会以为绑定成功了）。

发送侧统一用替身捕获 ``send_group_msg``（把发送内容抓下来断言），事件用真
``GroupMessageEvent``；平台接口用 ``ctx.should_call_api`` 声明（nonebug 严格模式）。
"""

from __future__ import annotations

import time
from typing import Any

from nonebug import App
import pytest

from src.plugins.nonebot_plugin_fanqie_verify.handle.qq.commands import (
    notice as notice_cmd,
)

_SELF_ID = 3128682634
_GROUP_ID = 1094538078
_SUPERUSER = 1330509996


def _event(message: str, *, user_id: int = _SUPERUSER) -> Any:
    """构造一条群消息事件（照 tests/test_sticker_detection.py 的写法）。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

    return GroupMessageEvent(
        time=int(time.time()),
        self_id=_SELF_ID,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1001,
        group_id=_GROUP_ID,
        user_id=user_id,
        anonymous=None,
        sender={"user_id": user_id, "nickname": "群主", "role": "owner"},
        raw_message=message,
        message=Message(message),
        font=0,
    )  # type: ignore[call-arg]


def _raw_notice(
    notice_id: str,
    *,
    confirm: bool = True,
    send_new_member: bool = True,
    text: str = "公告正文",
) -> dict[str, Any]:
    """构造一条接口原始公告（默认是「可用公告」：两者都勾）。"""
    return {
        "notice_id": notice_id,
        "message": {"text": text},
        "settings": {
            "confirm_required": confirm,
            "send_new_member": send_new_member,
            "pinned": False,
            "is_show_edit_card": False,
        },
        "publish_time": 1_700_000_000,
        "sender_id": 2846018938,
    }


@pytest.fixture(autouse=True)
def _text_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让渲染失败 → 走纯文本回退分支（图片分支由 test_notice_render 覆盖）。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notice_render,
    )

    async def _none(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(notice_render, "render_card", _none)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """捕获 ``send_group_msg`` 的参数，供断言发送内容。"""
    from nonebot.adapters.onebot.v11 import Bot

    captured: list[dict[str, Any]] = []

    async def fake_send(
        _self: Any, *, group_id: int, message: Any, **_kwargs: Any
    ) -> dict[str, str]:
        captured.append({"group_id": group_id, "message": message})
        return {"message_id": "1"}

    # onebot v11 的 Bot 用 __getattr__ 动态生成 API 方法（类上没有真实属性），
    # 所以必须 raising=False 才能挂上替身。
    monkeypatch.setattr(Bot, "send_group_msg", fake_send, raising=False)
    return captured


def _last_text(sent: list[dict[str, Any]]) -> str:
    """最后一条发送内容转成纯文本。"""
    assert sent, "没有发出任何消息"
    return str(sent[-1]["message"])


def test_no_reply_segment_on_notice_cards() -> None:
    """所有公告命令的发送路径都**不得**带 ``MessageSegment.reply``。

    用户 2026-10-07 实机反馈：带引用段的消息会被 QQ **强制折叠**成回复下方的小缩略图
    （不是正常的「图文」消息），成员得再点一次才能看清 —— 出图的意义就没了。
    定位成员用 ``@`` 即可，不需要引用。

    这是 AST 静态守卫（不是运行期用例）：``_send_notice_card`` 是**唯一**发图路径，
    任何人在它里面补回 ``reply``/``anchor`` 都会让这条红掉。
    """
    import ast
    import pathlib as _pathlib

    src_path = _pathlib.Path(
        "src/plugins/nonebot_plugin_fanqie_verify/handle/qq/adapters"
        "/onebot11/default/verification.py"
    )
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    senders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"_send_notice_card", "_send_member_reply"}
    ]
    assert len(senders) == 2, "扫描失效：没找到两个发送函数"

    for node in senders:
        body = ast.get_source_segment(source, node) or ""
        # _send_member_reply 的纯文本分支仍可引用（无图、不会被折叠）；
        # 但它转给 _send_notice_card 的卡片分支不能。这里只锁发图函数。
        if node.name == "_send_notice_card":
            assert ".reply(" not in body, f"{node.name} 又加回了引用段"
            assert "anchor" not in body, f"{node.name} 残留 anchor"
            assert "MessageSegment.image(png)" in body, "发图逻辑不应被改动"


# ---------------------------------------------------------------- 获取群公告列表


@pytest.mark.asyncio
async def test_notice_list_shows_index_not_long_id(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """列表每项的**主文本必须是 #序号**，不是那串长公告 id。

    用户 2026-10-07 实机反馈：「展示的每项大标题一样很长啊，我要的是 #1、#2 这种
    数据库映射编号」—— 公告 id 又长又手抄不动，序号才是给人的。
    """
    from nonebot.adapters.onebot.v11 import Bot

    async with app.test_matcher(notice_cmd.notice_list_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[
                _raw_notice("N-A", confirm=True, text="带确认的公告"),
                _raw_notice("N-B", confirm=False, text="没开确认的公告"),
            ],
        )
        ctx.receive_event(bot, _event("获取群公告列表"))

    text = _last_text(sent)
    assert "#1" in text and "#2" in text  # 主文本 = 序号
    assert "N-A" not in text and "N-B" not in text  # 长 id 不再当标题
    assert "可作验证公告" in text
    assert "未开确认" in text
    assert "带确认的公告" in text
    assert "#数字" in text  # 说明里点出 # 前缀的含义


@pytest.mark.asyncio
async def test_notice_list_marks_already_bound(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """已绑定的公告要在列表里标出来（避免重复绑）。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    await notices.bind_notices(_GROUP_ID, ["N-A"])

    async with app.test_matcher(notice_cmd.notice_list_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[_raw_notice("N-A"), _raw_notice("N-B")],
        )
        ctx.receive_event(bot, _event("获取群公告列表"))

    assert "已绑定" in _last_text(sent)


@pytest.mark.asyncio
async def test_notice_list_reports_api_failure(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """接口不可用时要明确报错（不能装成「本群没有公告」）。"""
    from nonebot.adapters.onebot.v11 import Bot

    async with app.test_matcher(notice_cmd.notice_list_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            exception=RuntimeError("LLBot 未登录"),
        )
        ctx.receive_event(bot, _event("获取群公告列表"))

    text = _last_text(sent)
    assert "失败" in text
    assert "v8.3.0" in text


# ---------------------------------------------------------------- 设为验证公告


@pytest.mark.asyncio
async def test_notice_bind_rejects_multiple_tokens(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """一次给多个公告 → **明确拒绝**且不改动绑定（QQ 只允许 1 条）。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    async with app.test_matcher(notice_cmd.notice_bind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[_raw_notice("OK1", confirm=True), _raw_notice("OK2", confirm=True)],
        )
        ctx.receive_event(bot, _event("设为验证公告 OK1 OK2"))

    # 静默丢掉多余的会让人以为绑上了 —— 所以这里是「两条都没绑」
    assert (await notices.load_bindings()).get(_GROUP_ID) is None

    text = _last_text(sent)
    assert "只能绑定 1 条" in text
    assert "QQ 只允许" in text


@pytest.mark.asyncio
async def test_notice_bind_by_index(app: App, sent: list[dict[str, Any]]) -> None:
    """用**序号**绑定（公告 id 太长，用户 2026-10-07 要求用序号代替）。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    async with app.test_matcher(notice_cmd.notice_bind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[
                _raw_notice("AAA", confirm=True),  # 序号 1
                _raw_notice("BBB", confirm=False),  # 序号 2（未开确认 → 跳过）
                _raw_notice("CCC", confirm=True),  # 序号 3
            ],
        )
        ctx.receive_event(bot, _event("设为验证公告 3"))

    # 序号被翻译成真实 id 后再校验与写入（存的是 id，不是序号）
    assert (await notices.load_bindings())[_GROUP_ID] == ("CCC",)
    text = _last_text(sent)
    assert "#3" in text


@pytest.mark.asyncio
async def test_notice_bind_replaces_previous(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """已绑定时再设另一条 → **直接替换**，并写明被顶掉的是哪条（不必先取消）。

    用户 2026-10-08 拍板：QQ 只允许 1 条可确认公告，「追加」既无意义又会让第 2 条
    永远读不齐、把所有人拦在门外。
    """
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    await notices.bind_notices(_GROUP_ID, ["AAA"])

    async with app.test_matcher(notice_cmd.notice_bind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[
                _raw_notice("AAA", confirm=True),  # 序号 1（原绑定）
                _raw_notice("CCC", confirm=True),  # 序号 2
            ],
        )
        ctx.receive_event(bot, _event("设为验证公告 2"))

    assert (await notices.load_bindings())[_GROUP_ID] == ("CCC",)
    text = _last_text(sent)
    assert "已替换掉原绑定：#1" in text  # 序号按列表算，不是绑进文件里的位置
    assert "已设为验证公告：#2" in text


@pytest.mark.asyncio
async def test_notice_bind_by_index_skips_unconfirmed(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """按序号绑定时同样要校验确认环节（序号 2 未开确认 → 跳过并汇报）。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    async with app.test_matcher(notice_cmd.notice_bind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[
                _raw_notice("AAA", confirm=True),
                _raw_notice("BBB", confirm=False),
            ],
        )
        ctx.receive_event(bot, _event("设为验证公告 2"))

    assert _GROUP_ID not in await notices.load_bindings()
    text = _last_text(sent)
    assert "#2" in text  # 序号 2（BBB）未开确认被跳过
    assert "确认" in text  # 未开确认的原因


@pytest.mark.asyncio
async def test_notice_bind_reports_existing_as_skipped(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """重复绑定要如实报「已在绑定中」，且不改变绑定集合。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    await notices.bind_notices(_GROUP_ID, ["OK1"])

    async with app.test_matcher(notice_cmd.notice_bind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[_raw_notice("OK1", confirm=True)],
        )
        ctx.receive_event(bot, _event("设为验证公告 OK1"))

    assert (await notices.load_bindings())[_GROUP_ID] == ("OK1",)
    assert "本来就是验证公告" in _last_text(sent)


@pytest.mark.asyncio
async def test_notice_bind_without_args_shows_usage(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """不带 id 时给用法，且不调用公告接口（严格模式下漏调不会被漏声明）。"""
    from nonebot.adapters.onebot.v11 import Bot

    async with app.test_matcher(notice_cmd.notice_bind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.receive_event(bot, _event("设为验证公告"))

    assert "用法" in _last_text(sent)


@pytest.mark.asyncio
async def test_notice_bind_refuses_when_list_unavailable(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """读不到公告列表就**拒绝绑定**（不校验确认环节等于埋雷）。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    async with app.test_matcher(notice_cmd.notice_bind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            exception=RuntimeError("接口不可用"),
        )
        ctx.receive_event(bot, _event("设为验证公告 OK1"))

    assert await notices.load_bindings() == {}
    assert "失败" in _last_text(sent)


# ---------------------------------------------------------------- 查看 / 取消


@pytest.mark.asyncio
async def test_notice_show_lists_bound(app: App, sent: list[dict[str, Any]]) -> None:
    """查看验证公告：列出已绑定 id 与公告正文预览。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    await notices.bind_notices(_GROUP_ID, ["OK1"])

    async with app.test_matcher(notice_cmd.notice_show_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[_raw_notice("OK1", text="群规：先看公告再发言")],
        )
        ctx.receive_event(bot, _event("查看验证公告"))

    text = _last_text(sent)
    assert "#1" in text
    assert "群规：先看公告再发言" in text


@pytest.mark.asyncio
async def test_notice_show_flags_deleted_notice(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """绑定的公告被删掉时要标出来（否则闸门会一直拦人却看不出原因）。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    await notices.bind_notices(_GROUP_ID, ["GONE"])

    async with app.test_matcher(notice_cmd.notice_show_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.should_call_api(
            "_get_group_notice",
            {"group_id": _GROUP_ID},
            result=[_raw_notice("OTHER")],
        )
        ctx.receive_event(bot, _event("查看验证公告"))

    assert "不存在" in _last_text(sent)


@pytest.mark.asyncio
async def test_notice_show_without_binding(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """没绑定时要说明「当前不检查公告阅读」。"""
    from nonebot.adapters.onebot.v11 import Bot

    async with app.test_matcher(notice_cmd.notice_show_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.receive_event(bot, _event("查看验证公告"))

    assert "未设置验证公告" in _last_text(sent)


@pytest.mark.asyncio
async def test_notice_unbind_by_id(app: App, sent: list[dict[str, Any]]) -> None:
    """按 id 解绑：只删指定的，其余保留。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    await notices.bind_notices(_GROUP_ID, ["B"])
    await notices.bind_notices(868258211, ["X"])

    async with app.test_matcher(notice_cmd.notice_unbind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.receive_event(bot, _event("取消验证公告 B"))

    bindings = await notices.load_bindings()
    assert _GROUP_ID not in bindings  # 本条已解绑
    assert bindings[868258211] == ("X",)  # 其它群不受影响
    assert "B" in _last_text(sent)


@pytest.mark.asyncio
async def test_notice_unbind_all(app: App, sent: list[dict[str, Any]]) -> None:
    """「全部」清空本群绑定，且不影响其他群。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    await notices.bind_notices(_GROUP_ID, ["A"])
    await notices.bind_notices(868258211, ["X"])

    async with app.test_matcher(notice_cmd.notice_unbind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.receive_event(bot, _event("取消验证公告 全部"))

    bindings = await notices.load_bindings()
    assert _GROUP_ID not in bindings
    assert bindings[868258211] == ("X",)
    assert "清空" in _last_text(sent)


@pytest.mark.asyncio
async def test_notice_unbind_missing_id_is_reported(
    app: App, sent: list[dict[str, Any]]
) -> None:
    """解绑一个没绑过的 id：如实说「没有取消任何绑定」，不误报成功。"""
    from nonebot.adapters.onebot.v11 import Bot

    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        notices,
    )

    await notices.bind_notices(_GROUP_ID, ["A"])

    async with app.test_matcher(notice_cmd.notice_unbind_cmd) as ctx:
        bot = ctx.create_bot(base=Bot)
        ctx.receive_event(bot, _event("取消验证公告 ZZ"))

    assert (await notices.load_bindings())[_GROUP_ID] == ("A",)
    text = _last_text(sent)
    assert "ZZ" in text
    assert "没有取消任何绑定" in text
