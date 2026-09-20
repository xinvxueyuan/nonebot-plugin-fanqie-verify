"""FR4 综合判断逻辑测试（书评详情页）。"""

from __future__ import annotations

from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
    ExtractedField,
    ReadingEvidence,
    judge_evidence,
)


def _evidence(
    *,
    is_self: bool = True,
    book_name: str = "综漫：吉他雇佣兵无法找到归宿？",
    author: str = "阿百川大鬼",
    rating: str | None = None,
) -> ReadingEvidence:
    return ReadingEvidence(
        is_self_review=is_self,
        book_name=ExtractedField(book_name, "b", 1.0) if book_name else None,
        author=ExtractedField(author, "a", 1.0) if author else None,
        rating=ExtractedField(rating, "r", 1.0) if rating else None,
    )


def test_judge_pass() -> None:
    """本人书评 + 书名 + 作者应通过。"""
    verdict = judge_evidence(_evidence())
    assert verdict.passed is True
    assert verdict.reason is None
    assert verdict.book_name == "综漫：吉他雇佣兵无法找到归宿？"
    assert verdict.author == "阿百川大鬼"


def test_judge_not_self() -> None:
    """非本人书评应拒绝。"""
    verdict = judge_evidence(_evidence(is_self=False))
    assert verdict.passed is False
    assert verdict.reason == "书评不是本人发布"


def test_judge_missing_book_name() -> None:
    """缺少书名应拒绝。"""
    verdict = judge_evidence(_evidence(book_name=""))
    assert verdict.passed is False
    assert verdict.reason == "缺少书名"


def test_judge_empty_book_name() -> None:
    """空白书名应拒绝。"""
    verdict = judge_evidence(_evidence(book_name="   "))
    assert verdict.passed is False
    assert verdict.reason == "书名为空"


def test_judge_book_name_too_long() -> None:
    """超长书名应拒绝。"""
    verdict = judge_evidence(_evidence(book_name="长" * 101))
    assert verdict.passed is False
    assert verdict.reason == "书名过长"


def test_judge_pure_number_book() -> None:
    """纯数字书名应拒绝。"""
    verdict = judge_evidence(_evidence(book_name="12345"))
    assert verdict.passed is False
    assert verdict.reason == "书名为纯数字或乱码"


def test_judge_missing_author() -> None:
    """缺少作者应拒绝。"""
    verdict = judge_evidence(_evidence(author=""))
    assert verdict.passed is False
    assert verdict.reason == "缺少作者"


def test_judge_invalid_rating() -> None:
    """不合理评分（如 6 星）应拒绝。"""
    verdict = judge_evidence(_evidence(rating="★★★★★★"))
    assert verdict.passed is False
    assert verdict.reason == "评分不合理"


def test_judge_valid_rating() -> None:
    """合理评分应通过。"""
    verdict = judge_evidence(_evidence(rating="★★"))
    assert verdict.passed is True
    assert verdict.rating == "★★"


def test_judge_missing_book_and_author() -> None:
    """本人徽章 + 无书名作者时，仍缺核心字段应拒绝。"""
    verdict = judge_evidence(_evidence(book_name="", author=""))
    assert verdict.passed is False
