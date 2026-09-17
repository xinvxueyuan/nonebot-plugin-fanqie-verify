"""入群验证流程编排测试（FR1/FR2/FR5/FR6/FR7/FR8/FR9）。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
    admin_decision,
    flow as flow_module,
    get_session_store,
    handle_admin_decision_timeout,
    handle_reminder,
    handle_submission,
    handle_timeout,
    restore_pending_sessions,
    review_verification,
    start_verification,
)


class FakeBot:
    """最小 OneBot11 Bot 替身。"""

    self_id = "bot1"

    def __init__(
        self, *, muted_until: int = 0, in_group: bool = True, role: str = "member"
    ) -> None:
        self.calls: list[Any] = []
        self.muted_until = muted_until
        self.in_group = in_group
        self.role = role

    async def send_group_msg(self, **kwargs: Any) -> None:
        self.calls.append(("send_group_msg", kwargs))

    async def set_group_kick(self, **kwargs: Any) -> None:
        self.calls.append(("set_group_kick", kwargs))

    async def set_group_ban(self, **kwargs: Any) -> None:
        self.calls.append(("set_group_ban", kwargs))

    async def send_private_msg(self, **kwargs: Any) -> None:
        self.calls.append(("send_private_msg", kwargs))

    async def get_group_member_info(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_group_member_info", kwargs))
        if not self.in_group:
            from nonebot.adapters.onebot.v11.exception import ActionFailed

            raise ActionFailed(retcode=100, retmsg="member not found", data=None)
        return {
            "user_id": kwargs.get("user_id", 0),
            "role": self.role,
            "card": "",
            "nickname": "某用户",
            "shut_up_timestamp": self.muted_until,
        }

    async def call_api(self, api: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("call_api", api, kwargs))
        return {"card": "某用户", "nickname": "某用户"}


@pytest.fixture(autouse=True)
def _fresh_store() -> Generator[None]:
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        session,
    )

    before = session._store
    session._store = None
    try:
        yield
    finally:
        session._store = before


@pytest.fixture(autouse=True)
def _lenient_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让测试群 123 通过策略检查（配置作者节点，作者不在白名单约束）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="阿百川大鬼"),),
                ),
            },
        ),
    )


@pytest.mark.asyncio
async def test_start_verification_sends_guide() -> None:
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    assert get_session_store().is_waiting("123", "10001") is True
    send_calls = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert len(send_calls) == 1
    assert "欢迎新人进群" in str(send_calls[0][1]["message"])


@pytest.mark.asyncio
async def test_start_verification_skips_non_monitored_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """群不在监控范围（未配置群节点）时应跳过验证，不建会话不发引导。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                456: GroupPolicy(
                    group_id=456,
                    authors=(AuthorEntry(name="阿百川大鬼"),),
                ),
            },
        ),
    )

    bot: Any = FakeBot()
    record = await start_verification(bot, group_id=123, user_id=10001)

    assert record is None
    assert get_session_store().is_waiting("123", "10001") is False
    send_calls = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert send_calls == []


@pytest.mark.asyncio
async def test_handle_submission_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有效截图应通过验证并发送欢迎消息。"""

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_evidence() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/shelf.png",
    )

    assert "验证通过" in reply
    assert get_session_store().get("123", "10001").status == "approved"  # type: ignore[union-attr]
    welcomes = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert any("欢迎新人进群" in str(c[1]["message"]) for c in welcomes)


@pytest.mark.asyncio
async def test_vision_review_vetoes(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCR 通过但视觉复核否决时，应拒绝。"""
    from unittest.mock import AsyncMock

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_evidence() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)
    monkeypatch.setattr(
        flow_module.vision,
        "vision_fallback",
        AsyncMock(
            return_value=flow_module.vision.VisionVerdict(
                passed=False, reason="未识别到书评详情标题"
            )
        ),
    )

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)
    reply = await handle_submission(
        bot, group_id=123, user_id=10001, image_url="https://example.com/shelf.png"
    )

    assert "验证未通过" in reply


@pytest.mark.asyncio
async def test_vision_review_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCR 通过且视觉复核通过时，应放行。"""
    from unittest.mock import AsyncMock

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_evidence() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)
    monkeypatch.setattr(
        flow_module.vision,
        "vision_fallback",
        AsyncMock(
            return_value=flow_module.vision.VisionVerdict(passed=True, reason=None)
        ),
    )

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)
    reply = await handle_submission(
        bot, group_id=123, user_id=10001, image_url="https://example.com/shelf.png"
    )

    assert "验证通过" in reply


@pytest.mark.asyncio
async def test_ocr_disabled_skips_ocr_and_uses_vision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FANQIE_OCR_ENABLED=False 时应跳过 OCR，直接由视觉模型判定。"""
    from unittest.mock import AsyncMock

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    recognize_calls: list[str] = []

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = models
        recognize_calls.append(url)
        return {_m: _ocr_result_with_evidence() for _m in ["PaddleOCR-VL-1.6"]}

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)
    monkeypatch.setattr(plugin_config, "fanqie_ocr_enabled", False)
    monkeypatch.setattr(
        flow_module.vision,
        "vision_fallback",
        AsyncMock(
            return_value=flow_module.vision.VisionVerdict(passed=True, reason=None)
        ),
    )

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)
    reply = await handle_submission(
        bot, group_id=123, user_id=10001, image_url="https://example.com/shelf.png"
    )

    assert recognize_calls == []  # OCR 完全未被调用
    assert "验证通过" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "approved"


@pytest.mark.asyncio
async def test_ocr_disabled_vision_unavailable_counts_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OCR 停用时视觉模型不可用，应按识别失败计次（消耗重试次数）。"""
    from unittest.mock import AsyncMock

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_ocr_enabled", False)
    monkeypatch.setattr(plugin_config, "fanqie_max_attempts", 3)
    monkeypatch.setattr(
        flow_module.vision, "vision_fallback", AsyncMock(return_value=None)
    )

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)
    reply = await handle_submission(
        bot, group_id=123, user_id=10001, image_url="https://example.com/shelf.png"
    )

    assert "识别失败" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.retry_count == 1


@pytest.mark.asyncio
async def test_handle_submission_insufficient_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """信息不足应提示重试并计数，不直接踢出。"""

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {_m: _ocr_result_empty() for _m in (models or ["PaddleOCR-VL-1.6"])}

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/blank.png",
    )

    assert "剩余尝试次数" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.retry_count == 1
    assert record.status == "waiting"


@pytest.mark.asyncio
async def test_handle_submission_other_review_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """他人书评（无「我」徽章）应视为信息不足并重试。"""

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_other_review() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/other.png",
    )

    assert "剩余尝试次数" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.retry_count == 1
    assert record.status == "waiting"


@pytest.mark.asyncio
async def test_handle_submission_reject_notifies_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判定拒绝应结束会话并私信管理员决策（不禁言）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(plugin_config, "fanqie_admin_ids", {90001})
    monkeypatch.setattr(plugin_config, "fanqie_notify_admin", True)
    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="张三"),),
                ),
            },
        ),
    )

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_author("李四")
            for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/bad.png",
    )

    assert "验证未通过" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "awaiting_admin"
    bans = [c for c in bot.calls if c[0] == "set_group_ban"]
    assert bans == []
    privates = [c for c in bot.calls if c[0] == "send_private_msg"]
    assert any("验证失败" in str(c[1]["message"]) for c in privates)
    assert any("/keep" in str(c[1]["message"]) for c in privates)


@pytest.mark.asyncio
async def test_handle_submission_policy_rejects_missing_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """策略要求指定元素但截图缺少时应拒绝。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author", "rating"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="阿百川大鬼"),),
                ),
            },
        ),
    )

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_evidence() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/shelf.png",
    )

    assert "验证未通过" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "awaiting_admin"


@pytest.mark.asyncio
async def test_handle_submission_policy_rejects_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作者不在白名单时应拒绝。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        policy as policy_module,
    )
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification.policy import (
        AuthorEntry,
        GroupPolicy,
        VerificationPolicy,
    )

    monkeypatch.setattr(
        policy_module,
        "_policy_cache",
        VerificationPolicy(
            require_all=False,
            required_elements=frozenset({"book_name", "author"}),
            groups={
                123: GroupPolicy(
                    group_id=123,
                    authors=(AuthorEntry(name="张三"),),
                ),
            },
        ),
    )

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_author("李四")
            for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)

    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/shelf.png",
    )

    assert "验证未通过" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "awaiting_admin"


@pytest.mark.asyncio
async def test_handle_timeout_notifies_and_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时应转入待管理员决策并私信通知（不自动踢出）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_admin_ids", {90001})
    monkeypatch.setattr(plugin_config, "fanqie_notify_admin", True)

    bot: Any = FakeBot()

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    await start_verification(bot, group_id=123, user_id=10001)

    await handle_timeout("123", "10001")

    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert kicks == []
    privates = [c for c in bot.calls if c[0] == "send_private_msg"]
    assert len(privates) >= 1
    admin_notices = [c for c in privates if c[1].get("user_id") == 90001]
    assert len(admin_notices) == 1
    assert "/keep" in str(admin_notices[0][1]["message"])
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "awaiting_admin"


@pytest.mark.asyncio
async def test_handle_timeout_bot_missing_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bot 缺失时超时处理应将会话转 expired，避免永久堆积（0 秒 bug）。"""
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    async def fake_get_bot(bot_id: str) -> Any:
        _ = bot_id
        return None

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    await handle_timeout("123", "10001")

    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "expired"


@pytest.mark.asyncio
async def test_admin_kick_decision() -> None:
    """管理员 /kick 应踢出并结束会话。"""
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await admin_decision(bot, group_id=123, user_id=10001, keep=False)

    assert "已将该成员移出群聊" in reply
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert len(kicks) == 1
    assert get_session_store().get("123", "10001").status == "kicked"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_admin_keep_decision() -> None:
    """管理员 /keep 应通过并保留成员。"""
    bot: Any = FakeBot()
    record = await start_verification(bot, group_id=123, user_id=10001)
    assert record is not None
    store = get_session_store()

    reply = await admin_decision(bot, group_id=123, user_id=10001, keep=True)

    assert "直接批准" in reply
    assert store.get("123", "10001").status == "approved"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_admin_keep_requires_active_verification() -> None:
    """不在验证流程中的成员不应被 /keep 误批准。"""
    bot: Any = FakeBot()
    reply = await admin_decision(bot, group_id=123, user_id=10001, keep=True)
    assert "不在验证流程" in reply
    # 未结束任何会话、未发欢迎
    assert get_session_store().get("123", "10001") is None
    welcomes = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert all("欢迎" not in str(c[1]["message"]) for c in welcomes)


@pytest.mark.asyncio
async def test_admin_approve_direct_approves_awaiting_admin() -> None:
    """待管理员决策(awaiting_admin)成员可被直接批准。"""
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)
    store = get_session_store()
    store.await_admin("123", "10001")

    reply = await admin_decision(bot, group_id=123, user_id=10001, keep=True)

    assert "直接批准" in reply
    assert store.get("123", "10001").status == "approved"  # type: ignore[union-attr]
    welcomes = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert any("欢迎" in str(c[1]["message"]) for c in welcomes)


@pytest.mark.asyncio
async def test_handle_timeout_member_already_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员已退群时，超时处理应跳过动作并结束会话。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_notify_admin", False)

    bot: Any = FakeBot(in_group=False)

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    get_session_store().start(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

    await handle_timeout("123", "10001")

    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert kicks == []
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "expired"


@pytest.mark.asyncio
async def test_handle_submission_member_already_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员已退群时，提交截图应直接结束会话。"""

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_evidence() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)

    bot: Any = FakeBot(in_group=False)
    get_session_store().start(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

    reply = await handle_submission(
        bot,
        group_id=123,
        user_id=10001,
        image_url="https://example.com/shelf.png",
    )

    assert "不在群聊" in reply
    assert get_session_store().get("123", "10001") is None


@pytest.mark.asyncio
async def test_handle_submission_member_muted_state_synced() -> None:
    """成员已处于禁言时，会话应同步 is_muted。"""
    future = int(__import__("time").time()) + 3600
    bot: Any = FakeBot(muted_until=future)
    await start_verification(bot, group_id=123, user_id=10001)

    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.is_muted is True


@pytest.mark.asyncio
async def test_await_admin_schedules_decision_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """转入待管理员决策后应设置 16h 截止并保留会话。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_admin_decision_timeout", 57600)
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    store = get_session_store()
    store.await_admin("123", "10001")

    record = store.get("123", "10001")
    assert record is not None
    assert record.status == "awaiting_admin"
    assert record.expires_at is not None
    remaining = (
        record.expires_at
        - __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    ).total_seconds()
    assert 57000 < remaining <= 57600
    assert store.list_awaiting_admin("123") == (record,)


@pytest.mark.asyncio
async def test_admin_decision_timeout_announces_and_kicks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理决策超时应群内通报并踢出成员。"""
    bot: Any = FakeBot()

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    await start_verification(bot, group_id=123, user_id=10001)
    get_session_store().await_admin("123", "10001")

    await handle_admin_decision_timeout("123", "10001")

    announces = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert any("移出群聊" in str(c[1]["message"]) for c in announces)
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert len(kicks) == 1
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "kicked"


@pytest.mark.asyncio
async def test_admin_decision_timeout_member_left_no_kick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理决策超时时成员已退群则仅结束会话。"""
    bot: Any = FakeBot(in_group=False)

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    get_session_store().start(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    get_session_store().await_admin("123", "10001")

    await handle_admin_decision_timeout("123", "10001")

    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert kicks == []
    record = get_session_store().get("123", "10001")
    assert record is not None
    assert record.status == "expired"


@pytest.mark.asyncio
async def test_restore_pending_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重启后应恢复 waiting 与 awaiting_admin 会话并重建超时。"""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        session as session_module,
    )

    now = datetime.now(UTC)
    persisted: list[Any] = [
        SimpleNamespace(
            group_id="123",
            user_id="10001",
            bot_id="bot1",
            platform_id="qq",
            adapter_id="~onebot.v11",
            protocol_id="default",
            trigger_time=now - timedelta(minutes=2),
            expires_at=now + timedelta(minutes=3),
            retry_count=0,
            review_count=2,
            is_muted=False,
            last_extracted=None,
            status="waiting",
        ),
        SimpleNamespace(
            group_id="123",
            user_id="20001",
            bot_id="bot1",
            platform_id="qq",
            adapter_id="~onebot.v11",
            protocol_id="default",
            trigger_time=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=15),
            retry_count=0,
            review_count=1,
            is_muted=False,
            last_extracted=None,
            status="awaiting_admin",
        ),
    ]

    async def fake_list_pending_sessions(session: Any) -> list[Any]:
        _ = session
        return persisted

    monkeypatch.setattr(
        flow_module.repository,
        "list_pending_sessions",
        fake_list_pending_sessions,
    )
    monkeypatch.setattr(plugin_config, "fanqie_message_store_enabled", True)

    session_module._store = None

    restored = await restore_pending_sessions()
    assert restored == 2

    store = get_session_store()
    waiting = store.get("123", "10001")
    awaiting = store.get("123", "20001")
    assert waiting is not None and waiting.status == "waiting"
    assert awaiting is not None and awaiting.status == "awaiting_admin"
    assert len(store.list_waiting()) == 1
    assert len(store.list_awaiting_admin("123")) == 1
    # 重审计数应从数据库恢复，重启后不丢
    assert waiting is not None and waiting.review_count == 2
    assert awaiting is not None and awaiting.review_count == 1


def _ocr_result_with_evidence() -> Any:
    """构造能提取出有效证据的书评详情页 OCR 结果（本人发布）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
        OCRPage,
        OCRResult,
        OCRTextLine,
    )

    return OCRResult(
        job_id="job-pass",
        pages=[
            OCRPage(
                lines=[
                    OCRTextLine(text="书评详情", confidence=0.99, box=_box(0)),
                    OCRTextLine(text="新v学员", confidence=0.98, box=_box(30)),
                    OCRTextLine(text="我", confidence=0.97, box=_box(40)),
                    OCRTextLine(text="刚刚", confidence=0.96, box=_box(60)),
                    OCRTextLine(text="阅读2小时后点评", confidence=0.95, box=_box(80)),
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


def _ocr_result_with_author(author: str) -> Any:
    """构造带指定作者的书评详情页 OCR 结果。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
        OCRPage,
        OCRResult,
        OCRTextLine,
    )

    return OCRResult(
        job_id="job-author",
        pages=[
            OCRPage(
                lines=[
                    OCRTextLine(text="书评详情", confidence=0.99, box=_box(0)),
                    OCRTextLine(text="新v学员", confidence=0.98, box=_box(30)),
                    OCRTextLine(text="我", confidence=0.97, box=_box(40)),
                    OCRTextLine(text="刚刚", confidence=0.96, box=_box(60)),
                    OCRTextLine(text="阅读2小时后点评", confidence=0.95, box=_box(80)),
                    OCRTextLine(
                        text="综漫：吉他雇佣兵无法找到归宿？",
                        confidence=0.98,
                        box=_box(200),
                    ),
                    OCRTextLine(text=author, confidence=0.97, box=_box(220)),
                ]
            )
        ],
    )


def _ocr_result_empty() -> Any:
    """构造无有效信息的 OCR 结果（无「我」徽章）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
        OCRPage,
        OCRResult,
        OCRTextLine,
    )

    return OCRResult(
        job_id="job-empty",
        pages=[OCRPage(lines=[OCRTextLine(text="首页", confidence=0.9, box=_box(0))])],
    )


def _ocr_result_other_review() -> Any:
    """构造他人书评的 OCR 结果（无「我」徽章）。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.ocr import (
        OCRPage,
        OCRResult,
        OCRTextLine,
    )

    return OCRResult(
        job_id="job-other",
        pages=[
            OCRPage(
                lines=[
                    OCRTextLine(text="书评详情", confidence=0.99, box=_box(0)),
                    OCRTextLine(text="叶月辉夜", confidence=0.98, box=_box(30)),
                    OCRTextLine(text="05-28", confidence=0.97, box=_box(60)),
                    OCRTextLine(text="阅读11小时后点评", confidence=0.96, box=_box(80)),
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


# ---- 重审（review_verification） ----


@pytest.mark.asyncio
async def test_review_self_without_session_rejected() -> None:
    """普通成员无待处理会话时重审被拒绝。"""
    bot: Any = FakeBot()
    reply = await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=False
    )
    assert "没有待处理" in reply
    assert get_session_store().get("123", "10001") is None


@pytest.mark.asyncio
async def test_review_self_restarts_flow_and_consumes_quota() -> None:
    """普通成员重审自己：重开流程、重试清零、重审计数 +1。"""
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)
    store = get_session_store()
    store.mark_retry("123", "10001")  # 模拟已失败过一次

    reply = await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=False
    )
    assert "重新发起验证" in reply
    record = store.get("123", "10001")
    assert record is not None
    assert record.retry_count == 0  # 重审重置 OCR 重试
    assert record.review_count == 1  # 消耗一次重审机会


@pytest.mark.asyncio
async def test_review_self_limited_by_max_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通成员重审次数达上限后被拒绝。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_review_max_times", 2)
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)
    store = get_session_store()

    assert "重新发起验证" in await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=False
    )
    assert "重新发起验证" in await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=False
    )
    reply = await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=False
    )
    assert "上限" in reply
    record = store.get("123", "10001")
    assert record is not None and record.review_count == 2


@pytest.mark.asyncio
async def test_review_admin_unlimited_and_opens_flow() -> None:
    """管理员重审：无需待处理会话即可重开验证，且不消耗次数。"""
    bot: Any = FakeBot()
    reply = await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=True
    )
    assert "重新发起验证" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None and record.status == "waiting"
    assert record.review_count == 0  # 管理员重审不计次


@pytest.mark.asyncio
async def test_review_rejects_admin_target() -> None:
    """目标是群管理/群主时拒绝重审。"""
    bot: Any = FakeBot(role="admin")
    reply = await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=True
    )
    assert "管理" in reply


@pytest.mark.asyncio
async def test_review_rejects_missing_member() -> None:
    """目标不在群聊时拒绝重审。"""
    bot: Any = FakeBot(in_group=False)
    reply = await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=True
    )
    assert "不在群" in reply


@pytest.mark.asyncio
async def test_handle_timeout_announces_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员响应超时应先在群内 @ 成员提示已超时、可重审。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_admin_ids", {90001})
    monkeypatch.setattr(plugin_config, "fanqie_notify_admin", True)

    bot: Any = FakeBot()

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    await start_verification(bot, group_id=123, user_id=10001)

    await handle_timeout("123", "10001")

    announces = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert any(
        "超时" in str(c[1]["message"]) and "重审" in str(c[1]["message"])
        for c in announces
    )
    # 仍未移出
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert kicks == []
    record = get_session_store().get("123", "10001")
    assert record is not None and record.status == "awaiting_admin"


@pytest.mark.asyncio
async def test_handle_reminder_announces_before_kick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待管理员决策成员被移出前应收到群内提醒（含剩余时长）。"""
    bot: Any = FakeBot()

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    await start_verification(bot, group_id=123, user_id=10001)
    get_session_store().await_admin("123", "10001")

    await handle_reminder("123", "10001", remaining_seconds=3540)

    announces = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert any(
        "移出" in str(c[1]["message"]) and "重审" in str(c[1]["message"])
        for c in announces
    )
    # 提醒不触发踢出
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert kicks == []
    record = get_session_store().get("123", "10001")
    assert record is not None and record.status == "awaiting_admin"


@pytest.mark.asyncio
async def test_handle_reminder_member_left_no_announce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员已不在群时，移出前提醒应静默跳过。"""
    bot: Any = FakeBot(in_group=False)

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    await start_verification(bot, group_id=123, user_id=10001)
    get_session_store().await_admin("123", "10001")

    await handle_reminder("123", "10001", remaining_seconds=3540)

    announces = [c for c in bot.calls if c[0] == "send_group_msg"]
    assert all("移出" not in str(c[1]["message"]) for c in announces)
    kicks = [c for c in bot.calls if c[0] == "set_group_kick"]
    assert kicks == []


async def _record_events(monkeypatch: pytest.MonkeyPatch, bot: Any) -> list[str]:
    """Mock 事件写入并返回已记录的事件类型序列。"""
    recorded: list[str] = []

    async def fake_record_event(_session: Any, event: Any) -> Any:
        recorded.append(event.event_type)
        return event

    monkeypatch.setattr(
        flow_module.repository,
        "record_verification_event",
        fake_record_event,
    )

    async def fake_get_bot(bot_id: str) -> FakeBot:
        _ = bot_id
        return bot

    monkeypatch.setattr(flow_module, "_get_bot", fake_get_bot)
    return recorded


@pytest.mark.asyncio
async def test_flow_records_start_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开启验证应记录 verify.start 事件。"""
    bot: Any = FakeBot()
    recorded = await _record_events(monkeypatch, bot)
    await start_verification(bot, group_id=123, user_id=10001)
    assert "verify.start" in recorded


@pytest.mark.asyncio
async def test_flow_records_pass_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通过验证应记录 verify.pass 事件。"""

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_evidence() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)
    bot: Any = FakeBot()
    recorded = await _record_events(monkeypatch, bot)
    await start_verification(bot, group_id=123, user_id=10001)
    await handle_submission(
        bot, group_id=123, user_id=10001, image_url="https://example.com/a.png"
    )
    assert "verify.start" in recorded
    assert "verify.pass" in recorded


@pytest.mark.asyncio
async def test_flow_records_timeout_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """响应超时应记录 verify.timeout 事件。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_admin_ids", {90001})
    monkeypatch.setattr(plugin_config, "fanqie_notify_admin", True)
    bot: Any = FakeBot()
    recorded = await _record_events(monkeypatch, bot)
    await start_verification(bot, group_id=123, user_id=10001)
    await handle_timeout("123", "10001")
    assert "verify.timeout" in recorded


@pytest.mark.asyncio
async def test_flow_records_reminder_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """移出前提醒应记录 verify.reminder 事件。"""
    bot: Any = FakeBot()
    recorded = await _record_events(monkeypatch, bot)
    await start_verification(bot, group_id=123, user_id=10001)
    get_session_store().await_admin("123", "10001")
    await handle_reminder("123", "10001", remaining_seconds=3540)
    assert "verify.reminder" in recorded


@pytest.mark.asyncio
async def test_flow_records_admin_keep_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员 /keep 应记录 verify.admin_keep 事件。"""
    bot: Any = FakeBot()
    recorded = await _record_events(monkeypatch, bot)
    await start_verification(bot, group_id=123, user_id=10001)
    await admin_decision(bot, group_id=123, user_id=10001, keep=True)
    assert "verify.admin_keep" in recorded


@pytest.mark.asyncio
async def test_flow_records_review_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通成员重审应记录 verify.review 事件（含 by_admin 标记）。"""
    bot: Any = FakeBot()
    recorded = await _record_events(monkeypatch, bot)
    await start_verification(bot, group_id=123, user_id=10001)
    await review_verification(
        bot, group_id=123, user_id=10001, triggered_by_admin=False
    )
    assert "verify.review" in recorded


@pytest.mark.asyncio
async def test_private_submission_single_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """私聊验证：恰有一个待验证群时直接验证。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        handle_private_submission,
    )

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_evidence() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)
    bot: Any = FakeBot()
    await start_verification(bot, group_id=123, user_id=10001)

    reply = await handle_private_submission(
        bot, user_id=10001, image_url="https://example.com/p.png"
    )
    assert "验证通过" in reply
    record = get_session_store().get("123", "10001")
    assert record is not None and record.status == "approved"


@pytest.mark.asyncio
async def test_private_submission_no_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """私聊验证：无待验证会话时提示。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        handle_private_submission,
    )

    monkeypatch.setattr(
        flow_module,
        "recognize_image_url_multi",
        lambda _u, *, _models=None: {
            _m: _ocr_result_with_evidence() for _m in (_models or ["PaddleOCR-VL-1.6"])
        },
    )
    bot: Any = FakeBot()
    reply = await handle_private_submission(
        bot, user_id=10001, image_url="https://example.com/p.png"
    )
    assert "没有待验证" in reply


@pytest.mark.asyncio
async def test_private_submission_multi_group_asks_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """私聊验证：多群待验证且未选群时返回群列表供选择。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        handle_private_submission,
    )

    monkeypatch.setattr(
        flow_module,
        "recognize_image_url_multi",
        lambda _u, *, _models=None: {
            _m: _ocr_result_with_evidence() for _m in (_models or ["PaddleOCR-VL-1.6"])
        },
    )
    store = get_session_store()
    store.start(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    store.start(
        group_id="456",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )

    reply = await handle_private_submission(
        FakeBot(),  # type: ignore[arg-type]
        user_id=10001,
        image_url="https://example.com/p.png",
    )
    assert "2 个群等待验证" in reply
    assert "123" in reply and "456" in reply


@pytest.mark.asyncio
async def test_private_submission_multi_group_with_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """私聊验证：多群待验证且已选目标群时验证选定群。"""
    from src.plugins.nonebot_plugin_ocr_fanqie_novel.services.verification import (
        handle_private_submission,
    )

    async def fake_recognize(url: str, *, models: list[str] | None = None) -> Any:
        _ = (url, models)
        return {
            _m: _ocr_result_with_evidence() for _m in (models or ["PaddleOCR-VL-1.6"])
        }

    monkeypatch.setattr(flow_module, "recognize_image_url_multi", fake_recognize)
    store = get_session_store()
    store.start(
        group_id="123",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    store.start(
        group_id="456",
        user_id="10001",
        bot_id="bot1",
        platform_id="qq",
        adapter_id="~onebot.v11",
        protocol_id="default",
    )
    store.set_private_target("10001", "456")

    reply = await handle_private_submission(
        FakeBot(),  # type: ignore[arg-type]
        user_id=10001,
        image_url="https://example.com/p.png",
    )
    assert "验证通过" in reply
    assert store.get("123", "10001").status == "waiting"  # type: ignore[union-attr]
    assert store.get("456", "10001").status == "approved"  # type: ignore[union-attr]


def _box(y: int) -> list[list[int]]:
    return [[0, y], [100, y], [100, y + 20], [0, y + 20]]
