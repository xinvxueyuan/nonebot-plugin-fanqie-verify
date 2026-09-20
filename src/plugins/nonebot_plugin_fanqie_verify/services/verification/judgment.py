"""FR4 综合判断：依据书评详情页提取结果决定是否放行。

判断规则：
- 必须检测到「我」徽章（书评为自己发布）。
- 书名不为空、长度在 1~``book_name_max_len``、非纯数字/乱码。
- 作者名有效（非空、非纯数字）。
- 若提取到评分，评分应为合理的 1~5 星。

三项核心（我徽章 + 书名 + 作者）均符合则通过；否则拒绝。信息不足
（FR3 未通过）由调用方在进入判断前处理，本模块假定证据已经过
``is_sufficient`` 检查。

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.config import plugin_config

if TYPE_CHECKING:
    from .models import ReadingEvidence

# 评分星数上限。
_RATING_MAX_STARS = 5


def _is_plain_number(value: str) -> bool:
    """书名是否为纯数字（视为无效书名）。"""
    return value.isdigit()


def _is_junk(value: str) -> bool:
    """书名是否含中文或字母字符；完全没有则视为乱码。"""
    for char in value:
        if "\u4e00" <= char <= "\u9fff":
            return False
        if char.isalpha():
            return False
    return True


@dataclass(frozen=True, slots=True)
class Judgment:
    """综合判断结果。

    Attributes:
        passed: 是否通过验证。
        reason: 未通过原因；通过时为 ``None``。
        book_name: 判断使用的书名文本。
        author: 判断使用的作者名。
        rating: 判断使用的评分星数。

    """

    passed: bool
    reason: str | None = None
    book_name: str | None = None
    author: str | None = None
    rating: str | None = None


def judge_evidence(evidence: ReadingEvidence) -> Judgment:
    """对提取结果执行 FR4 综合判断。

    Args:
        evidence: FR3 提取的阅读证据。

    Returns:
        判断结果。

    """
    if not evidence.is_self_review:
        return Judgment(passed=False, reason="书评不是本人发布")

    book_name = evidence.book_name.value if evidence.book_name is not None else None
    author = evidence.author.value if evidence.author is not None else None
    rating = evidence.rating.value if evidence.rating is not None else None

    if book_name is None:
        return Judgment(passed=False, reason="缺少书名")

    stripped = book_name.strip()
    reason: str | None = None
    if not stripped:
        reason = "书名为空"
    elif len(stripped) > plugin_config.fanqie_book_name_max_len:
        reason = "书名过长"
    elif _is_plain_number(stripped) or _is_junk(stripped):
        reason = "书名为纯数字或乱码"

    if reason is None and author is None:
        reason = "缺少作者"

    if reason is None and rating is not None:
        stars = len(rating)
        if not 1 <= stars <= _RATING_MAX_STARS:
            reason = "评分不合理"

    return Judgment(
        passed=reason is None,
        reason=reason,
        book_name=book_name,
        author=author,
        rating=rating,
    )
