"""OCR 识别结果的数据模型。

将 PaddleOCR 官方 API 的原始 ``pruned_result`` 规范化为一组稳定的
数据类，隔离第三方返回结构，供上层信息提取模块使用。

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class OCRTextLine:
    """一行识别文本及其置信度与位置。

    Attributes:
        text: 识别出的文本内容。
        confidence: 置信度，取值范围 0.0 到 1.0。
        box: 四个角点坐标 ``[[x1, y1], [x2, y2], [x3, y3], [x4, y4]]``，
            无位置信息时为 ``None``。

    """

    text: str
    confidence: float
    box: list[list[int]] | None = None


@dataclass(frozen=True, slots=True)
class OCRPage:
    """单页的 OCR 识别结果。

    Attributes:
        lines: 按检测顺序排列的识别文本行。
        raw: 页面的原始 ``pruned_result`` 字典。

    """

    lines: list[OCRTextLine] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OCRResult:
    """一次完整 OCR 请求的规范化结果。

    Attributes:
        job_id: 云端任务的唯一标识。
        pages: 识别出的页面列表。

    """

    job_id: str
    pages: list[OCRPage] = field(default_factory=list)

    @property
    def text(self) -> str:
        """所有页面的识别文本，行与行之间用换行符连接。"""
        return "\n".join(line.text for page in self.pages for line in page.lines)
