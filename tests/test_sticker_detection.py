"""表情包/非截图内容的识别测试。

背景：成员发表的表情包曾被当作「提交截图」触发验证流程。LLBot（NTQQ）会
产生三类形态，本模块逐一覆盖：

1. 独立表情段 ``mface``（商城表情）/ ``face``（系统表情）；
2. image 段带 emoji 字段（LLOneBot 等把商城表情转成 image）；
3. **image 段 ``subType`` 非 0** —— 用户收藏的自定义表情包（customFace）
   同样走 picElement 路径产出不带 emoji 字段的 image 段，仅能靠 subType
   区分（LLBot 把 QQ 的 bizType 原样放进 subType，普通截图实测恒为 0）。

"""

from __future__ import annotations

import time
from typing import Any

from nonebug import App
import pytest

from src.plugins.nonebot_plugin_ocr_fanqie_novel.handle.qq.adapters.onebot11.default import (
    verification as adapter_module,
)
from src.plugins.nonebot_plugin_ocr_fanqie_novel.handle.qq.commands import (
    verification as cmd_module,
)

_SELF_ID = 12345
_GROUP_ID = 123
_USER_ID = 10001


def _seg(seg_type: str, **data: Any) -> Any:
    """构造一个消息段。"""
    from nonebot.adapters.onebot.v11.message import MessageSegment

    return MessageSegment(type=seg_type, data=data)


def _image_event(segments: list[Any]) -> Any:
    """构造仅含给定消息段的群消息事件。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent
    from nonebot.adapters.onebot.v11.message import Message

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
        sender={"user_id": _USER_ID, "nickname": "成员", "role": "member"},
        raw_message="[图片]",
        message=Message(segments),
        font=0,
    )  # type: ignore[call-arg]


# ------------------------------------------------------------ _is_sticker


def test_mface_segment_is_sticker() -> None:
    """商城表情（独立 mface 段）判定为表情。"""
    assert cmd_module._is_sticker(
        _seg("mface", emoji_id="abc", emoji_package_id=1, url="http://x")
    )


def test_face_segment_is_sticker() -> None:
    """QQ 系统表情（face 段）判定为表情。"""
    assert cmd_module._is_sticker(_seg("face", id="14"))


def test_dice_and_rps_are_stickers() -> None:
    """超级表情（骰子/猜拳）判定为表情。"""
    assert cmd_module._is_sticker(_seg("dice", result="1"))
    assert cmd_module._is_sticker(_seg("rps", result="1"))


def test_plain_screenshot_is_not_sticker() -> None:
    """普通截图（subType=0、无 emoji 字段）不是表情。"""
    assert not cmd_module._is_sticker(
        _seg(
            "image",
            file="abc.png",
            subType=0,
            url="https://example.invalid/a.png",
            file_size="175867",
        )
    )


def test_image_without_subtype_is_not_sticker() -> None:
    """缺少 subType 字段的图片按普通图片处理（不误杀）。"""
    assert not cmd_module._is_sticker(
        _seg("image", file="abc.png", url="https://example.invalid/a.png")
    )


def test_image_with_emoji_camel_is_sticker() -> None:
    """Image 段带驼峰 emojiId（LLOneBot 风格）判定为表情。"""
    assert cmd_module._is_sticker(_seg("image", emojiId="abc", url="http://x"))


def test_image_with_emoji_snake_is_sticker() -> None:
    """Image 段带下划线 emoji_id 判定为表情。"""
    assert cmd_module._is_sticker(_seg("image", emoji_id="abc", url="http://x"))


def test_custom_face_image_with_nonzero_subtype_is_sticker() -> None:
    """自定义表情包：image 段 subType 非 0，无 emoji 字段，仍须判为表情。"""
    assert cmd_module._is_sticker(
        _seg("image", file="sticker.gif", subType=1, url="http://x")
    )
    assert cmd_module._is_sticker(
        _seg("image", file="sticker.gif", subType="1", url="http://x")
    )


def test_text_segment_is_not_sticker() -> None:
    """文本段不是表情（也不是图片）。"""
    assert not cmd_module._is_sticker(_seg("text", text="你好"))


# --------------------------------------------------------- _contains_image


def test_contains_image_false_for_mface_only() -> None:
    """只发表情段时不认为含图片。"""
    assert not cmd_module._contains_image(_image_event([_seg("mface", emoji_id="a")]))


def test_contains_image_false_for_custom_face() -> None:
    """只发自定义表情包（subType 非 0）时不认为含图片。"""
    assert not cmd_module._contains_image(
        _image_event([_seg("image", file="s.gif", subType=1, url="http://x")])
    )


def test_contains_image_true_for_plain_screenshot() -> None:
    """普通截图仍视为含图片。"""
    assert cmd_module._contains_image(
        _image_event([_seg("image", file="a.png", subType=0, url="http://x")])
    )


def test_contains_image_true_when_screenshot_accompanies_sticker() -> None:
    """表情包 + 截图同时出现时仍应识别出图片。"""
    assert cmd_module._contains_image(
        _image_event([
            _seg("mface", emoji_id="a"),
            _seg("image", file="a.png", subType=0, url="http://x"),
        ])
    )


# ------------------------------------------------------------- _image_url


def test_image_url_skips_sticker_and_takes_screenshot() -> None:
    """取图时跳过后面的表情包，返回普通截图的 URL。"""
    event = _image_event([
        _seg("image", file="s.gif", subType=1, url="http://sticker"),
        _seg("image", file="a.png", subType=0, url="http://screenshot"),
    ])
    assert adapter_module._image_url(event) == "http://screenshot"


def test_image_url_none_when_only_sticker() -> None:
    """只有表情包时取不到图片 URL。"""
    event = _image_event([_seg("image", file="s.gif", subType=1, url="http://s")])
    assert adapter_module._image_url(event) is None


def test_image_url_falls_back_to_file() -> None:
    """无 url 时回退到 file:// 形式。"""
    event = _image_event([_seg("image", file="abc.png", subType=0)])
    assert adapter_module._image_url(event) == "file://abc.png"


# ------------------------------------------------------------------ 端到端


@pytest.mark.asyncio
async def test_custom_face_does_not_trigger_verification(app: App) -> None:
    """成员发自定义表情包时不应触发验证处理（不产生任何发送调用）。"""
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
        user_id=str(_USER_ID),
        bot_id=str(_SELF_ID),
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

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
        sender={"user_id": _USER_ID, "nickname": "成员", "role": "member"},
        raw_message="[CQ:image,file=s.gif,subType=1]",
        message=Message([
            MessageSegment(type="image", data={"file": "s.gif", "subType": 1})
        ]),
        font=0,
    )  # type: ignore[call-arg]

    # 未声明任何 should_call_api：nonebug 严格模式下，一旦插件发起
    # send_group_msg（即误把表情包当截图处理）测试会直接失败。
    async with app.test_matcher(cmd_module.image_submission) as ctx:
        bot = ctx.create_bot(base=OneBot11Bot)
        ctx.receive_event(bot, event)

    # 会话仍在等待提交截图（未被当作有效提交消费）
    record = store.get(str(_GROUP_ID), str(_USER_ID))
    assert record is not None
    assert record.status == "waiting"
    assert record.retry_count == 0
