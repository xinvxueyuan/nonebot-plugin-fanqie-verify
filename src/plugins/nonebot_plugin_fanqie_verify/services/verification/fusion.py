"""多模型 OCR 证据融合器。

三大 PaddleOCR 模型本质定位互补：
- ``PP-OCRv6``：场景文字识别（检测+识别），返回纯文本行；
- ``PP-StructureV3``：文档结构解析（Markdown/JSON），返回版面结构 + 逐行文本；
- ``PaddleOCR-VL-1.6``：视觉语言大模型，文档深度理解，返回布局块。

三者对同一张图识别结果可能不同（某模型认出作者、另一模型认出书名）。
本融合器对**每个字段**在所有模型提取结果中取置信度最高、且不低于
可配置阈值的值（逐字段选优），最大化利用各模型各自擅长的能力。

阈值默认 0.9，可通过 ``FANQIE_SIMILARITY_THRESHOLD`` 配置。本模块是
**纯逻辑**，不依赖 ``core.config`` / nonebot，可被独立 CLI 直接复用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ExtractedField, ReadingEvidence

# 参与融合的文本字段（每个都是 ExtractedField | None）。
_FUSION_FIELDS: tuple[str, ...] = (
    "review_detail_page",
    "reader_name",
    "publish_time",
    "rating",
    "read_duration",
    "book_name",
    "author",
    "review_text",
)

# 默认置信度阈值（不传参时使用）。
_DEFAULT_THRESHOLD = 0.9


def _pick_field(
    evidences: list[ReadingEvidence],
    field_name: str,
    threshold: float,
) -> tuple[ExtractedField | None, str | None]:
    """从多个模型证据中选取某字段置信度最高且达阈值者。

    ``is_self_review``（「我」徽章）为布尔标志，不参与置信度取优，单独处理。

    Args:
        evidences: 各模型的提取证据列表。
        field_name: 字段名（见 ``_FUSION_FIELDS``）。
        threshold: 置信度阈值；低于该值的字段不被采纳。

    Returns:
        (选中的字段, 来源模型索引)。字段全部低于阈值或都为空时返回
        ``(None, None)``。

    """
    best: ExtractedField | None = None
    best_conf = -1.0
    best_model: str | None = None
    for idx, ev in enumerate(evidences):
        field = getattr(ev, field_name, None)
        if field is None:
            continue
        if field.confidence < threshold:
            continue
        if field.confidence > best_conf:
            best = field
            best_conf = field.confidence
            best_model = str(idx)
    return best, best_model


def merge_evidences(
    evidences: list[ReadingEvidence],
    *,
    threshold: float | None = None,
) -> ReadingEvidence:
    """融合多个模型的提取证据，逐字段选置信度最高者。

    Args:
        evidences: 各模型的提取证据（可为空或含失败模型）。
        threshold: 置信度阈值；为 ``None`` 时使用默认 0.9。

    Returns:
        融合后的证据。

    """
    valid = list(evidences)
    threshold = _DEFAULT_THRESHOLD if threshold is None else threshold
    if not valid:
        from .models import ReadingEvidence

        return ReadingEvidence()

    # 「我」徽章：任一模型检测到即认为本人书评。
    is_self_review = any(ev.is_self_review for ev in valid)

    from .models import ReadingEvidence

    merged = ReadingEvidence(is_self_review=is_self_review)
    for field_name in _FUSION_FIELDS:
        picked, _ = _pick_field(valid, field_name, threshold)
        if picked is not None:
            object.__setattr__(merged, field_name, picked)
    # publish_days_ago 取自所选 publish_time 的来源证据。
    publish = merged.publish_time
    if publish is not None:
        for ev in valid:
            f = ev.publish_time
            if f is not None and f.value == publish.value:
                merged = ReadingEvidence(
                    is_self_review=merged.is_self_review,
                    reader_name=merged.reader_name,
                    publish_time=merged.publish_time,
                    publish_days_ago=ev.publish_days_ago,
                    rating=merged.rating,
                    read_duration=merged.read_duration,
                    book_name=merged.book_name,
                    author=merged.author,
                    review_text=merged.review_text,
                    review_detail_page=merged.review_detail_page,
                )
                break
    return merged


def merge_evidences_with_trace(
    evidences: list[ReadingEvidence],
    models: list[str],
    *,
    threshold: float | None = None,
) -> tuple[ReadingEvidence, dict[str, dict[str, Any]]]:
    """融合多模型证据并返回各字段的来源溯源。

    与 :func:`merge_evidences` 结果一致，额外返回 ``provenance``：记录每个
    字段最终采用的 ``value``、置信度及贡献的模型名（便于审计"哪个模型
    提供了哪个字段"）。

    Args:
        evidences: 各模型的提取证据（与 models 一一对应）。
        models: 各模型名（与 evidences 顺序一致）。
        threshold: 置信度阈值；为 ``None`` 时使用默认 0.9。

    Returns:
        (融合后的证据, 字段溯源字典)。

    """
    merged = merge_evidences(evidences, threshold=threshold)
    threshold = _DEFAULT_THRESHOLD if threshold is None else threshold
    valid = list(evidences)
    # 字段溯源：对每个最终非空的字段，找到最高置信度的贡献者。
    provenance: dict[str, dict[str, Any]] = {}
    # 溯源用当前字段值匹配各模型的同字段（置信度最高者）。
    for field_name in _FUSION_FIELDS:
        final_field = getattr(merged, field_name, None)
        if final_field is None:
            provenance[field_name] = {"value": None, "confidence": None, "model": None}
            continue
        best_conf = -1.0
        best_model: str | None = None
        for idx, ev in enumerate(valid):
            field = getattr(ev, field_name, None)
            if field is None:
                continue
            if field.value == final_field.value and field.confidence > best_conf:
                best_conf = field.confidence
                best_model = models[idx] if idx < len(models) else str(idx)
        provenance[field_name] = {
            "value": final_field.value,
            "confidence": best_conf if best_conf >= 0 else None,
            "model": best_model,
        }
    return merged, provenance


__all__ = ["merge_evidences", "merge_evidences_with_trace"]
