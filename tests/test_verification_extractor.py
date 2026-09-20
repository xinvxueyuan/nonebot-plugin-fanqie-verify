"""FR3 信息提取（书评详情页）测试。

基于 vivo 办公套件中的 4 张书评详情页截图布局构造 OCR 结果，验证
「我」徽章检测、读者名/发布日期/评分/书名/作者提取，以及正面例与
反例的判定。

"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.ocr import (
    OCRPage,
    OCRResult,
    OCRTextLine,
)
from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
    ReadingEvidence,
    extract_reading_evidence,
)


def _line(text: str, y: int, confidence: float = 1.0) -> OCRTextLine:
    """构造一行位于 ``y`` 处、宽 200 的 OCR 文本。"""
    return OCRTextLine(
        text=text,
        confidence=confidence,
        box=[[0, y], [200, y], [200, y + 30], [0, y + 30]],
    )


def _result(lines: list[OCRTextLine]) -> OCRResult:
    """构造仅含一页的规范化 OCR 结果。"""
    return OCRResult(job_id="job-extract", pages=[OCRPage(lines=lines)])


def _self_review_layout() -> list[OCRTextLine]:
    """正面例：自己发布的书评（Screenshot_20260813_115925）。"""
    return [
        _line("书评详情", 198),
        _line("新v学员", 403),
        _line("我", 412),
        _line("刚刚", 495),
        _line("阅读不足30分钟后点评", 653),
        _line("综漫：吉他雇佣兵无法找到归宿？", 1000),
        _line("阿百川大鬼", 1089),
    ]


def _other_review_layout() -> list[OCRTextLine]:
    """反例：别人发布的书评，无「我」徽章（Screenshot_20260813_115840）。"""
    return [
        _line("书评详情", 198),
        _line("叶月辉夜", 399),
        _line("+关注", 452),
        _line("05-28", 498),
        _line("阅读11小时后点评", 650),
        _line("综漫：吉他雇佣兵无法找到归宿？", 1092),
        _line("阿百川大鬼", 1181),
    ]


def _rated_review_layout() -> list[OCRTextLine]:
    """带评分的书评（Screenshot_20260813_115816，非本人）。"""
    return [
        _line("书评详情", 198),
        _line("老兔子精", 409),
        _line("超级粉丝", 403),
        _line("+关注", 446),
        _line("08-05", 518),
        _line("★★阅读不足30分钟后点评", 653),
        _line("崩铁：斯人已逝，何必介怀？", 1092),
        _line("时空下的尽余欢", 1181),
    ]


def test_self_review_extraction() -> None:
    """正面例：应检测到「我」徽章并提取书名/作者/读者名。"""
    evidence = extract_reading_evidence(_result(_self_review_layout()))

    assert evidence.is_self_review is True
    assert evidence.reader_name is not None
    assert evidence.reader_name.value == "新v学员"
    assert evidence.book_name is not None
    assert evidence.book_name.value == "综漫：吉他雇佣兵无法找到归宿？"
    assert evidence.author is not None
    assert evidence.author.value == "阿百川大鬼"
    assert evidence.publish_time is not None
    assert evidence.publish_time.value == "刚刚"
    assert evidence.publish_days_ago == 0
    assert evidence.is_sufficient is True


def test_other_review_not_self() -> None:
    """反例：他人书评（无「我」徽章）应判定为不充分。"""
    evidence = extract_reading_evidence(_result(_other_review_layout()))

    assert evidence.is_self_review is False
    assert evidence.reader_name is not None
    assert evidence.reader_name.value == "叶月辉夜"
    assert evidence.book_name is not None
    assert evidence.book_name.value == "综漫：吉他雇佣兵无法找到归宿？"
    assert evidence.author is not None
    assert evidence.author.value == "阿百川大鬼"
    assert evidence.is_sufficient is False


def test_rating_extraction() -> None:
    """带评分的书评应提取星数。"""
    evidence = extract_reading_evidence(_result(_rated_review_layout()))

    assert evidence.rating is not None
    assert evidence.rating.value == "★★"
    assert evidence.is_self_review is False


def test_publish_time_relative() -> None:
    """相对发布时间应归一化为天数。"""
    lines = [
        _line("书评详情", 198),
        _line("新v学员", 403),
        _line("我", 412),
        _line("2天前", 495),
        _line("阅读2小时后点评", 653),
        _line("崩铁：斯人已逝，何必介怀？", 1274),
        _line("阿百川大鬼", 1363),
    ]
    evidence = extract_reading_evidence(_result(lines))

    assert evidence.publish_time is not None
    assert evidence.publish_time.value == "2天前"
    assert evidence.publish_days_ago == 2
    assert evidence.is_self_review is True


def test_reader_name_fallback_without_self_marker() -> None:
    """无「我」徽章时，读者名应回退到标题下方的名字行。"""
    evidence = extract_reading_evidence(_result(_other_review_layout()))
    assert evidence.reader_name is not None
    assert evidence.reader_name.value == "叶月辉夜"


def test_empty_result() -> None:
    """空识别结果应返回空证据。"""
    evidence = extract_reading_evidence(OCRResult(job_id="job-empty"))
    assert evidence.is_self_review is False
    assert evidence.book_name is None
    assert evidence.author is None
    assert evidence.is_sufficient is False


def test_evidence_fields_are_frozen() -> None:
    """ReadingEvidence 与 ExtractedField 应为不可变结构。"""
    evidence = ReadingEvidence()

    with pytest.raises(FrozenInstanceError):
        evidence.book_name = None  # type: ignore[misc]


def test_evidence_confidence_sourced() -> None:
    """提取字段应携带来源文本与置信度。"""
    line = _line("综漫：吉他雇佣兵无法找到归宿？", 1000, 0.98)
    evidence = extract_reading_evidence(
        _result([
            _line("书评详情", 198),
            _line("新v学员", 403),
            _line("我", 412),
            _line("刚刚", 495),
            line,
            _line("阿百川大鬼", 1089),
        ])
    )

    assert evidence.book_name is not None
    assert evidence.book_name.source_text == "综漫：吉他雇佣兵无法找到归宿？"
    assert evidence.book_name.confidence == pytest.approx(0.98)


def test_reader_name_excludes_ui_elements() -> None:
    """读者名不应是界面元素（如 +关注 / 书评详情）。"""
    evidence = extract_reading_evidence(
        _result([
            _line("书评详情", 198),
            _line("+关注", 452),
            _line("我", 412),
            _line("刚刚", 495),
            _line("阅读2小时后点评", 653),
            _line("崩铁：斯人已逝，何必介怀？", 1274),
            _line("阿百川大鬼", 1363),
        ])
    )
    # 「我」徽章区域（±120px）内没有读者名候选，应回退或为 None
    assert evidence.is_self_review is True


def _no_colon_layout() -> list[OCRTextLine]:
    """无冒号书名的书评详情页（Image_1787132023465_185，vivo 套件实拍）。"""
    return [
        _line("17:29", 50),
        _line("GG50", 43),
        _line("书评详情", 200),
        _line("Sakana~~", 407),
        _line("我", 403),
        _line("刚刚", 497),
        _line("阅读4小时后点评", 633),
        _line("劲呀，我要看的就是这个", 723),
        _line("乐队少女不能啵经纪人嘴", 957),
        _line("百舸川掮客", 1037),
        _line("添加追评", 1243),
        _line("全部评论", 1450),
        _line("发表评论...", 3013),
    ]


def test_no_colon_book_title_with_whitelist() -> None:
    """无冒号书名 + 白名单双向确认：应提取书名与作者并判定充分。"""
    known_books = frozenset({
        "综漫经纪人先生不死于乐队修罗场",
        "乐队少女神人多，急需棍棒教育",
        "乐队少女不能啵经纪人嘴",
        "综漫：吉他雇佣兵无法找到归宿？",
        "大少女乐队时代的传奇经纪人",
        "乐队少女攻略日志",
        "能别撕剧本了吗？这样显得我很呆",
    })
    evidence = extract_reading_evidence(
        _result(_no_colon_layout()),
        known_books=known_books,
    )

    assert evidence.is_self_review is True
    assert evidence.book_name is not None
    assert evidence.book_name.value == "乐队少女不能啵经纪人嘴"
    assert evidence.author is not None
    assert evidence.author.value == "百舸川掮客"
    assert evidence.is_sufficient is True


def test_no_colon_book_title_without_whitelist() -> None:
    """无白名单时，无冒号书名回退应通过坐标（作者行上方紧邻）提取。"""
    evidence = extract_reading_evidence(_result(_no_colon_layout()))

    assert evidence.is_self_review is True
    assert evidence.book_name is not None
    assert evidence.book_name.value == "乐队少女不能啵经纪人嘴"
    assert evidence.author is not None
    assert evidence.author.value == "百舸川掮客"
    assert evidence.is_sufficient is True


def test_no_colon_book_title_whitelist_miss_is_rejected() -> None:
    """白名单提供但无精确命中：宁缺毋滥，不冒险提取（交由管理员审核）。"""
    known_books = frozenset({"完全无关的其它书名"})
    evidence = extract_reading_evidence(
        _result(_no_colon_layout()),
        known_books=known_books,
    )

    assert evidence.book_name is None
    assert evidence.author is None
    assert evidence.is_sufficient is False


def test_merged_book_author_line_with_whitelist() -> None:
    """书名与作者合并在同一行（``书名作者``）：应基于白名单前缀切分提取。"""
    known_books = frozenset({
        "少女乐队神人多，急需棍棒教育",
        "综漫：吉他雇佣兵无法找到归宿？",
    })
    lines = [
        _line("书评详情", 198),
        _line("癲火回荡", 403),
        _line("我", 412),
        _line("★★★★阅读14小时后点评", 653),
        _line("少女乐队神人多，急需棍棒教育百舸川掮客", 1000),
    ]
    evidence = extract_reading_evidence(_result(lines), known_books=known_books)

    assert evidence.is_self_review is True
    assert evidence.book_name is not None
    assert evidence.book_name.value == "少女乐队神人多，急需棍棒教育"
    assert evidence.author is not None
    assert evidence.author.value == "百舸川掮客"
    assert evidence.is_sufficient is True


def test_review_detail_page_extracted() -> None:
    """应识别到「书评详情」标题，确认确实是书评详情页。"""
    evidence = extract_reading_evidence(_result(_self_review_layout()))

    assert evidence.review_detail_page is not None
    assert evidence.review_detail_page.value == "书评详情"
    assert evidence.review_detail_page.confidence == 1.0


def test_review_detail_page_missing() -> None:
    """无「书评详情」标题行时应判为信息不足。"""
    lines = [
        _line("新v学员", 403),
        _line("我", 412),
        _line("刚刚", 495),
        _line("综漫：吉他雇佣兵无法找到归宿？", 1000),
        _line("阿百川大鬼", 1089),
    ]
    evidence = extract_reading_evidence(_result(lines))

    assert evidence.review_detail_page is None
    assert evidence.is_sufficient is False
