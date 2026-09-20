"""OCR 识别服务测试。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from httpx import Response
import pytest
from pytest import MonkeyPatch
import respx

from src.plugins.nonebot_plugin_fanqie_verify.services.ocr import (
    OCRClient,
    OCRError,
    OCRInvalidResponseError,
    OCRNetworkError,
    OCRNotConfiguredError,
    OCRTextLine,
    get_ocr_client,
    recognize_file,
)
from src.plugins.nonebot_plugin_fanqie_verify.services.ocr.client import (
    _layout_result_to_page,
    _normalize_model,
    _pruned_result_to_page,
)
from src.plugins.nonebot_plugin_fanqie_verify.services.ocr.service import (
    _reset_client,
)

_SCREENSHOT_DIR = Path(r"C:\Users\admin\Downloads\vivo办公套件")
_SCREENSHOT_SELF_REVIEW = _SCREENSHOT_DIR / "Screenshot_20260813_115925.jpg"
_SCREENSHOT_OTHER_REVIEW = _SCREENSHOT_DIR / "Screenshot_20260813_115840.jpg"


def _raw_pruned_result() -> dict[str, Any]:
    """构造一份模拟的 PaddleOCR pruned_result 字典。"""
    return {
        "model_settings": {"use_doc_preprocessor": True},
        "rec_texts": [
            "书架",
            "大少女乐队时代的传奇经纪人",
            "已完结・共242章",
            "读到242章",
            "1分钟前",
        ],
        "rec_scores": [0.99, 0.98, 0.97, 0.96, 0.95],
        "rec_polys": [
            [[0, 0], [100, 0], [100, 30], [0, 30]],
            [[0, 40], [200, 40], [200, 70], [0, 70]],
            [[0, 80], [180, 80], [180, 110], [0, 110]],
            [[0, 120], [120, 120], [120, 150], [0, 150]],
            [[0, 160], [90, 160], [90, 190], [0, 190]],
        ],
        "textline_orientation_angles": [0, 0, 0, 0, 0],
    }


class FakeOCRResult:
    """模拟 paddleocr 的 OCRResult 对象。"""

    def __init__(self, pages: list[Any]) -> None:
        self.job_id = "job-fake-123"
        self.pages = pages


class FakePage:
    """模拟 paddleocr 的 OCRPage 对象。"""

    def __init__(self, pruned_result: dict[str, Any]) -> None:
        self.pruned_result = pruned_result


class FakeClient:
    """模拟 AsyncPaddleOCRClient，供测试注入。"""

    def __init__(
        self, result: Any | None = None, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def ocr(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture()
def ocr_client() -> OCRClient:
    """提供一个注入 FakeClient 的 OCRClient 实例。"""
    return OCRClient(client=FakeClient())  # type: ignore[arg-type]


from collections.abc import Generator


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None]:
    """每个测试前后重置全局客户端单例。"""
    yield
    _reset_client()


def test_screenshot_files_exist() -> None:
    """测试用截图应存在于下载目录（仅本机可运行）。"""
    if not (_SCREENSHOT_SELF_REVIEW.is_file() and _SCREENSHOT_OTHER_REVIEW.is_file()):
        pytest.skip("测试截图未就绪（本地开发目录）")


def test_normalize_model() -> None:
    """模型名应被规范化。"""
    assert _normalize_model("pp-ocrv6") == "PP-OCRv6"
    assert _normalize_model("PaddleOCR-VL-1.6") == "PaddleOCR-VL-1.6"
    assert _normalize_model("  PP-OCRV5  ") == "PP-OCRv5"
    assert _normalize_model("unknown-model") == "unknown-model"


def test_pruned_result_to_page() -> None:
    """pruned_result 应被规范化为 OCRPage 文本行。"""
    page = _pruned_result_to_page(_raw_pruned_result())

    assert len(page.lines) == 5
    assert page.lines[0].text == "书架"
    assert page.lines[0].confidence == 0.99
    assert page.lines[1].text == "大少女乐队时代的传奇经纪人"
    assert page.lines[2].confidence == pytest.approx(0.97)
    assert page.lines[3].box == [[0, 120], [120, 120], [120, 150], [0, 150]]
    assert page.raw["rec_texts"][0] == "书架"


def test_pruned_result_to_page_invalid() -> None:
    """缺少 rec_texts/rec_scores 列表时应抛出 OCRInvalidResponseError。"""
    with pytest.raises(OCRInvalidResponseError):
        _pruned_result_to_page({"model_settings": {}, "rec_texts": "not-a-list"})


def _raw_layout_result() -> dict:
    """构造一份模拟的 PaddleOCR-VL 布局解析 pruned_result。"""
    return {
        "page_count": None,
        "width": 1440,
        "height": 3168,
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_content": "书评详情",
                "block_bbox": [569, 199, 871, 288],
                "block_id": 3,
            },
            {
                "block_label": "image",
                "block_content": "",
                "block_bbox": [60, 195, 116, 283],
                "block_id": 2,
            },
            {
                "block_label": "text",
                "block_content": "综漫：吉他雇佣兵无法找到归宿？",
                "block_bbox": [103, 3036, 376, 3105],
                "block_id": 15,
            },
            {
                "block_label": "footer",
                "block_content": "发表评论...",
                "block_bbox": [0, 3100, 1440, 3168],
                "block_id": 20,
            },
        ],
    }


def test_layout_result_to_page() -> None:
    """VL 布局解析结果应提取文本块为文本行，跳过图片块。"""
    page = _layout_result_to_page(_raw_layout_result())

    texts = [line.text for line in page.lines]
    assert texts == ["书评详情", "综漫：吉他雇佣兵无法找到归宿？", "发表评论..."]
    # 图片块（无内容）被跳过
    assert all(t != "" for t in texts)
    # 文本块保留包围盒位置
    assert page.lines[1].box == [[103, 3036], [376, 3036], [376, 3105], [103, 3105]]
    assert page.raw["parsing_res_list"] is not None


def test_layout_result_to_page_empty() -> None:
    """无解析块时返回空 OCRPage。"""
    page = _layout_result_to_page({"parsing_res_list": "not-a-list"})
    assert page.lines == []


def test_pruned_result_skips_non_string_lines() -> None:
    """rec_texts 中的非字符串元素应被跳过。"""
    page = _pruned_result_to_page({
        "rec_texts": ["正常", 123, None, "另一行"],
        "rec_scores": [0.9, 0.8, 0.7, 0.6],
    })
    assert [line.text for line in page.lines] == ["正常", "另一行"]


@pytest.mark.asyncio
async def test_recognize_pipeline_uses_file_path(
    ocr_client: OCRClient,
    monkeypatch: MonkeyPatch,
) -> None:
    """recognize_path 应把本地路径透传给底层客户端。"""
    fake = ocr_client._client
    assert isinstance(fake, FakeClient)
    fake.result = FakeOCRResult([FakePage(_raw_pruned_result())])

    monkeypatch.setattr(
        "src.plugins.nonebot_plugin_fanqie_verify.services.ocr.service._client",
        ocr_client,
    )
    result = await recognize_file(_SCREENSHOT_SELF_REVIEW)

    assert fake.calls[0]["file_path"] == str(_SCREENSHOT_SELF_REVIEW)
    assert fake.calls[0]["file_url"] is None
    assert result.job_id == "job-fake-123"
    assert len(result.pages) == 1
    assert "大少女乐队时代的传奇经纪人" in result.text


@pytest.mark.asyncio
async def test_recognize_url(ocr_client: OCRClient) -> None:
    """recognize_url 应把 URL 透传给底层客户端。"""
    fake = ocr_client._client
    assert isinstance(fake, FakeClient)
    fake.result = FakeOCRResult([FakePage(_raw_pruned_result())])

    result = await ocr_client.recognize_url("https://example.com/book.png")

    assert fake.calls[0]["file_url"] == "https://example.com/book.png"
    assert fake.calls[0]["file_path"] is None
    assert result.job_id == "job-fake-123"


@pytest.mark.asyncio
async def test_recognize_bytes_uses_temp_file(
    ocr_client: OCRClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """recognize_bytes 应把字节写入临时文件后再识别。"""
    fake = ocr_client._client
    assert isinstance(fake, FakeClient)
    fake.result = FakeOCRResult([FakePage(_raw_pruned_result())])

    monkeypatch.setattr(
        "tempfile.gettempdir",
        lambda: str(tmp_path),
    )
    result = await ocr_client.recognize_bytes(b"fake-image-bytes", suffix=".jpg")

    assert result.job_id == "job-fake-123"
    assert fake.calls[0]["file_path"].endswith(".jpg")
    assert not Path(fake.calls[0]["file_path"]).exists()


@pytest.mark.asyncio
async def test_map_exception_raises_plugin_error() -> None:
    """Httpx 网络异常应被映射为插件自定义异常。"""
    from httpx import ConnectError

    from src.plugins.nonebot_plugin_fanqie_verify.services.ocr.client import (
        _map_http_error,
    )

    mapped = _map_http_error(ConnectError("connection refused"))
    assert isinstance(mapped, OCRNetworkError)


@pytest.mark.asyncio
async def test_map_status_error_categories() -> None:
    """HTTP 状态码应映射为对应异常类别。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.ocr import (
        OCRAuthError,
        OCRBadRequestError,
        OCRRateLimitError,
        OCRServiceUnavailableError,
    )
    from src.plugins.nonebot_plugin_fanqie_verify.services.ocr.client import (
        _map_status_error,
    )

    assert isinstance(_map_status_error(401, ""), OCRAuthError)
    assert isinstance(_map_status_error(403, ""), OCRAuthError)
    assert isinstance(_map_status_error(429, ""), OCRRateLimitError)
    assert isinstance(_map_status_error(500, ""), OCRServiceUnavailableError)
    assert isinstance(_map_status_error(400, ""), OCRBadRequestError)
    assert isinstance(_map_status_error(422, ""), OCRError)


@pytest.mark.asyncio
async def test_no_token_raises_not_configured(monkeypatch: MonkeyPatch) -> None:
    """未配置 token 时应抛出 OCRNotConfiguredError。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_ocr_api_token", "")
    client = OCRClient()
    with pytest.raises(OCRNotConfiguredError):
        await client.recognize_url("https://example.com/x.png")


@pytest.mark.asyncio
async def test_http_pipeline_with_respx(
    monkeypatch: MonkeyPatch,
) -> None:
    """模拟网络：验证提交 → 轮询 → jsonl 解析的完整 HTTP 链路。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config

    monkeypatch.setattr(plugin_config, "fanqie_ocr_api_token", "test-token")
    monkeypatch.setattr(
        plugin_config,
        "fanqie_ocr_api_url",
        "https://ocr.example.com/api/v2/ocr/jobs",
    )
    monkeypatch.setattr(plugin_config, "fanqie_ocr_poll_timeout", 30.0)

    jsonl_line = {
        "logId": "log-1",
        "result": {
            "ocrResults": [
                {"prunedResult": _raw_pruned_result()},
            ]
        },
    }
    import json as _json

    jsonl_body = _json.dumps(jsonl_line, ensure_ascii=False)

    with respx.mock:
        route = respx.post("https://ocr.example.com/api/v2/ocr/jobs")
        route.mock(
            return_value=Response(
                200,
                json={"code": 0, "data": {"jobId": "job-respx-1"}},
            )
        )
        poll = respx.get("https://ocr.example.com/api/v2/ocr/jobs/job-respx-1")
        poll.mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "state": "done",
                        "resultUrl": {
                            "jsonUrl": "https://ocr.example.com/result.jsonl"
                        },
                    }
                },
            )
        )
        jsonl_route = respx.get("https://ocr.example.com/result.jsonl")
        jsonl_route.mock(return_value=Response(200, text=jsonl_body))

        client = OCRClient()
        result = await client.recognize_url("https://img.example.com/shelf.jpg")

    assert result.job_id == "job-respx-1"
    assert len(result.pages) == 1
    assert "大少女乐队时代的传奇经纪人" in result.text
    assert route.called and poll.called and jsonl_route.called


def test_get_ocr_client_singleton() -> None:
    """get_ocr_client 应返回同一个单例。"""
    first = get_ocr_client()
    second = get_ocr_client()
    assert first is second
    _reset_client()
    assert get_ocr_client() is not first


def test_result_text_joins_lines() -> None:
    """OCRResult.text 应按顺序拼接所有文本行。"""
    from src.plugins.nonebot_plugin_fanqie_verify.services.ocr import (
        OCRPage,
        OCRResult,
    )

    result = OCRResult(
        job_id="job-1",
        pages=[
            OCRPage(
                lines=[
                    OCRTextLine(text="第一行", confidence=0.9),
                    OCRTextLine(text="第二行", confidence=0.8),
                ]
            )
        ],
    )
    assert result.text == "第一行\n第二行"


def _has_ocr_credentials() -> bool:
    """是否配置了云端 OCR 凭据。"""
    from src.plugins.nonebot_plugin_fanqie_verify.core.config import plugin_config

    return bool(
        plugin_config.fanqie_ocr_api_token or os.environ.get("PADDLEOCR_ACCESS_TOKEN")
    )


@pytest.mark.skipif(not _has_ocr_credentials(), reason="未配置 OCR 云端凭据")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "screenshot,expect_text",
    [
        (
            _SCREENSHOT_SELF_REVIEW,
            "综漫：吉他雇佣兵无法找到归宿？",
        ),
        (
            _SCREENSHOT_OTHER_REVIEW,
            "综漫：吉他雇佣兵无法找到归宿？",
        ),
    ],
)
async def test_recognize_real_cloud(screenshot: Path, expect_text: str) -> None:
    """使用真实云端服务识别两张书评详情页测试截图（需配置凭据）。"""
    result = await recognize_file(screenshot)
    assert expect_text in result.text
    assert result.pages
    assert any(line.text for page in result.pages for line in page.lines)
