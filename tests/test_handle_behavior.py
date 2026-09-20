"""事件响应器行为测试（基于 nonebug 官方行为测试实践）。

通过 ``app.test_matcher`` 驱动真实的事件响应器与处理器，并用
``ctx.should_call_api`` 断言预期的平台接口调用，覆盖 FR1（入群引导）、
退群/禁言/管理员变动边界。图片提交与管理员命令的 in-handler 逻辑通过
直接调用处理器函数验证（nonebug 对未声明的 API 调用采用严格模式）。

"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import time
from typing import Any

from nonebug import App
import pytest

from src.plugins.nonebot_plugin_ocr_fanqie_novel.handle.qq.commands import (
    verification as cmd_module,
)

_SELF_ID = 12345
_GROUP_ID = 123
_USER_ID = 10001
_ADMIN_ID = 90001
_MEMBER_INFO = {
    "user_id": _USER_ID,
    "role": "member",
    "card": "",
    "nickname": "某用户",
    "shut_up_timestamp": 0,
}


@pytest.fixture(autouse=True)
def _cleanup_session_store() -> Generator[None]:
    """每个行为测试后重置会话存储与策略缓存，避免状态泄漏。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
        session as session_module,
    )

    before = session_module._store
    policy_before = policy_module._policy_cache
    session_module._store = None
    try:
        yield
    finally:
        session_module._store = before
        policy_module._policy_cache = policy_before


@pytest.fixture(autouse=True)
def _default_monitored_group() -> Generator[None]:
    """默认让群 _GROUP_ID 处于监控范围（配置作者节点）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    before = policy_module._policy_cache
    policy_module._policy_cache = VerificationPolicy(
        require_all=False,
        required_elements=frozenset({"book_name", "author"}),
        groups={
            _GROUP_ID: GroupPolicy(
                group_id=_GROUP_ID,
                authors=(AuthorEntry(name="阿百川大鬼"),),
            ),
        },
    )
    try:
        yield
    finally:
        policy_module._policy_cache = before


@pytest.fixture(autouse=True)
async def _drain_timeout_tasks() -> AsyncGenerator[None]:
    """排空会话存储的超时任务，避免已取消任务在 teardown 时产生警告。"""
    import asyncio

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    try:
        yield
    finally:
        store = get_session_store()
        tasks = list(store._timeout_tasks.values())
        store._timeout_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture(autouse=True)
def _quiet_background_tasks() -> Generator[None]:
    """行为测试不持久化消息存储，避免后台 DB 任务未等待。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    enabled_before = plugin_config.fanqie_message_store_enabled
    plugin_config.fanqie_message_store_enabled = False
    try:
        yield
    finally:
        plugin_config.fanqie_message_store_enabled = enabled_before


def _ban_event(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "time": int(time.time()),
        "self_id": _SELF_ID,
        "post_type": "notice",
        "notice_type": "group_ban",
        "sub_type": "ban",
        "group_id": _GROUP_ID,
        "operator_id": _ADMIN_ID,
        "user_id": _USER_ID,
        "duration": 600,
    }
    data.update(overrides)
    return data


def _start_session() -> None:
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    get_session_store().start(
        group_id=str(_GROUP_ID),
        user_id=str(_USER_ID),
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )


@pytest.mark.asyncio
async def test_group_increase_sends_guide(app: App) -> None:
    """FR1：入群事件应创建会话并查询成员信息。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupIncreaseNoticeEvent,
        Message,
        MessageSegment,
    )

    async with app.test_matcher(cmd_module.group_increase) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupIncreaseNoticeEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="notice",
            notice_type="group_increase",
            sub_type="approve",
            group_id=_GROUP_ID,
            operator_id=1,
            user_id=_USER_ID,
        )
        from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import (
            plugin_config,
        )

        welcome = (
            f" {plugin_config.fanqie_welcome_message} "
            "请在 10 分钟内完成截图并发送，超时后将由管理员人工处理。"
            "可在群成员列表中点击我发起临时会话直接发送截图。\n"
            "【如何查找书评】进入番茄小说 App -> 右下角「我的」页面按压进入 -> "
            "点我的头像栏目进入用户信息页面 -> 在顶级菜单栏找到「讨论」并进入 -> "
            "在子菜单栏找到「书评」 -> 滚动屏幕找到本书的书评并点击进入「书评详情页」 -> "
            "直接截取手机整个屏幕（不要裁剪，含顶部「书评详情」标题）发送到本 QQ 群。"
        )
        expected_message = Message(MessageSegment.at(_USER_ID)) + welcome
        ctx.should_call_api(
            "get_group_member_info",
            {"group_id": _GROUP_ID, "user_id": _USER_ID, "no_cache": True},
            result=_MEMBER_INFO,
        )
        ctx.should_call_api(
            "send_group_msg",
            {"group_id": _GROUP_ID, "message": expected_message},
        )
        ctx.receive_event(bot, event)

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    record = get_session_store().get(str(_GROUP_ID), str(_USER_ID))
    assert record is not None
    assert record.status == "waiting"


@pytest.mark.asyncio
async def test_group_increase_not_monitored_skips(app: App) -> None:
    """群不在监控白名单时应跳过验证，不发引导。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, GroupIncreaseNoticeEvent

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    policy_module._policy_cache = VerificationPolicy(
        require_all=False,
        required_elements=frozenset({"book_name", "author"}),
        groups={
            456: GroupPolicy(
                group_id=456,
                authors=(AuthorEntry(name="阿百川大鬼"),),
            ),
        },
    )

    async with app.test_matcher(cmd_module.group_increase) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupIncreaseNoticeEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="notice",
            notice_type="group_increase",
            sub_type="approve",
            group_id=_GROUP_ID,
            operator_id=1,
            user_id=_USER_ID,
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_group_ban_syncs_muted_state(app: App) -> None:
    """群禁言事件应同步会话禁言状态。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, GroupBanNoticeEvent

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    _start_session()

    async with app.test_matcher(cmd_module.group_ban) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupBanNoticeEvent(**_ban_event())
        ctx.receive_event(bot, event)

    record = get_session_store().get(str(_GROUP_ID), str(_USER_ID))
    assert record is not None
    assert record.is_muted is True


@pytest.mark.asyncio
async def test_group_ban_lift_updates_state(app: App) -> None:
    """解除禁言事件应更新会话状态。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, GroupBanNoticeEvent

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    _start_session()
    store = get_session_store()
    store.set_muted(str(_GROUP_ID), str(_USER_ID), is_muted=True)

    async with app.test_matcher(cmd_module.group_ban) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupBanNoticeEvent(**_ban_event(sub_type="lift_ban", duration=0))
        ctx.receive_event(bot, event)

    record = store.get(str(_GROUP_ID), str(_USER_ID))
    assert record is not None
    assert record.is_muted is False


@pytest.mark.asyncio
async def test_group_admin_change_approves_waiting(app: App) -> None:
    """待验证成员被设为管理员应直接放行。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, GroupAdminNoticeEvent

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    _start_session()

    async with app.test_matcher(cmd_module.group_admin_change) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupAdminNoticeEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="notice",
            notice_type="group_admin",
            sub_type="set",
            group_id=_GROUP_ID,
            user_id=_USER_ID,
        )
        ctx.receive_event(bot, event)

    record = get_session_store().get(str(_GROUP_ID), str(_USER_ID))
    assert record is not None
    assert record.status == "approved"


@pytest.mark.asyncio
async def test_group_decrease_clears_session(app: App) -> None:
    """成员退群应清除验证会话。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, GroupDecreaseNoticeEvent

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    _start_session()

    async with app.test_matcher(cmd_module.group_decrease) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupDecreaseNoticeEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="notice",
            notice_type="group_decrease",
            sub_type="leave",
            group_id=_GROUP_ID,
            operator_id=_USER_ID,
            user_id=_USER_ID,
        )
        ctx.receive_event(bot, event)

    assert get_session_store().get(str(_GROUP_ID), str(_USER_ID)) is None


@pytest.mark.asyncio
async def test_group_decrease_kick_me_clears_group(app: App) -> None:
    """机器人被移出群应清理该群全部会话。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, GroupDecreaseNoticeEvent

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    _start_session()
    store = get_session_store()
    store.start(
        group_id=str(_GROUP_ID),
        user_id="10002",
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

    async with app.test_matcher(cmd_module.group_decrease) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupDecreaseNoticeEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="notice",
            notice_type="group_decrease",
            sub_type="kick_me",
            group_id=_GROUP_ID,
            operator_id=_ADMIN_ID,
            user_id=_SELF_ID,
        )
        ctx.receive_event(bot, event)

    assert store.get(str(_GROUP_ID), str(_USER_ID)) is None
    assert store.get(str(_GROUP_ID), "10002") is None


@pytest.mark.asyncio
async def test_image_submission_ignores_plain_message(app: App) -> None:
    """无图片消息应被忽略，不触发 OCR。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    async with app.test_matcher(cmd_module.image_submission) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=_USER_ID,
            anonymous=None,
            sender={"user_id": _USER_ID, "nickname": "t", "role": "member"},
            raw_message="hello",
            message=Message([MessageSegment.text("hello")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_image_submission_handles_pending_member_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待验证成员发送图片：in-handler 检查应通过并走完验证流程。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.handle.qq.adapters.onebot11.default import (
        verification as handle_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        actions,
        flow as flow_module,
        get_session_store,
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    policy_module._policy_cache = VerificationPolicy(
        require_all=False,
        required_elements=frozenset({"book_name", "author"}),
        groups={
            _GROUP_ID: GroupPolicy(
                group_id=_GROUP_ID,
                authors=(AuthorEntry(name="阿百川大鬼"),),
            ),
        },
    )

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
            OCRPage,
            OCRResult,
            OCRTextLine,
        )

        def _box(y: int) -> list[list[int]]:
            return [[0, y], [100, y], [100, y + 20], [0, y + 20]]

        ocr = OCRResult(
            job_id="job-pass",
            pages=[
                OCRPage(
                    lines=[
                        OCRTextLine(text="书评详情", confidence=0.99, box=_box(0)),
                        OCRTextLine(text="新v学员", confidence=0.98, box=_box(30)),
                        OCRTextLine(text="我", confidence=0.97, box=_box(40)),
                        OCRTextLine(text="刚刚", confidence=0.96, box=_box(60)),
                        OCRTextLine(
                            text="阅读2小时后点评", confidence=0.95, box=_box(80)
                        ),
                        OCRTextLine(
                            text="综漫：吉他雇佣兵无法找到归宿？",
                            confidence=0.98,
                            box=_box(200),
                        ),
                        OCRTextLine(text="阿百川大鬼", confidence=0.97, box=_box(220)),
                    ]
                )
            ],
        )
        return dict.fromkeys(models or ["PaddleOCR-VL-1.6"], ocr)

    async def fake_get_member_info(
        bot: Any,
        group_id: int,
        user_id: int,
    ) -> Any:
        _ = (bot, group_id, user_id)
        return actions.MemberInfo(
            user_id=_USER_ID,
            role="member",
            card="",
            nickname="某用户",
            shut_up_timestamp=0,
        )

    class FakeBot:
        self_id = str(_SELF_ID)

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def send_group_msg(self, **kwargs: Any) -> None:
            self.calls.append(("send_group_msg", kwargs))

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)
    monkeypatch.setattr(actions, "get_member_info", fake_get_member_info)

    _start_session()
    bot: Any = FakeBot()
    await handle_module.on_image_submission(bot, _image_message_event())

    record = get_session_store().get(str(_GROUP_ID), str(_USER_ID))
    assert record is not None
    assert record.status == "approved"
    assert any(c[0] == "send_group_msg" for c in bot.calls)


def _image_message_event() -> Any:
    """构造带图片的群消息事件。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

    return GroupMessageEvent(
        time=int(time.time()),
        self_id=_SELF_ID,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        group_id=_GROUP_ID,
        user_id=_USER_ID,
        anonymous=None,
        sender={"user_id": _USER_ID, "nickname": "t", "role": "member"},
        raw_message="[CQ:image,file=x.jpg]",
        message=Message([
            MessageSegment(
                type="image", data={"file": "x.jpg", "url": "https://e.com/x.jpg"}
            )
        ]),
        font=0,
    )  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_kick_cmd_superuser_runs(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超级用户执行 /kick 应能走通命令流程（不触发依赖注入错误）。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        actions,
        get_session_store,
    )

    async def fake_get_member_info(
        bot: Any,
        group_id: int,
        user_id: int,
    ) -> Any:
        _ = (bot, group_id, user_id)
        return actions.MemberInfo(
            user_id=user_id,
            role="member",
            card="",
            nickname="某用户",
            shut_up_timestamp=0,
        )

    monkeypatch.setattr(actions, "get_member_info", fake_get_member_info)

    store = get_session_store()
    store.start(
        group_id=str(_GROUP_ID),
        user_id=str(_USER_ID),
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

    async with app.test_matcher(cmd_module.kick_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=1330509996,
            anonymous=None,
            sender={"user_id": 1330509996, "nickname": "owner", "role": "owner"},
            raw_message="/kick 10001",
            message=Message([MessageSegment.text("/kick 10001")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "set_group_kick",
            {"group_id": _GROUP_ID, "user_id": _USER_ID},
        )
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": MessageSegment.reply(1) + "已将该成员移出群聊。",
            },
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_pending_list_cmd_lists_awaiting_admin(app: App) -> None:
    """待处理列表命令应列出本群等待管理员决策的成员。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    store = get_session_store()
    store.start(
        group_id=str(_GROUP_ID),
        user_id="10001",
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    store.await_admin(str(_GROUP_ID), "10001")
    store.start(
        group_id=str(_GROUP_ID),
        user_id="20001",
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    store.await_admin(str(_GROUP_ID), "20001")

    async with app.test_matcher(cmd_module.pending_list_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=1330509996,
            anonymous=None,
            sender={"user_id": 1330509996, "nickname": "owner", "role": "owner"},
            raw_message="/待处理列表",
            message=Message([MessageSegment.text("/待处理列表")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1) + "等待管理员决策的成员 2 人：\n"
                    "QQ 10001（剩余 16 小时 0 分，/keep 或 /kick）\n"
                    "QQ 20001（剩余 16 小时 0 分，/keep 或 /kick）"
                ),
            },
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_pending_list_cmd_empty(app: App) -> None:
    """无待处理成员时返回空提示。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    async with app.test_matcher(cmd_module.pending_list_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=1330509996,
            anonymous=None,
            sender={"user_id": 1330509996, "nickname": "owner", "role": "owner"},
            raw_message="/待处理列表",
            message=Message([MessageSegment.text("/待处理列表")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1) + "当前没有等待管理员处理的验证成员。"
                ),
            },
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_processing_list_cmd_lists_waiting(app: App) -> None:
    """处理中列表命令应只列本群 waiting（等待截图）的成员。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    store = get_session_store()
    store.start(
        group_id=str(_GROUP_ID),
        user_id="10001",
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    # 20001 转 awaiting_admin，不应出现在处理中列表
    store.start(
        group_id=str(_GROUP_ID),
        user_id="20001",
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    store.await_admin(str(_GROUP_ID), "20001")

    async with app.test_matcher(cmd_module.processing_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=1330509996,
            anonymous=None,
            sender={"user_id": 1330509996, "nickname": "owner", "role": "owner"},
            raw_message="/处理中列表",
            message=Message([MessageSegment.text("/处理中列表")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1)
                    + "等待提交截图的成员 1 人：\nQQ 10001（剩余 10 分，/keep 或 /kick）"
                ),
            },
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_approve_cmd_superuser_direct_approve(app: App) -> None:
    """管理员 /通过 能直接批准验证中的新成员。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    get_session_store().start(
        group_id=str(_GROUP_ID),
        user_id=str(_USER_ID),
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

    async with app.test_matcher(cmd_module.approve_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=1,
            group_id=_GROUP_ID,
            user_id=1330509996,
            anonymous=None,
            sender={"user_id": 1330509996, "nickname": "owner", "role": "owner"},
            raw_message="/通过 10001",
            message=Message([MessageSegment.text("/通过 10001")]),
            font=0,
        )  # type: ignore[call-arg]
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": Message([
                    MessageSegment.reply(1),
                    MessageSegment.at(_USER_ID),
                    MessageSegment.text(" 验证通过，欢迎加入本群！"),
                ]),
            },
        )
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (MessageSegment.reply(1) + "已直接批准该成员并通过验证。"),
            },
        )
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_private_submission_silent_without_waiting(app: App) -> None:
    """用户无待验证会话时，私聊发图应静默，不发送任何回复（不干扰其它功能）。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        Message as OneBot11Message,
        MessageSegment as OneBot11MessageSegment,
        PrivateMessageEvent,
    )

    async with app.test_matcher(cmd_module.private_image_submission) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = PrivateMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="private",
            sub_type="friend",
            message_id=7,
            user_id=_USER_ID,
            sender={"user_id": _USER_ID, "nickname": "某用户"},
            raw_message="[图片]",
            message=OneBot11Message([
                OneBot11MessageSegment.image("https://example.com/img.png")
            ]),
            font=0,
        )  # type: ignore[call-arg]
        # 无待验证会话：expect none——不应有任何 send_private_msg 调用
        ctx.receive_event(bot, event)


@pytest.mark.asyncio
async def test_whitelist_cmd_shows_authors(app: App) -> None:
    """管理员 查看白名单 应列出当前群配置的作者与作品。"""
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        GroupMessageEvent,
        Message,
        MessageSegment,
    )

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    async with app.test_matcher(cmd_module.whitelist_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        event = GroupMessageEvent(
            time=int(time.time()),
            self_id=_SELF_ID,
            post_type="message",
            message_type="group",
            sub_type="normal",
            message_id=9,
            group_id=_GROUP_ID,
            user_id=1330509996,
            anonymous=None,
            sender={"user_id": 1330509996, "nickname": "管理", "role": "owner"},
            raw_message="查看白名单",
            message=Message([MessageSegment.text("查看白名单")]),
            font=0,
        )  # type: ignore[call-arg]
        monkeypatch = _make_monkeypatch()
        monkeypatch.setattr(
            policy_module,
            "_policy_cache",
            VerificationPolicy(
                require_all=False,
                required_elements=frozenset({"book_name", "author"}),
                groups={
                    _GROUP_ID: GroupPolicy(
                        group_id=_GROUP_ID,
                        authors=(
                            AuthorEntry(
                                name="百舸川掮客",
                                books=frozenset({"乐队少女神人多，急需棍棒教育"}),
                            ),
                        ),
                    ),
                },
            ),
        )
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(9)
                    + "群 123 验证白名单 1 位作者：\n作者：百舸川掮客\n  作品：乐队少女神人多，急需棍棒教育"
                ),
            },
        )
        ctx.receive_event(bot, event)


def _make_monkeypatch() -> Any:
    """构造一个测试用 MonkeyPatch（避免逃逸到测试函数签名）。"""
    from pytest import MonkeyPatch

    return MonkeyPatch()


def test_is_sticker_and_contains_image_exclude_emoji() -> None:
    """表情包（带 emoji 字段的 image 段）不应被当作验证截图。"""
    from nonebot.adapters.onebot.v11.message import Message, MessageSegment

    sticker = MessageSegment(
        type="image",
        data={"file": "x", "emojiId": "abc", "emojiPackageId": "1"},
    )
    assert cmd_module._is_sticker(sticker) is True
    assert cmd_module._is_sticker(MessageSegment.image("http://x/a.png")) is False
    # 语义说明：_is_sticker 判断「是否是表情而非截图」，QQ 系统表情
    # （face 段）本身也不含截图，故同样返回 True。
    assert cmd_module._is_sticker(MessageSegment(type="face", data={"id": "1"})) is True

    class _FakeEvent:
        def __init__(self, message: Message) -> None:
            self.message = message

    sticker_event: Any = _FakeEvent(Message([sticker]))
    assert cmd_module._contains_image(sticker_event) is False
    image_event: Any = _FakeEvent(Message([MessageSegment.image("http://x/a.png")]))
    assert cmd_module._contains_image(image_event) is True


def _backfill_event(message_id: int = 1, text: str = "/补验") -> Any:
    """构造一条补验命令的群消息事件。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

    return GroupMessageEvent(
        time=int(time.time()),
        self_id=_SELF_ID,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=message_id,
        group_id=_GROUP_ID,
        user_id=1330509996,
        anonymous=None,
        sender={"user_id": 1330509996, "nickname": "管理", "role": "owner"},
        raw_message=text,
        message=Message([MessageSegment.text(text)]),
        font=0,
    )  # type: ignore[call-arg]


def _patch_backfill(
    monkeypatch: pytest.MonkeyPatch,
    *,
    confirm_first: bool = False,
    started: list[int] | None = None,
) -> list[int]:
    """替换补验的扫描与执行，返回记录已开启验证的成员列表。

    同时把重连模式置为 off：``on_bot_connect`` 的重连补验会抢先调用群 API，
    消费掉测试为 handler 声明的 API 期望队列。
    """
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        backfill as backfill_module,
    )

    recorded = started if started is not None else []

    async def fake_collect(
        bot: Any,
        *,
        group_id: int,
        hours: int,
        targets: list[int] | None = None,
    ) -> Any:
        _ = (bot, group_id, hours, targets)
        return [backfill_module.BackfillCandidate(20001, "新人", 1)]

    async def fake_run(
        bot: Any,
        *,
        group_id: int,
        candidates: Any,
        max_batch: int | None = None,
    ) -> int:
        _ = (bot, group_id, max_batch)
        recorded.extend(candidate.user_id for candidate in candidates)
        return len(candidates)

    monkeypatch.setattr(backfill_module, "collect_candidates", fake_collect)
    monkeypatch.setattr(backfill_module, "run_backfill", fake_run)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_enabled", True)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_reconnect_mode", "off")
    monkeypatch.setattr(plugin_config, "fanqie_backfill_confirm_first", confirm_first)
    return recorded


def _patch_empty_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    """替换补验扫描为「无候选」（用于确认命令的边界测试）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        backfill as backfill_module,
    )

    async def fake_collect(
        bot: Any,
        *,
        group_id: int,
        hours: int,
        targets: list[int] | None = None,
    ) -> Any:
        _ = (bot, group_id, hours, targets)
        return []

    monkeypatch.setattr(backfill_module, "collect_candidates", fake_collect)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_enabled", True)
    monkeypatch.setattr(plugin_config, "fanqie_backfill_reconnect_mode", "off")


@pytest.mark.asyncio
async def test_backfill_cmd_starts_verification(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补验命令（默认直接模式）应把候选成员纳入验证流程并回复数量。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, MessageSegment

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    started = _patch_backfill(monkeypatch)
    get_session_store().pop_pending_backfill(str(_GROUP_ID))

    async with app.test_matcher(cmd_module.backfill_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1) + "已为 1 名成员开启验证"
                    "（已发送引导并开始计时，未通过将转管理员处理）。"
                ),
            },
        )
        ctx.receive_event(bot, _backfill_event())

    assert started == [20001]


@pytest.mark.asyncio
async def test_backfill_cmd_confirm_first_lists_and_stores(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirm_first 模式只列候选名单并暂存，不开启验证。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, MessageSegment

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    started = _patch_backfill(monkeypatch, confirm_first=True)

    async with app.test_matcher(cmd_module.backfill_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1)
                    + "群 123 待补验成员 1 人：\nQQ 20001（新人）"
                    "\n\n回复「补验确认」执行补验。"
                ),
            },
        )
        ctx.receive_event(bot, _backfill_event())

    assert started == []  # 未开启验证
    assert get_session_store().pop_pending_backfill(str(_GROUP_ID)) == [20001]


@pytest.mark.asyncio
async def test_backfill_confirm_cmd_executes_pending(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补验确认命令应执行暂存的候选名单。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, MessageSegment

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    started = _patch_backfill(monkeypatch)
    get_session_store().set_pending_backfill(str(_GROUP_ID), [20001])

    async with app.test_matcher(cmd_module.backfill_confirm_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": MessageSegment.reply(1) + "已为 1 名成员开启验证。",
            },
        )
        ctx.receive_event(bot, _backfill_event(text="/补验确认"))

    assert started == [20001]
    # 名单已消费，不可重复执行
    assert get_session_store().pop_pending_backfill(str(_GROUP_ID)) == []


@pytest.mark.asyncio
async def test_backfill_confirm_cmd_without_pending(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有待确认名单时提示先发送补验。"""
    from nonebot.adapters.onebot.v11 import Bot as OneBot11Bot, MessageSegment

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        get_session_store,
    )

    _patch_empty_backfill(monkeypatch)
    get_session_store().pop_pending_backfill(str(_GROUP_ID))

    async with app.test_matcher(cmd_module.backfill_confirm_cmd) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        ctx.should_call_api(
            "send_group_msg",
            {
                "group_id": _GROUP_ID,
                "message": (
                    MessageSegment.reply(1) + "没有待确认的补验名单，请先发送「补验」。"
                ),
            },
        )
        ctx.receive_event(bot, _backfill_event(text="/补验确认"))


def test_parse_backfill_args_minutes_and_qq() -> None:
    """补验参数解析：小数视为小时数，大数视为 QQ 号，@ 视为指定成员。"""
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.handle.qq.adapters.onebot11.default import (
        verification as adapter_module,
    )

    class _FakeEvent:
        def __init__(self, message: Message) -> None:
            self.message = message

    hours, targets = adapter_module._parse_backfill_args(
        Message(MessageSegment.text("48 12345678")),
        _FakeEvent(Message([MessageSegment.at(20001)])),  # type: ignore[arg-type]
        24,
    )
    assert hours == 48
    assert targets == [12345678, 20001]

    # 无参数时用默认窗口
    hours, targets = adapter_module._parse_backfill_args(
        Message(MessageSegment.text("")),
        _FakeEvent(Message([])),  # type: ignore[arg-type]
        24,
    )
    assert hours == 24
    assert targets == []
