"""FR3 信息提取：将 OCR 识别结果解析为结构化阅读证据。

目标页面为番茄小说「书评详情」页。新成员需发送自己发布的书评截图，
关键标识为「我」徽章；据此提取读者名、发布日期、评分、阅读时长、
书评正文、书名与作者。

提取基于文本模式与相对位置，不依赖绝对像素坐标（页面可滚动导致 y
偏移）。核心判定：是否存在 ``text.strip() == "我"`` 的徽章行。

"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .models import ExtractedField, ReadingEvidence

if TYPE_CHECKING:
    from ..ocr.models import OCRResult, OCRTextLine

_POSITION_SENTINEL = 1 << 30

# 书评详情页标题行。
_PAGE_TITLE = "书评详情"

# 自己发布书评的「我」徽章。
_SELF_MARKER = "我"

# 身份标签（读者名后可能出现）。
_IDENTITY_MARKERS = ("超级粉丝", "+关注")

# 读者名候选的最大长度。
_READER_MAX_LEN = 16

# 与「我」徽章同属读者名区域的垂直距离上限。
_READER_SLACK = 120

# 时间标签的最大长度。
_TIME_MAX_LEN = 12

# 书名最大长度。
_BOOK_MAX_LEN = 60

# 回退匹配（无冒号书名）时，书名行与作者行的最大垂直间距。
_BOOK_AUTHOR_MAX_GAP = 120

# 回退匹配（无冒号书名）时，书名候选的最短长度。
_MIN_BOOK_LEN = 2

# 作者名候选的最大长度。
_AUTHOR_MAX_LEN = 16

# 评分星数上限。
_RATING_MAX_STARS = 5

# 评分星号前缀正则（支持全角 ★ / ☆ 与 ASCII *）。
_RATING_RE = re.compile(r"^([★☆*]+)")

# 发布日期：相对时间（刚刚/X 天前/X 小时前）或 MM-DD。
_RELATIVE_TIME_RE = re.compile(r"(刚刚|\d+\s*天前|\d+\s*小时前)")
_DATE_MMDD_RE = re.compile(r"(\d{1,2})-(\d{1,2})")

# 阅读时长描述。
_READ_DURATION_RE = re.compile(r"阅读.{0,12}?(?:小时|分钟|天).{0,6}点评")

# 书名标题行（含全角冒号分隔主副标题）。
_BOOK_TITLE_RE = re.compile(r"^[^：\n]{1,30}：[^：\n]{1,40}$")


def extract_reading_evidence(
    result: OCRResult,
    *,
    known_books: frozenset[str] | None = None,
) -> ReadingEvidence:
    """从书评详情页 OCR 识别结果中提取阅读证据。

    Args:
        result: OCR 服务层返回的规范化识别结果。
        known_books: 群策略中配置的全部白名单书名（可选）。提供时，
            无冒号书名的回退匹配会优先采用与白名单精确一致的行。

    Returns:
        包含读者名、发布日期、评分、书名、作者等字段的阅读证据。

    """
    lines = [line for page in result.pages for line in page.lines]
    if not lines:
        return ReadingEvidence()

    self_marker_line = _find_self_marker(lines)
    page_title_field = _extract_review_detail_page(lines)
    reader_field = _extract_reader_name(lines, self_marker_line)
    publish_field, publish_days = _extract_publish_time(lines, self_marker_line)
    rating_field = _extract_rating(lines)
    duration_field = _extract_read_duration(lines)
    # 该书名字段为空时,尝试从合并行(书名+作者同行)切分提取。
    book_field = _extract_book_title(lines, known_books=known_books)
    author_field = _extract_author(lines, book_field)
    if book_field is None or author_field is None:
        split = _split_merged_book_author(lines, known_books=known_books)
        if split is not None:
            merged_book, merged_author = split
            if book_field is None:
                book_field = merged_book
            if author_field is None or author_field.value != merged_author.value:
                author_field = merged_author
    review_field = _extract_review_text(lines, book_field)

    return ReadingEvidence(
        is_self_review=self_marker_line is not None,
        review_detail_page=page_title_field,
        reader_name=reader_field,
        publish_time=publish_field,
        publish_days_ago=publish_days,
        rating=rating_field,
        read_duration=duration_field,
        book_name=book_field,
        author=author_field,
        review_text=review_field,
    )


def _find_self_marker(lines: list[OCRTextLine]) -> OCRTextLine | None:
    """查找「我」徽章行（``text.strip() == "我"``）。"""
    for line in lines:
        if line.text.strip() == _SELF_MARKER:
            return line
    return None


def _extract_review_detail_page(lines: list[OCRTextLine]) -> ExtractedField | None:
    """提取「书评详情」标题行，确认截图确实是书评详情页。"""
    for line in lines:
        if line.text.strip() == _PAGE_TITLE:
            return ExtractedField(
                value=_PAGE_TITLE,
                source_text=line.text,
                confidence=line.confidence,
            )
    return None


def _extract_reader_name(
    lines: list[OCRTextLine],
    self_marker: OCRTextLine | None,
) -> ExtractedField | None:
    """提取书评读者名。

    「我」徽章存在时，取与之 y 相近且位于其上方的读者名行；否则取
    页面标题（``书评详情``）下方、``+关注`` 上方、且非界面元素的候选。

    """
    if self_marker is not None:
        marker_y = _y_center(self_marker)
        if marker_y is None:
            return None
        candidates = [
            line
            for line in lines
            if _y_center(line) is not None
            and abs((_y_center(line) or 0.0) - marker_y) < _READER_SLACK
            and line.text.strip()
            and line.text.strip() != _SELF_MARKER
            and _is_reader_candidate(line.text)
        ]
        if candidates:
            best = min(
                candidates,
                key=lambda line: abs((_y_center(line) or 0.0) - marker_y),
            )
            return ExtractedField(
                value=best.text.strip(),
                source_text=best.text,
                confidence=best.confidence,
            )

    title_y = _find_page_title_y(lines)
    for line in lines:
        if not _is_reader_candidate(line.text):
            continue
        y = _y_center(line)
        if y is None or title_y is None:
            continue
        if title_y < y < _POSITION_SENTINEL:
            return ExtractedField(
                value=line.text.strip(),
                source_text=line.text,
                confidence=line.confidence,
            )
    return None


def _is_reader_candidate(text: str) -> bool:
    """是否为读者名候选（短文本，非界面元素/时间/书籍标题）。"""
    stripped = text.strip()
    if not stripped or len(stripped) > _READER_MAX_LEN:
        return False
    if stripped in _IDENTITY_MARKERS:
        return False
    if stripped in (_PAGE_TITLE, _SELF_MARKER):
        return False
    if _RELATIVE_TIME_RE.search(stripped) or _DATE_MMDD_RE.search(stripped):
        return False
    if _RATING_RE.match(stripped) or _READ_DURATION_RE.search(stripped):
        return False
    return not _BOOK_TITLE_RE.match(stripped)


def _extract_publish_time(
    lines: list[OCRTextLine],
    self_marker: OCRTextLine | None,
) -> tuple[ExtractedField | None, int | None]:
    """提取书评发布日期。

    优先取「我」徽章行下方最近的时间项（相对时间或 MM-DD），否则取
    全局第一个时间项。

    """
    candidates: list[tuple[ExtractedField, OCRTextLine, int | None]] = []
    for line in lines:
        field, days = _match_publish_time(line)
        if field is None:
            continue
        candidates.append((field, line, days))
    if not candidates:
        return None, None

    if self_marker is not None:
        marker_y = _y_center(self_marker)
        below = [
            item
            for item in candidates
            if _y_center(item[1]) is not None
            and marker_y is not None
            and (_y_center(item[1]) or 0.0) >= marker_y
        ]
        if below:
            best = min(below, key=lambda item: _y_center(item[1]) or _POSITION_SENTINEL)
            return best[0], best[2]

    best = min(candidates, key=lambda item: _y_center(item[1]) or _POSITION_SENTINEL)
    return best[0], best[2]


def _match_publish_time(
    line: OCRTextLine,
) -> tuple[ExtractedField | None, int | None]:
    """从一行文本识别发布日期，返回字段与归一化天数。"""
    text = line.text.strip()
    if not text or len(text) > _TIME_MAX_LEN:
        return None, None

    if text == "刚刚":
        return (
            ExtractedField(
                value="刚刚",
                source_text=line.text,
                confidence=line.confidence,
            ),
            0,
        )
    if match := _RELATIVE_TIME_RE.search(text):
        label = match.group(0).replace(" ", "")
        days = None
        if "天前" in label:
            days = int(re.search(r"\d+", label).group(0))  # type: ignore[union-attr]
        elif "小时前" in label:
            days = 0
        return (
            ExtractedField(
                value=label,
                source_text=line.text,
                confidence=line.confidence,
            ),
            days,
        )
    if match := _DATE_MMDD_RE.search(text):
        return (
            ExtractedField(
                value=match.group(0),
                source_text=line.text,
                confidence=line.confidence,
            ),
            None,
        )
    return None, None


def _extract_rating(lines: list[OCRTextLine]) -> ExtractedField | None:
    """提取评分（行首星号，如 ``★★``）。"""
    for line in lines:
        text = line.text.strip()
        match = _RATING_RE.match(text)
        if match is not None and 1 <= len(match.group(1)) <= _RATING_MAX_STARS:
            return ExtractedField(
                value=match.group(1),
                source_text=line.text,
                confidence=line.confidence,
            )
    return None


def _extract_read_duration(lines: list[OCRTextLine]) -> ExtractedField | None:
    """提取阅读时长描述（如 ``阅读2小时后点评``）。"""
    for line in lines:
        match = _READ_DURATION_RE.search(line.text)
        if match is not None:
            return ExtractedField(
                value=match.group(0),
                source_text=line.text,
                confidence=line.confidence,
            )
    return None


def _extract_book_title(
    lines: list[OCRTextLine],
    *,
    known_books: frozenset[str] | None = None,
) -> ExtractedField | None:
    """提取书名。

    优先匹配含全角冒号的主副标题行（如 ``综漫：吉他雇佣兵无法找到归宿？``）；
    未命中时回退到无冒号书名匹配（见 :func:`_extract_book_title_fallback`）。

    """
    for line in lines:
        text = line.text.strip()
        if _BOOK_TITLE_RE.match(text) and len(text) <= _BOOK_MAX_LEN:
            return ExtractedField(
                value=text,
                source_text=line.text,
                confidence=line.confidence,
            )
    return _extract_book_title_fallback(lines, known_books=known_books)


def _extract_book_title_fallback(
    lines: list[OCRTextLine],
    *,
    known_books: frozenset[str] | None = None,
) -> ExtractedField | None:
    """回退提取无冒号书名（如 ``乐队少女不能啵经纪人嘴``）。

    利用「书评正文 → 书名 → 作者」的页面垂直结构：作者候选行上方紧邻、
    且间距在 ``_BOOK_AUTHOR_MAX_GAP`` 内的非界面短文本行视为书名候选。

    - ``known_books`` 提供时做**双向确认**：书名候选必须与白名单书名
      精确一致才接受（最稳，宁缺毋滥）；
    - ``known_books`` 未提供时接受距作者行最近的候选行。

    Args:
        lines: OCR 文本行列表。
        known_books: 群策略中配置的全部白名单书名（可选）。

    Returns:
        书名提取字段；无法可靠提取时为 ``None``。

    """
    positioned = [(line, y) for line in lines if (y := _y_center(line)) is not None]
    candidates: list[tuple[OCRTextLine, int]] = []
    for author_line, author_y in positioned:
        if not _is_author_candidate(author_line.text):
            continue
        for line, y in positioned:
            gap = author_y - y
            if not 0 < gap <= _BOOK_AUTHOR_MAX_GAP:
                continue
            text = line.text.strip()
            if not _is_fallback_book_candidate(text):
                continue
            candidates.append((line, gap))

    if not candidates:
        return None

    if known_books:
        for line, _gap in sorted(candidates, key=lambda item: item[1]):
            if line.text.strip() in known_books:
                return ExtractedField(
                    value=line.text.strip(),
                    source_text=line.text,
                    confidence=line.confidence,
                )
        # 白名单提供了但无精确命中：不冒险放行，交由管理员审核。
        return None

    best, _gap = min(candidates, key=lambda item: item[1])
    return ExtractedField(
        value=best.text.strip(),
        source_text=best.text,
        confidence=best.confidence,
    )


def _is_fallback_book_candidate(text: str) -> bool:
    """是否为无冒号书名的候选行。

    要求：非界面元素/时间/评分/时长，且**必须含中文字符**（本群白名单
    书名均为中文；纯字母/数字/符号行如状态栏 ``GG50``、``KB/s`` 直接排除）。

    """
    stripped = text.strip()
    valid_length = _MIN_BOOK_LEN <= len(stripped) <= _BOOK_MAX_LEN
    is_ui_element = (
        stripped in _IDENTITY_MARKERS
        or stripped in (_PAGE_TITLE, _SELF_MARKER)
        or bool(_RELATIVE_TIME_RE.search(stripped))
        or bool(_DATE_MMDD_RE.search(stripped))
        or bool(_RATING_RE.match(stripped))
        or bool(_READ_DURATION_RE.search(stripped))
    )
    # 必须含中文字符，排除纯数字/字母/符号/表情（状态栏、界面元素等）。
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in stripped)
    return valid_length and not is_ui_element and has_cjk


def _extract_author(
    lines: list[OCRTextLine],
    book_field: ExtractedField | None,
) -> ExtractedField | None:
    """提取作者名。

    优先取书名行下方、非界面元素的名字行；其次匹配 ``XX著`` / ``作者：XX``。

    """
    if book_field is not None:
        book_y = _y_center_from_text(lines, book_field.source_text)
        for line in lines:
            if not _is_author_candidate(line.text):
                continue
            y = _y_center(line)
            if y is None or book_y is None or y <= book_y:
                continue
            return ExtractedField(
                value=line.text.strip(),
                source_text=line.text,
                confidence=line.confidence,
            )

    author_named = re.compile(r"^([^著]{1,16})著\s*[。．]?$")
    author_label = re.compile(r"作者[:：]?\s*([^,，。]{1,16})")
    for line in lines:
        match = author_named.search(line.text)
        if match is not None:
            return ExtractedField(
                value=match.group(1).strip(),
                source_text=line.text,
                confidence=line.confidence,
            )
        match = author_label.search(line.text)
        if match is not None:
            return ExtractedField(
                value=match.group(1).strip(),
                source_text=line.text,
                confidence=line.confidence,
            )
    return None


def _is_author_candidate(text: str) -> bool:
    """是否为作者名候选。"""
    stripped = text.strip()
    if not stripped or len(stripped) > _AUTHOR_MAX_LEN:
        return False
    if stripped in _IDENTITY_MARKERS or stripped in (_PAGE_TITLE, _SELF_MARKER):
        return False
    if _RELATIVE_TIME_RE.search(stripped) or _DATE_MMDD_RE.search(stripped):
        return False
    if _RATING_RE.match(stripped) or _READ_DURATION_RE.search(stripped):
        return False
    return not _BOOK_TITLE_RE.match(stripped)


def _extract_review_text(
    lines: list[OCRTextLine],
    book_field: ExtractedField | None,
) -> ExtractedField | None:
    """提取书评正文：书名行上方、读者名下方最长的连续文本段。"""
    if book_field is None:
        return None
    book_y = _y_center_from_text(lines, book_field.source_text)
    candidates: list[OCRTextLine] = []
    for line in lines:
        y = _y_center(line)
        if y is None or book_y is None or y >= book_y:
            continue
        if not line.text.strip():
            continue
        if _is_reader_candidate(line.text) or _is_author_candidate(line.text):
            continue
        candidates.append(line)
    if not candidates:
        return None
    best = max(candidates, key=lambda line: len(line.text.strip()))
    return ExtractedField(
        value=best.text.strip(),
        source_text=best.text,
        confidence=best.confidence,
    )


def _split_merged_book_author(
    lines: list[OCRTextLine],
    *,
    known_books: frozenset[str] | None,
) -> tuple[ExtractedField, ExtractedField] | None:
    """从合并行（书名+作者同行）中切分出书名与作者。

    书评详情页中书名与作者可能在**同一行**（如
    ``少女乐队神人多，急需棍棒教育百舸川掮客``），无冒号也无换行分隔。
    当 ``known_books`` 提供时，若某行以某白名单书名为**前缀**，则取该书名为
    书名，行内其余文本为作者。

    Args:
        lines: OCR 文本行列表。
        known_books: 群策略配置的白名单书名（必须提供，否则无法可靠切分）。

    Returns:
        (书名字段, 作者字段)；无可切分的合并行时返回 ``None``。

    """
    if not known_books:
        return None
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        for book in known_books:
            if not book:
                continue
            if text.startswith(book):
                remainder = text[len(book) :].strip()
                if not remainder:
                    continue
                book_field = ExtractedField(
                    value=book,
                    source_text=line.text,
                    confidence=line.confidence,
                )
                author_field = ExtractedField(
                    value=remainder,
                    source_text=line.text,
                    confidence=line.confidence,
                )
                return book_field, author_field
    return None


def _find_page_title_y(lines: list[OCRTextLine]) -> int | None:
    """返回「书评详情」标题行的 y 坐标。"""
    for line in lines:
        if line.text.strip() == _PAGE_TITLE:
            return _y_center(line)
    return None


def _y_center_from_text(lines: list[OCRTextLine], source_text: str) -> int | None:
    """按文本内容查找行的 y 坐标。"""
    for line in lines:
        if line.text == source_text:
            return _y_center(line)
    return None


def _y_center(line: OCRTextLine) -> int | None:
    """返回文本行的垂直中心 y 坐标，无位置信息时为 ``None``。"""
    if line.box is None:
        return None
    return sum(y for _, y in line.box) // len(line.box)
