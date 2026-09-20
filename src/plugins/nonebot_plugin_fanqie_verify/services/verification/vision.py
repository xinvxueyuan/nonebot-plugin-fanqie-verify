"""OCR 失败时的视觉模型兜底判定。

当 PaddleOCR 云端识别失败（报错/超时/返回异常）时，改用 OpenAI 兼容
视觉模型（默认 DeepSeek ``deepseek-v4-flash-vision-exp``）直接看图判定。

与 :mod:`.flow` 的识别-提取-判定链路不同，本模块把该群的验证要求
（作者白名单等）直接写入提示词，由视觉模型端到端给出「通过/不通过」
结论，作为 OCR 识别不出时的兜底路径。

"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import TYPE_CHECKING, Any

from nonebot import logger
from openai import AsyncOpenAI, OpenAIError

from ...core.config import plugin_config
from . import policy

if TYPE_CHECKING:
    from .models import ReadingEvidence

_logger = logging.getLogger(__name__)

# 视觉模型判定结果 JSON 中的字段名（英文键，跨模型稳定）。
_VERDICT_PROMPT = """\
你是一个番茄小说「书评详情页」截图验证助手。请仔细查看这张手机截图，\
判断它是否是一张有效的、由用户本人发布的书评详情页截图。

判定要求：
1. 必须检测到「我」徽章（表示这是用户本人发布的书评），否则视为无效。
2. 必须能识别出书名与作者名。
3. 必须是整本书的「书评」（书评详情页），而非「短评」「段评」「章评」「想法」
   等其他形式的评论。请按以下特征严格区分：
   - 书评详情页：有五角星评分组件（1~5 颗星）、顶部「书评详情」标题、
     阅读时长标注（如「阅读X小时后点评」）、书籍信息卡片（封面+书名+作者）。
   - 段评：没有评分组件、没有「书评详情」标题（可能有书籍信息卡片）。
   - 章评：没有评分组件、没有「书评详情」标题，也没有书籍信息卡片，
     只在评论末尾有书籍名称的蓝色链接（话题跳转标记）。
   若判断为短评/段评/章评，一律判为无效。
4. 作者名必须命中以下白名单之一：{author_whitelist}

请只输出一个 JSON 对象（不要输出任何其他文字），字段如下：
{{
  "passed": true 或 false,
  "reason": "未通过时的原因；通过时为 null",
  "is_self_review": true 或 false,
  "has_review_detail_title": true 或 false,
  "has_rating_stars": true 或 false,
  "reader_name": "读者名或 null",
  "book_name": "书名或 null",
  "author": "作者名或 null",
  "rating": "评分星数如 ★★★ 或 null"
}}
"""


@dataclass(frozen=True, slots=True)
class VisionVerdict:
    """视觉模型兜底判定结果。

    Attributes:
        passed: 视觉模型是否判定通过。
        reason: 未通过原因；通过时为 ``None``。
        is_self_review: 是否检测到「我」徽章。
        reader_name: 读者名。
        book_name: 书名。
        author: 作者名。
        rating: 评分星数。
        has_review_detail_title: 是否识别到「书评详情」标题。
        has_rating_stars: 是否识别到五角星评分组件。
        raw: 视觉模型原始 JSON 字符串（审计用）。
        prompt: 发送给视觉模型的完整提示词（审计用）。
        model: 使用的视觉模型名（审计用）。

    """

    passed: bool
    reason: str | None
    is_self_review: bool = False
    reader_name: str | None = None
    book_name: str | None = None
    author: str | None = None
    rating: str | None = None
    has_review_detail_title: bool = False
    has_rating_stars: bool = False
    raw: str = ""
    prompt: str = ""
    model: str = ""


def _author_whitelist_text(group_id: int) -> str:
    """把该群作者白名单渲染为提示词文本。"""
    group = policy.get_policy().group_policy(group_id)
    if group is None or not group.is_configured:
        return "（该群未配置作者白名单，任意作者均可）"
    names = sorted(group.author_names)
    return "、".join(f"「{name}」" for name in names)


def _build_prompt(group_id: int) -> str:
    return _VERDICT_PROMPT.format(author_whitelist=_author_whitelist_text(group_id))


def _parse_content(content: str) -> dict[str, Any] | None:
    """从模型返回文本中提取 JSON 对象（容错 markdown 代码块/多余文字）。"""
    text = content.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        text = (
            text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        )
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    # 提取首个 { 到末个 } 之间的子串再试
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _verdict_from_dict(
    data: dict[str, Any],
    raw: str,
    *,
    prompt: str = "",
    model: str = "",
) -> VisionVerdict:
    passed = bool(data.get("passed"))
    return VisionVerdict(
        passed=passed,
        reason=_optional_str(data, "reason"),
        is_self_review=bool(data.get("is_self_review")),
        has_review_detail_title=bool(data.get("has_review_detail_title")),
        has_rating_stars=bool(data.get("has_rating_stars")),
        reader_name=_optional_str(data, "reader_name"),
        book_name=_optional_str(data, "book_name"),
        author=_optional_str(data, "author"),
        rating=_optional_str(data, "rating"),
        raw=raw,
        prompt=prompt,
        model=model,
    )


def _verdict_to_evidence(verdict: VisionVerdict) -> ReadingEvidence:
    """把视觉判定结果构造成 :class:`ReadingEvidence`，供拒绝路径展示/审计。"""
    from .models import ExtractedField, ReadingEvidence

    def _field(value: str | None) -> ExtractedField | None:
        if value is None:
            return None
        return ExtractedField(value=value, source_text=value, confidence=1.0)

    return ReadingEvidence(
        is_self_review=verdict.is_self_review,
        reader_name=_field(verdict.reader_name),
        book_name=_field(verdict.book_name),
        author=_field(verdict.author),
        rating=_field(verdict.rating),
    )


# 供 flow 侧把视觉判定结果转成 ReadingEvidence 的公开入口。
verdict_to_evidence = _verdict_to_evidence


async def vision_fallback(image_url: str, group_id: int) -> VisionVerdict | None:
    """用视觉模型对截图做兜底判定。

    未启用、未配置 key 或调用失败时返回 ``None``（由调用方回退到原有
    失败处理）。调用成功时返回 :class:`VisionVerdict`。

    Args:
        image_url: 截图的可公开访问 URL。
        group_id: 群号（用于读取该群作者白名单）。

    Returns:
        视觉判定结果；不可用时为 ``None``。

    """
    cfg = _vision_config()
    if cfg is None:
        return None
    base, key, model = cfg
    prompt = _build_prompt(group_id)

    try:
        client = AsyncOpenAI(api_key=key, base_url=base)
        completion = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
        )
    except OpenAIError as exc:
        logger.warning("视觉兜底调用异常: %s", exc)
        return None

    content = _completion_content(completion)
    if content is None:
        return None
    parsed = _parse_content(content)
    if parsed is None:
        logger.warning("视觉兜底响应无法解析为 JSON: %s", content[:200])
        return None
    return _verdict_from_dict(parsed, raw=content, prompt=prompt, model=model)


def _vision_config() -> tuple[str, str, str] | None:
    """校验并返回视觉兜底配置 (base, key, model)；不可用返回 None。"""
    if not plugin_config.fanqie_vision_enabled:
        return None
    key = plugin_config.fanqie_vision_api_key
    if not key:
        logger.warning("视觉兜底未配置 API key，跳过")
        return None
    base = plugin_config.fanqie_vision_api_base or "https://api.deepseek.com"
    model = plugin_config.fanqie_vision_model or "deepseek-v4-flash-vision-exp"
    return base, key, model


def _completion_content(completion: Any) -> str | None:
    """从 openai 响应对象中取出 assistant 文本内容；异常时返回 None。"""
    try:
        content = completion.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        logger.warning("视觉兜底响应缺少 content: %s", exc)
        return None
    if not isinstance(content, str) or not content.strip():
        logger.warning("视觉兜底响应 content 为空")
        return None
    return content


__all__ = ["VisionVerdict", "verdict_to_evidence", "vision_fallback"]
