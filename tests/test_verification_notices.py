"""群公告闸门（``services/verification/notices.py``）的单元测试。

覆盖四组语义，其中**多公告「全部已读」**与**降级不拦**是本功能的要害：

1. 解析与绑定存储（TOML 往返、去重、坏数据容错）；
2. 绑定前校验（**不带确认环节的公告必须跳过**，用户 2026-10-07 明确要求）；
3. 闸门判定（全部已读才放行、任一未读即拦）；
4. 降级（未绑定 / 接口失败 / 开关关闭 → **不拦**）。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.verification import notices

#: 假公告里固定的发布时间与发送者（字段名与 LLBot 返回一致，取值无关断言）。
_PUBLISH_TIME = 1_700_000_000
_SENDER_ID = 10001


class FakeBot:
    """记录调用的假 Bot；按动作名返回预设结果或抛异常。"""

    def __init__(
        self,
        *,
        notices_result: Any = None,
        acklists: dict[str, Any] | None = None,
        notices_error: Exception | None = None,
        ack_error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._notices_result = notices_result
        self._acklists = acklists or {}
        self._notices_error = notices_error
        self._ack_error = ack_error

    async def call_api(self, action: str, **params: Any) -> Any:
        self.calls.append((action, params))
        if action == "_get_group_notice":
            if self._notices_error is not None:
                raise self._notices_error
            return self._notices_result
        if action == "_get_group_notice_acklist":
            if self._ack_error is not None:
                raise self._ack_error
            return self._acklists.get(str(params.get("notice_id")))
        raise AssertionError(f"未预期的接口调用: {action}")


def _raw_notice(
    notice_id: Any = "123",
    *,
    text: str = "公告正文",
    confirm: bool = True,
    pinned: bool = False,
) -> dict[str, Any]:
    """构造一条接口原始公告（字段名与 LLBot 返回一致）。"""
    return {
        "notice_id": notice_id,
        "message": {"text": text},
        "settings": {
            "confirm_required": confirm,
            "pinned": pinned,
            "is_show_edit_card": False,
        },
        "publish_time": _PUBLISH_TIME,
        "sender_id": _SENDER_ID,
    }


# ---------------------------------------------------------------------------
# 1. 解析与绑定存储
# ---------------------------------------------------------------------------


def test_parse_notice_reads_settings_and_text() -> None:
    """正常公告：id/正文/确认开关/置顶/发布时间都要解出来。"""
    notice = notices.parse_notice(_raw_notice("N1", confirm=True, pinned=True))

    assert notice is not None
    assert notice.notice_id == "N1"
    assert notice.text == "公告正文"
    assert notice.confirm_required is True
    assert notice.pinned is True
    assert notice.publish_time == 1_700_000_000
    assert notice.sender_id == 10001


def test_parse_notice_int_id_becomes_str() -> None:
    """接口若给数字 id，也要统一成字符串（否则绑定串不起）。"""
    notice = notices.parse_notice(_raw_notice(987654321))

    assert notice is not None
    assert notice.notice_id == "987654321"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not-a-dict",
        123,
        {"message": {"text": "没有 id"}},
        {"notice_id": "", "message": {"text": "空 id"}},
        {"notice_id": "   ", "message": {"text": "空白 id"}},
    ],
)
def test_parse_notice_rejects_without_id(raw: Any) -> None:
    """没有可用 id 的公告一律丢弃 —— 拿不到 id 就查不了已读名单。"""
    assert notices.parse_notice(raw) is None


def test_parse_notice_tolerates_bad_types() -> None:
    """字段类型乱（时间/发布者非数字、settings 缺失）时不抛，给安全默认值。"""
    notice = notices.parse_notice({
        "notice_id": "X",
        "publish_time": "oops",
        "sender_id": None,
    })

    assert notice is not None
    assert notice.publish_time == 0
    assert notice.sender_id == 0
    assert notice.confirm_required is False


def test_preview_truncates_and_flattens() -> None:
    """预览要压成单行并截断（否则图片被长公告撑爆）。"""
    notice = notices.parse_notice(_raw_notice("A", text="第一行\n第二行 " + "字" * 100))

    assert notice is not None
    assert "\n" not in notice.preview
    assert len(notice.preview) == notices.PREVIEW_LEN + 1  # 截断标记 …
    assert notice.preview.endswith("…")


@pytest.mark.asyncio
async def test_bind_and_unbind_round_trip() -> None:
    """绑定 → 读回 → 解绑 → 读回，都要如实反映。"""
    added, already = await notices.bind_notices(1094538078, ["N1", "N2"])
    assert added == ["N1", "N2"] and already == []

    bindings = await notices.load_bindings()
    assert bindings == {1094538078: ("N1", "N2")}

    # 重复绑定只报告「已存在」，不重复写入
    added2, already2 = await notices.bind_notices(1094538078, ["N2", "N3"])
    assert added2 == ["N3"] and already2 == ["N2"]
    assert (await notices.load_bindings())[1094538078] == ("N1", "N2", "N3")

    removed, missing = await notices.unbind_notices(1094538078, ["N2", "N9"])
    assert removed == ["N2"] and missing == ["N9"]
    assert (await notices.load_bindings())[1094538078] == ("N1", "N3")

    # 解绑最后一个后该群条目消失（不留空群）
    await notices.unbind_notices(1094538078, ["N1", "N3"])
    assert 1094538078 not in await notices.load_bindings()


@pytest.mark.asyncio
async def test_bindings_are_isolated_per_group() -> None:
    """群之间互不串味（一个群的公告不能约束另一个群）。"""
    await notices.bind_notices(111, ["A"])
    await notices.bind_notices(222, ["B"])

    bindings = await notices.load_bindings()
    assert bindings == {111: ("A",), 222: ("B",)}

    await notices.unbind_notices(111, ["A"])
    assert await notices.load_bindings() == {222: ("B",)}


@pytest.mark.asyncio
async def test_load_bindings_survives_broken_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """绑定文件里的坏数据（非法群号/非列表/重复项）要被跳过而不是炸掉读取。"""

    async def fake_load(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "groups": {
                "1094538078": ["N1", "N1", "  N2  ", ""],
                "not-a-group": ["N3"],
                "222": {"notices": ["N4"]},
                "333": "不是列表",
            }
        }

    monkeypatch.setattr(
        "src.plugins.nonebot_plugin_fanqie_verify.database.toml_store"
        ".load_toml_dict_async",
        fake_load,
    )

    bindings = await notices.load_bindings()
    assert bindings == {1094538078: ("N1", "N2"), 222: ("N4",)}


@pytest.mark.asyncio
async def test_load_bindings_swallows_read_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """绑定文件读坏了只降级为空绑定（不能把插件搞崩）。"""

    async def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise OSError("磁盘炸了")

    monkeypatch.setattr(
        "src.plugins.nonebot_plugin_fanqie_verify.database.toml_store"
        ".load_toml_dict_async",
        boom,
    )

    assert await notices.load_bindings() == {}


# ---------------------------------------------------------------------------
# 2. 绑定前校验：必须带「确认阅读」
# ---------------------------------------------------------------------------


def test_is_bindable_requires_confirm_required() -> None:
    """带确认环节才可绑定；不带的一律拒绝并给出原因。"""
    ok = notices.parse_notice(_raw_notice("A", confirm=True))
    bad = notices.parse_notice(_raw_notice("B", confirm=False))
    assert ok is not None and bad is not None

    assert notices.is_bindable(ok) == (True, None)
    allowed, reason = notices.is_bindable(bad)
    assert allowed is False
    assert reason is not None and "确认" in reason


def test_resolve_bindable_splits_ok_and_skipped() -> None:
    """可绑定 / 未开确认 / 群里没有，三类要分清楚（用户要求汇报实际添加的）。"""
    live = [
        notices.parse_notice(_raw_notice("OK1", confirm=True)),
        notices.parse_notice(_raw_notice("NO1", confirm=False)),
        notices.parse_notice(_raw_notice("OK2", confirm=True)),
    ]
    live = [item for item in live if item is not None]

    ok, skipped = notices.resolve_bindable(live, ["OK1", "NO1", "GHOST", "OK2"])

    assert [notice.notice_id for notice in ok] == ["OK1", "OK2"]
    assert [item[0] for item in skipped] == ["NO1", "GHOST"]
    reasons = dict(skipped)
    assert "确认" in reasons["NO1"]
    assert "找不到" in reasons["GHOST"]


# ---------------------------------------------------------------------------
# 3. 闸门判定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_notice_read_passes_when_all_read() -> None:
    """全部绑定公告都已读 → 放行。"""
    await notices.bind_notices(111, ["N1", "N2"])
    bot = FakeBot(
        acklists={
            "N1": [{"user_id": 42, "display_name": "我"}],
            "N2": [{"user_id": "42"}],
        }
    )

    gate = await notices.check_notice_read(bot, 111, 42)  # type: ignore[arg-type]

    assert gate.blocked is False
    assert gate.detail["read"] == ["N1", "N2"]
    # 两个公告都要查（漏查一条就等于没闸门）
    assert [call[0] for call in bot.calls] == ["_get_group_notice_acklist"] * 2


@pytest.mark.asyncio
async def test_check_notice_read_blocks_on_any_unread() -> None:
    """**任一**公告未读就拦下，并在原因里带上未读 id 供管理员对照。"""
    await notices.bind_notices(111, ["N1", "N2"])
    bot = FakeBot(acklists={"N1": [{"user_id": 42}], "N2": [{"user_id": 7}]})

    gate = await notices.check_notice_read(bot, 111, 42)  # type: ignore[arg-type]

    assert gate.blocked is True
    assert gate.detail["unread"] == ["N2"]
    assert gate.detail["read"] == ["N1"]
    assert "N2" in (gate.reason or "") or "N2" in str(gate.detail)


@pytest.mark.asyncio
async def test_acklist_requests_correct_list_type() -> None:
    """查已读要传 ``list_type='ack'``、查未读传 ``'unack'``（传反了判定就颠倒）。"""
    bot = FakeBot(acklists={"N1": []})

    await notices.fetch_acklist(bot, 111, "N1")  # type: ignore[arg-type]
    await notices.fetch_acklist(bot, 111, "N1", unread=True)  # type: ignore[arg-type]

    assert bot.calls[0][1]["list_type"] == "ack"
    assert bot.calls[1][1]["list_type"] == "unack"
    assert bot.calls[0][1]["notice_id"] == "N1"


def test_acklist_user_ids_filters_garbage() -> None:
    """名单里的脏数据（缺字段/非数字）要被丢掉，别把异常带进判定。"""
    entries = [
        {"user_id": 42},
        {"user_id": " 43 "},
        {"user_id": None},
        {"user_id": "abc"},
        {"display_name": "没有 user_id"},
        "不是字典",  # type: ignore[list-item]
    ]

    assert notices.acklist_user_ids(entries) == {42, 43}  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# 4. 降级：这些情况一律**不拦**
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_skips_when_group_has_no_binding() -> None:
    """未绑定公告的群不拦（否则升级后所有群的新人全被挡住）。"""
    bot = FakeBot()

    gate = await notices.check_notice_read(bot, 111, 42)  # type: ignore[arg-type]

    assert gate.blocked is False
    assert gate.detail["skipped"] == "no_binding"
    assert bot.calls == []  # 没绑定就不该白调接口


@pytest.mark.asyncio
async def test_gate_degrades_on_api_error() -> None:
    """查询接口失败 → 降级放行并记 ``api_error``（LLBot 抽风不该封群）。"""
    await notices.bind_notices(111, ["N1"])
    bot = FakeBot(ack_error=RuntimeError("LLBot 掉线"))

    gate = await notices.check_notice_read(bot, 111, 42)  # type: ignore[arg-type]

    assert gate.blocked is False
    assert gate.detail["skipped"] == "api_error"
    assert gate.detail["notice_id"] == "N1"


@pytest.mark.asyncio
async def test_gate_degrades_when_multi_notice_lookup_fails_midway() -> None:
    """多条绑定时中途失败也要降级（不能只查了一半就拦人）。"""
    await notices.bind_notices(111, ["N1", "N2"])
    bot = FakeBot(acklists={"N1": [{"user_id": 42}], "N2": None})

    gate = await notices.check_notice_read(bot, 111, 42)  # type: ignore[arg-type]

    assert gate.blocked is False
    assert gate.detail.get("skipped") == "api_error"


@pytest.mark.asyncio
async def test_gate_disabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关掉时直接跳过（连绑定都不看）。"""
    await notices.bind_notices(111, ["N1"])
    monkeypatch.setattr(
        "src.plugins.nonebot_plugin_fanqie_verify.core.config.plugin_config."
        "fanqie_notice_gate_enabled",
        False,
        raising=False,
    )
    bot = FakeBot(acklists={"N1": []})

    gate = await notices.check_notice_read(bot, 111, 42)  # type: ignore[arg-type]

    assert gate.blocked is False
    assert gate.detail["skipped"] == "disabled"
    assert bot.calls == []


# ---------------------------------------------------------------------------
# 5. 接口层的「失败」与「为空」必须区分
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_group_notices_distinguishes_failure_and_empty() -> None:
    """接口失败回 ``None``，群里真没公告回 ``[]`` —— 两者不能混。"""
    failing = FakeBot(notices_error=RuntimeError("boom"))
    assert await notices.fetch_group_notices(failing, 111) is None  # type: ignore[arg-type]

    empty = FakeBot(notices_result=[])
    assert await notices.fetch_group_notices(empty, 111) == []  # type: ignore[arg-type]

    weird = FakeBot(notices_result={"not": "a list"})
    assert await notices.fetch_group_notices(weird, 111) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetch_group_notices_drops_invalid_entries() -> None:
    """列表里夹着无效项时只丢该项，不整体失败。"""
    bot = FakeBot(
        notices_result=[
            _raw_notice("N1"),
            {"message": {"text": "没有 id"}},
            "垃圾",
            _raw_notice("N2", confirm=False),
        ]
    )

    result = await notices.fetch_group_notices(bot, 111)  # type: ignore[arg-type]

    assert result is not None
    assert [notice.notice_id for notice in result] == ["N1", "N2"]
