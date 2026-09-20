"""核心业务链路冒烟测试：OCR 规范化 → 信息提取 → 放行策略 → 综合判断。"""

from __future__ import annotations

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.ocr import (
    OCRClient,
    OCRPage,
    OCRResult,
    OCRTextLine,
)
from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
    extract_reading_evidence,
    judge_evidence,
)
from src.plugins.nonebot_plugin_fanqie_verify.services.verification.policy import (
    AuthorEntry,
    GroupPolicy,
    VerificationPolicy,
)

pytestmark = pytest.mark.smoke


def _line(text: str, y: int, confidence: float = 1.0) -> OCRTextLine:
    """构造一行位于 ``y`` 处的 OCR 文本。"""
    return OCRTextLine(
        text=text,
        confidence=confidence,
        box=[[0, y], [200, y], [200, y + 30], [0, y + 30]],
    )


def _shelf_result() -> OCRResult:
    """模拟书评详情页（本人发布）的 OCR 结果。"""
    return OCRResult(
        job_id="smoke-review",
        pages=[
            OCRPage(
                lines=[
                    _line("书评详情", 0),
                    _line("新v学员", 30),
                    _line("我", 40),
                    _line("刚刚", 60),
                    _line("阅读2小时后点评", 80),
                    _line("综漫：吉他雇佣兵无法找到归宿？", 200),
                    _line("阿百川大鬼", 220),
                ]
            )
        ],
    )


def test_ocr_models_normalize() -> None:
    """OCR 客户端模型名规范化应工作。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.ocr.client import (
        _normalize_model,
    )

    assert _normalize_model("pp-ocrv6") == "PP-OCRv6"


def test_core_services_initializable() -> None:
    """核心服务单例应能初始化。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
        get_session_store,
    )

    store = get_session_store()
    assert store is not None
    assert store.list_waiting() == ()


def test_extract_to_judge_pipeline() -> None:
    """OCR 结果 → 提取 → 放行策略 → 综合判断应跑通并放行。"""
    result = _shelf_result()
    evidence = extract_reading_evidence(result)

    assert evidence.is_self_review is True
    assert evidence.book_name is not None
    assert evidence.book_name.value == "综漫：吉他雇佣兵无法找到归宿？"
    assert evidence.author is not None
    assert evidence.author.value == "阿百川大鬼"
    assert evidence.is_sufficient is True

    policy = VerificationPolicy(
        require_all=False,
        required_elements=frozenset({"book_name", "author"}),
        groups={
            123: GroupPolicy(
                group_id=123,
                authors=(AuthorEntry(name="阿百川大鬼"),),
            ),
        },
    )
    policy_check = policy.check(evidence, 123)
    assert policy_check.passed is True

    verdict = judge_evidence(evidence)
    assert verdict.passed is True


def test_ocr_client_injectable() -> None:
    """OCRClient 应能注入替身客户端。"""
    client = OCRClient(client=None)  # type: ignore[arg-type]
    assert client is not None
