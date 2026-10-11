"""OCR 失败视觉模型兜底判定测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.verification import vision


class TestParseContent:
    def test_plain_json(self) -> None:
        assert vision._parse_content('{"passed": true}') == {"passed": True}

    def test_json_with_markdown_fence(self) -> None:
        content = '```json\n{"passed": false, "reason": "x"}\n```'
        assert vision._parse_content(content) == {"passed": False, "reason": "x"}

    def test_json_with_surrounding_text(self) -> None:
        content = '结果是：{"passed": true} 谢谢'
        assert vision._parse_content(content) == {"passed": True}

    def test_invalid_returns_none(self) -> None:
        assert vision._parse_content("不是 JSON") is None


class TestVerdictFromDict:
    def test_full_fields(self) -> None:
        data = {
            "passed": True,
            "reason": None,
            "is_self_review": True,
            "reader_name": "我",
            "book_name": "综漫：吉他雇佣兵无法找到归宿？",
            "author": "阿百川大鬼",
            "rating": "★★★",
        }
        verdict = vision._verdict_from_dict(data, raw=json.dumps(data))
        assert verdict.passed is True
        assert verdict.reason is None
        assert verdict.is_self_review is True
        assert verdict.reader_name == "我"
        assert verdict.book_name == "综漫：吉他雇佣兵无法找到归宿？"
        assert verdict.author == "阿百川大鬼"
        assert verdict.rating == "★★★"
        assert verdict.raw
        assert verdict.prompt == ""
        assert verdict.model == ""

    def test_minimal_fields(self) -> None:
        verdict = vision._verdict_from_dict({"passed": False}, raw="{}")
        assert verdict.passed is False
        assert verdict.reason is None
        assert verdict.is_self_review is False
        assert verdict.book_name is None
        assert verdict.author is None
        assert verdict.rating is None

    def test_has_flags(self) -> None:
        verdict = vision._verdict_from_dict(
            {
                "passed": True,
                "has_review_detail_title": True,
                "has_rating_stars": False,
            },
            raw="{}",
        )
        assert verdict.has_review_detail_title is True
        assert verdict.has_rating_stars is False


class TestVerdictToEvidence:
    def test_converts_fields(self) -> None:
        verdict = vision.VisionVerdict(
            passed=True,
            reason=None,
            is_self_review=True,
            reader_name="我",
            book_name="书名",
            author="作者",
            rating="★★",
        )
        evidence = vision.verdict_to_evidence(verdict)
        assert evidence.is_self_review is True
        assert evidence.book_name is not None
        assert evidence.book_name.value == "书名"
        assert evidence.author is not None
        assert evidence.author.value == "作者"
        assert evidence.reader_name is not None
        assert evidence.reader_name.value == "我"

    def test_empty_fields(self) -> None:
        verdict = vision.VisionVerdict(passed=False, reason="x")
        evidence = vision.verdict_to_evidence(verdict)
        assert evidence.is_self_review is False
        assert evidence.book_name is None
        assert evidence.author is None


class TestBuildPrompt:
    def test_prompt_contains_whitelist(self) -> None:
        with patch.object(vision.policy, "get_policy") as get_policy:
            get_policy.return_value.group_policy.return_value = SimpleNamespace(
                is_configured=True,
                author_names=frozenset({"阿百川大鬼", "某作者"}),
            )
            prompt = vision._build_prompt(123)
        assert "阿百川大鬼" in prompt
        assert "某作者" in prompt

    def test_prompt_no_whitelist(self) -> None:
        with patch.object(vision.policy, "get_policy") as get_policy:
            get_policy.return_value.group_policy.return_value = SimpleNamespace(
                is_configured=False,
                author_names=frozenset(),
            )
            prompt = vision._build_prompt(123)
        assert "未配置作者白名单" in prompt

    def test_prompt_requires_detail_title_and_stars(self) -> None:
        with patch.object(vision.policy, "get_policy") as get_policy:
            get_policy.return_value.group_policy.return_value = SimpleNamespace(
                is_configured=False,
                author_names=frozenset(),
            )
            prompt = vision._build_prompt(123)
        assert "书评详情" in prompt
        assert "评分组件" in prompt


class TestVisionConfig:
    def test_disabled_returns_none(self) -> None:
        with patch.object(vision.plugin_config, "fanqie_vision_enabled", new=False):
            assert vision._vision_config() is None

    def test_no_key_returns_none(self) -> None:
        with (
            patch.object(vision.plugin_config, "fanqie_vision_enabled", new=True),
            patch.object(vision.plugin_config, "fanqie_vision_api_key", ""),
        ):
            assert vision._vision_config() is None

    def test_configured(self) -> None:
        with (
            patch.object(vision.plugin_config, "fanqie_vision_enabled", new=True),
            patch.object(vision.plugin_config, "fanqie_vision_api_key", "sk-test"),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_api_base",
                "https://api.deepseek.com",
            ),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_model",
                "deepseek-v4-flash-vision-exp",
            ),
        ):
            cfg = vision._vision_config()
        assert cfg == (
            "https://api.deepseek.com",
            "sk-test",
            "deepseek-v4-flash-vision-exp",
        )


class TestVisionFallback:
    def _mock_completion(self, content: str) -> SimpleNamespace:
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self) -> None:
        with patch.object(vision.plugin_config, "fanqie_vision_enabled", new=False):
            assert await vision.vision_fallback("http://x/img.jpg", 123) is None

    @pytest.mark.asyncio
    async def test_pass_verdict(self) -> None:
        content = json.dumps({
            "passed": True,
            "reason": None,
            "is_self_review": True,
            "book_name": "书名",
            "author": "作者",
        })
        completion = self._mock_completion(content)
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=completion)

        with (
            patch.object(vision.plugin_config, "fanqie_vision_enabled", new=True),
            patch.object(vision.plugin_config, "fanqie_vision_api_key", "sk-test"),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_api_base",
                "https://api.deepseek.com",
            ),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_model",
                "deepseek-v4-flash-vision-exp",
            ),
            patch(
                "src.plugins.nonebot_plugin_fanqie_verify.services.verification.vision.AsyncOpenAI",
                return_value=client,
            ),
        ):
            verdict = await vision.vision_fallback("http://x/img.jpg", 123)

        assert verdict is not None
        assert verdict.passed is True
        assert verdict.book_name == "书名"
        assert verdict.author == "作者"
        assert verdict.model == "deepseek-v4-flash-vision-exp"
        assert "书评详情" in verdict.prompt

    @pytest.mark.asyncio
    async def test_openai_error_returns_none(self) -> None:
        with (
            patch.object(vision.plugin_config, "fanqie_vision_enabled", new=True),
            patch.object(vision.plugin_config, "fanqie_vision_api_key", "sk-test"),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_api_base",
                "https://api.deepseek.com",
            ),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_model",
                "deepseek-v4-flash-vision-exp",
            ),
            patch(
                "src.plugins.nonebot_plugin_fanqie_verify.services.verification.vision.AsyncOpenAI",
                side_effect=vision.OpenAIError("boom"),
            ),
        ):
            assert await vision.vision_fallback("http://x/img.jpg", 123) is None

    @pytest.mark.asyncio
    async def test_unparseable_content_returns_none(self) -> None:
        completion = self._mock_completion("完全不是 JSON")
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=completion)

        with (
            patch.object(vision.plugin_config, "fanqie_vision_enabled", new=True),
            patch.object(vision.plugin_config, "fanqie_vision_api_key", "sk-test"),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_api_base",
                "https://api.deepseek.com",
            ),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_model",
                "deepseek-v4-flash-vision-exp",
            ),
            patch(
                "src.plugins.nonebot_plugin_fanqie_verify.services.verification.vision.AsyncOpenAI",
                return_value=client,
            ),
        ):
            assert await vision.vision_fallback("http://x/img.jpg", 123) is None

    @pytest.mark.asyncio
    async def test_client_timeout_uses_configured_value(self) -> None:
        """AsyncOpenAI 必须收到 timeout=<fanqie_vision_timeout>。"""
        content = json.dumps({"passed": True, "reason": None})
        completion = self._mock_completion(content)
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=completion)
        openai_cls = MagicMock(return_value=client)

        with (
            patch.object(vision.plugin_config, "fanqie_vision_enabled", new=True),
            patch.object(vision.plugin_config, "fanqie_vision_api_key", "sk-test"),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_api_base",
                "https://api.deepseek.com",
            ),
            patch.object(
                vision.plugin_config,
                "fanqie_vision_model",
                "deepseek-v4-flash-vision-exp",
            ),
            patch.object(vision.plugin_config, "fanqie_vision_timeout", 60.0),
            patch(
                "src.plugins.nonebot_plugin_fanqie_verify.services.verification.vision.AsyncOpenAI",
                openai_cls,
            ),
        ):
            await vision.vision_fallback("http://x/img.jpg", 123)

        openai_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.deepseek.com",
            timeout=60.0,
        )


# ---------------------------------------------------------------------------
# 提示词必须保留「发布者不能是作者」这条（生产走仅视觉，丢了就没人拦）
# ---------------------------------------------------------------------------


def test_prompt_carries_reviewer_not_author_rule() -> None:
    """提示词里必须有第 5 条：书评发布者不能是白名单作者。"""
    prompt = vision._build_prompt(1094538078)

    assert "发布者" in prompt
    assert "不能是上述白名单作者" in prompt
    assert "5." in prompt


# ---------------------------------------------------------------------------
# 402 账户余额不足：判定函数（鸭子类型）+ 从 vision_fallback 抛出专属异常
# ---------------------------------------------------------------------------


def _make_error_with(
    status_code: int | None, body: object | None
) -> vision.OpenAIError:
    """构造带 status_code/body 属性的 OpenAIError（判定函数是鸭子类型）。"""
    exc = vision.OpenAIError("test")
    if status_code is not None:
        exc.status_code = status_code  # type: ignore[attr-defined]
    if body is not None:
        exc.body = body  # type: ignore[attr-defined]
    return exc


def test_is_insufficient_balance_variants() -> None:
    assert vision._is_insufficient_balance(_make_error_with(402, None)) is True
    assert (
        vision._is_insufficient_balance(
            _make_error_with(None, {"error": {"message": "Insufficient Balance"}})
        )
        is True
    )
    assert vision._is_insufficient_balance(_make_error_with(500, None)) is False
    assert vision._is_insufficient_balance(vision.OpenAIError("plain")) is False


@pytest.mark.asyncio
async def test_insufficient_balance_raises_from_fallback() -> None:
    """402 → 抛 VisionInsufficientBalanceError（真实异常类构造，探针已验）。"""
    import httpx2
    from openai import APIStatusError

    req = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    resp = httpx2.Response(402, request=req)
    err = APIStatusError(
        "Insufficient Balance",
        response=resp,
        body={"error": {"message": "Insufficient Balance"}},
    )
    with (
        patch.object(vision.plugin_config, "fanqie_vision_enabled", new=True),
        patch.object(vision.plugin_config, "fanqie_vision_api_key", "sk-test"),
        patch.object(
            vision.plugin_config, "fanqie_vision_api_base", "https://api.deepseek.com"
        ),
        patch.object(
            vision.plugin_config, "fanqie_vision_model", "deepseek-v4-flash-vision-exp"
        ),
        patch(
            "src.plugins.nonebot_plugin_fanqie_verify.services.verification.vision.AsyncOpenAI",
            side_effect=err,
        ),
        pytest.raises(vision.VisionInsufficientBalanceError),
    ):
        await vision.vision_fallback("http://x/img.jpg", 123)
