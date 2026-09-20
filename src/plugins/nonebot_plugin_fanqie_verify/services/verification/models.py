"""入群验证业务层：识别结果处理的数据模型。

从 OCR 服务层得到规范化的 :class:`OCRResult` 后，本模块承载
FR3 信息提取的产出结构，供后续 FR4 综合判断与流程编排使用。

验证目标页面为番茄小说「书评详情」页：新成员需发送自己发布的书评
截图（含「我」徽章），提取读者名、发布日期、评分、书名与作者等信息。

"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtractedField:
    """一个提取出的结构化字段及其来源溯源。

    Attributes:
        value: 规范化后的字段值。
        source_text: 命中所用规则的 OCR 原始文本行，便于审计与调试。
        confidence: 该文本行对应的 OCR 置信度，取值范围 0.0 到 1.0。

    """

    value: str
    source_text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ReadingEvidence:
    """从书评详情页 OCR 结果中提取的阅读证据。

    Attributes:
        is_self_review: 是否检测到「我」徽章（自己发布的书评）。
        reader_name: 书评读者名；自己发布时通常为「我」。
        publish_time: 书评发布时间（相对时间或 ``MM-DD`` 日期）。
        publish_days_ago: 发布时间的归一化天数；无法归一化时为 ``None``。
        rating: 评分（以星数表示，如 ``"★★"``）。
        read_duration: 阅读时长描述（如 ``"阅读2小时后点评"``）。
        book_name: 书名字段。
        author: 作者名字段。
        review_text: 书评正文字段。
        review_detail_page: 是否识别到「书评详情」标题（确认截图确实是
            书评详情页，而非列表页或其他页面）。

    """

    is_self_review: bool = False
    reader_name: ExtractedField | None = None
    publish_time: ExtractedField | None = None
    publish_days_ago: int | None = None
    rating: ExtractedField | None = None
    read_duration: ExtractedField | None = None
    book_name: ExtractedField | None = None
    author: ExtractedField | None = None
    review_text: ExtractedField | None = None
    review_detail_page: ExtractedField | None = None

    @property
    def is_sufficient(self) -> bool:
        """是否已提取到足够信息（FR3）。

        必须识别到「书评详情」标题（确认确实是书评页）、检测到「我」
        徽章，并提取到书名与作者。

        """
        return (
            self.is_self_review
            and self.review_detail_page is not None
            and self.book_name is not None
            and self.author is not None
        )
