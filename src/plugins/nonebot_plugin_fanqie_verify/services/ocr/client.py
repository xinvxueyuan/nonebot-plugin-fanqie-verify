"""PaddleOCR 云端服务客户端封装。

基于官方 AIStudio PP-OCRv6 云端 API（``https://paddleocr.aistudio-app.com/api/v2/ocr/jobs``）
提供插件可用的异步识别入口：把图片路径、字节或 URL 提交到云端服务，
轮询等待任务完成，并把返回的 ``pruned_result`` 规范化为
:class:`OCRResult` 数据模型。

实现不依赖 ``paddleocr`` 库，直接使用 ``httpx`` 调用任务提交与轮询接口，
避免第三方库与本插件版本之间的兼容性问题。

"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

import httpx

from ...core.config import plugin_config
from .errors import (
    OCRAuthError,
    OCRBadRequestError,
    OCRError,
    OCRInvalidResponseError,
    OCRJobFailedError,
    OCRNetworkError,
    OCRNotConfiguredError,
    OCRRateLimitError,
    OCRServiceUnavailableError,
    OCRTimeoutError,
)
from .models import OCRPage, OCRResult, OCRTextLine

logger = logging.getLogger(__name__)


class _FakeOCRResult(Protocol):
    job_id: str
    pages: list[Any]


class _FakeClient(Protocol):
    async def ocr(self, **kwargs: Any) -> _FakeOCRResult: ...


_DEFAULT_API_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
_POLL_INTERVAL_SECONDS = 5.0

# HTTP 状态码语义常量。
_STATUS_OK = 200
_STATUS_AUTH = (401, 403)
_STATUS_RATE_LIMIT = 429
_STATUS_SERVER_ERROR = 500
_STATUS_BAD_REQUEST = 400

_MODEL_ALIASES: dict[str, str] = {
    "pp-ocrv6": "PP-OCRv6",
    "pp-ocrv5": "PP-OCRv5",
    "paddleocr-vl": "PaddleOCR-VL",
    "paddleocr-vl-1.5": "PaddleOCR-VL-1.5",
    "paddleocr-vl-1.6": "PaddleOCR-VL-1.6",
    "pp-structurev3": "PP-StructureV3",
}

_OPTIONAL_PAYLOAD: dict[str, object] = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useTextlineOrientation": False,
}


def _normalize_model(model: str) -> str:
    """把配置中的模型名规范化为官方 API 接受的名称。

    Args:
        model: 配置提供的模型名，大小写不敏感。

    Returns:
        规范化后的模型名；无法识别时原样返回。

    """
    return _MODEL_ALIASES.get(model.strip().lower(), model.strip())


_MIN_POINT_COORDINATES = 2
_BBOX_COORDINATE_COUNT = 4  # block_bbox 形如 [x1, y1, x2, y2]

# VL 布局结果中与读者名合并成单块的「我」徽章标识。
_layout_self_marker = "我"


def _parse_polys(pruned_result: dict) -> list[list[list[int]]] | None:
    """从 pruned_result 提取每个文本框的四角坐标。"""
    polys = pruned_result.get("rec_polys")
    if not isinstance(polys, list):
        return None
    parsed: list[list[list[int]]] = []
    for poly in polys:
        if not isinstance(poly, list):
            return None
        points: list[list[int]] = []
        for point in poly:
            if (
                not isinstance(point, (list, tuple))
                or len(point) < _MIN_POINT_COORDINATES
            ):
                return None
            x, y = point[0], point[1]
            if not isinstance(x, int) or not isinstance(y, int):
                return None
            points.append([x, y])
        parsed.append(points)
    return parsed


def _pruned_result_to_page(pruned_result: dict) -> OCRPage:
    """把单页的原始 pruned_result 规范化为 OCRPage。"""
    texts = pruned_result.get("rec_texts", [])
    scores = pruned_result.get("rec_scores", [])
    boxes = _parse_polys(pruned_result)

    if not isinstance(texts, list) or not isinstance(scores, list):
        raise OCRInvalidResponseError("pruned_result 缺少 rec_texts/rec_scores 列表")

    lines: list[OCRTextLine] = []
    for index, text in enumerate(texts):
        if not isinstance(text, str):
            continue
        confidence = 0.0
        if index < len(scores) and isinstance(scores[index], (int, float)):
            confidence = float(scores[index])
        box = boxes[index] if boxes and index < len(boxes) else None
        lines.append(OCRTextLine(text=text, confidence=confidence, box=box))
    return OCRPage(lines=lines, raw=pruned_result)


def _split_self_marker(text: str) -> list[str]:
    r"""把 VL 布局块文本中末尾独立的「我」徽章拆成单独行。

    VL 布局合并可能把读者名与「我」徽章合成单个块（如 ``马钧 我``），
    而提取器要求独立的「我」徽章行来判定本人书评。本函数识别以空白
    结尾分隔的独立「我」并拆分。

    Args:
        text: 布局块文本内容。

    Returns:
        拆分后的文本行列表；无「我」徽章时原样返回单行。

    """
    if text.endswith(_layout_self_marker):
        name = text[: -len(_layout_self_marker)].rstrip()
        if name:
            return [name, _layout_self_marker]
    return [text]


def _layout_result_to_page(pruned_result: dict) -> OCRPage:
    """把 PP-StructureV3 / PaddleOCR-VL 系列布局解析结果规范化为 OCRPage。

    布局解析结果有两种文本呈现：

    - ``overall_ocr_res``（PP-StructureV3 结构化输出）：逐行识别结果，
      结构同 ``prunedResult``（``rec_texts``/``rec_scores``/``rec_polys``），
      时间精确、含逐行坐标，优先采用；
    - ``parsing_res_list``（PaddleOCR-VL 布局）：以布局块组织
      （``block_content`` 为文本、``block_label`` 为类型），块可能合并
      多行，作为回退。

    """
    overall = pruned_result.get("overall_ocr_res")
    if isinstance(overall, dict) and isinstance(overall.get("rec_texts"), list):
        return _pruned_result_to_page(overall)

    blocks = pruned_result.get("parsing_res_list", [])
    lines: list[OCRTextLine] = []
    if not isinstance(blocks, list):
        return OCRPage(lines=lines, raw=pruned_result)
    for block in blocks:
        if not isinstance(block, dict):
            continue
        label = block.get("block_label")
        content = block.get("block_content")
        if not isinstance(content, str) or not content.strip():
            continue  # 跳过图片/空内容块
        if label in ("image", "header_image", "footer_image", "seal_image"):
            continue
        box = None
        bbox = block.get("block_bbox")
        if (
            isinstance(bbox, list)
            and len(bbox) == _BBOX_COORDINATE_COUNT
            and all(isinstance(v, (int, float)) for v in bbox)
        ):
            # block_bbox 为 [x1, y1, x2, y2]，转为四角点
            x1, y1, x2, y2 = (int(v) for v in bbox)
            box = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        # VL 布局合并可能把「读者名」与「我」徽章合成一个块（如
        # ``马钧 我``）。拆分为独立两行，使「我」徽章可被提取器识别，
        # 否则 is_self_review 会误判为 False 导致拒绝。
        lines.extend(
            OCRTextLine(text=piece, confidence=1.0, box=box)
            for piece in _split_self_marker(content.strip())
        )
    return OCRPage(lines=lines, raw=pruned_result)


def _api_url() -> str:
    """返回云端任务提交地址。"""
    return plugin_config.fanqie_ocr_api_url or _DEFAULT_API_URL


def _map_http_error(exc: httpx.HTTPError) -> OCRError:
    """把 httpx 网络层异常映射为插件自定义异常。"""
    if isinstance(exc, httpx.TimeoutException):
        return OCRTimeoutError(str(exc))
    return OCRNetworkError(str(exc))


def _map_status_error(status_code: int, body: str) -> OCRError:
    """把非 200 的 HTTP 状态码映射为自定义异常。"""
    detail = body[:200] if body else f"HTTP {status_code}"
    if status_code in _STATUS_AUTH:
        return OCRAuthError(f"OCR 认证失败: {detail}")
    if status_code == _STATUS_RATE_LIMIT:
        return OCRRateLimitError(f"OCR 触发限流: {detail}")
    if status_code >= _STATUS_SERVER_ERROR:
        return OCRServiceUnavailableError(f"OCR 服务不可用: {detail}")
    if status_code == _STATUS_BAD_REQUEST:
        return OCRBadRequestError(f"OCR 请求无效: {detail}")
    return OCRError(f"OCR 请求失败 ({status_code}): {detail}")


class OCRClient:
    """PaddleOCR 云端识别客户端。

    直接通过 HTTP 提交识别任务并轮询结果，负责凭据读取、模型解析、
    结果规范化与异常映射。测试时可注入替身客户端（``_client``）绕过
    网络调用。

    """

    def __init__(self, client: _FakeClient | None = None) -> None:
        self._client: _FakeClient | None = client

    async def _submit_job(
        self,
        *,
        file_path: str | None,
        file_url: str | None,
        model: str,
    ) -> str:
        """提交识别任务，返回 jobId。"""
        import aiofiles

        token = plugin_config.fanqie_ocr_api_token
        if not token:
            raise OCRNotConfiguredError(
                "未配置 OCR 凭据：请设置 fanqie_ocr_api_token 或 PADDLEOCR_ACCESS_TOKEN"
            )

        url = _api_url()
        headers = {"Authorization": f"bearer {token}"}
        optional_payload = json.dumps(_OPTIONAL_PAYLOAD)

        if self._client is not None:
            raise OCRNotConfiguredError("替身客户端未实现 _submit_job")

        try:
            async with httpx.AsyncClient(
                timeout=plugin_config.fanqie_ocr_timeout
            ) as client:
                if file_url is not None:
                    response = await client.post(
                        url,
                        headers=headers,
                        json={
                            "fileUrl": file_url,
                            "model": model,
                            "optionalPayload": _OPTIONAL_PAYLOAD,
                        },
                    )
                else:
                    assert file_path is not None
                    async with aiofiles.open(file_path, "rb") as f:
                        content = await f.read()
                    response = await client.post(
                        url,
                        headers=headers,
                        data={
                            "model": model,
                            "optionalPayload": optional_payload,
                        },
                        files={"file": ("image", content, "application/octet-stream")},
                    )
        except httpx.HTTPError as exc:
            raise _map_http_error(exc) from exc

        if response.status_code != _STATUS_OK:
            raise _map_status_error(response.status_code, response.text)

        try:
            data = response.json()
            job_id = data["data"]["jobId"]
        except (ValueError, KeyError, TypeError) as exc:
            raise OCRInvalidResponseError(
                f"提交响应缺少 jobId: {response.text[:200]}"
            ) from exc
        if not isinstance(job_id, str):
            raise OCRInvalidResponseError(f"jobId 类型错误: {job_id!r}")
        return job_id

    async def _poll_job(self, job_id: str) -> str:
        """轮询任务状态直到 done，返回 jsonl 结果 URL。"""
        url = f"{_api_url()}/{job_id}"
        headers = {"Authorization": f"bearer {plugin_config.fanqie_ocr_api_token}"}
        deadline = plugin_config.fanqie_ocr_poll_timeout
        elapsed = 0.0
        timeout = plugin_config.fanqie_ocr_timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            while elapsed < deadline:
                try:
                    response = await client.get(url, headers=headers)
                except httpx.HTTPError as exc:
                    raise _map_http_error(exc) from exc
                if response.status_code != _STATUS_OK:
                    raise _map_status_error(response.status_code, response.text)
                try:
                    state = response.json()["data"]["state"]
                except (ValueError, KeyError, TypeError) as exc:
                    raise OCRInvalidResponseError(
                        f"轮询响应缺少 state: {response.text[:200]}"
                    ) from exc
                if state == "done":
                    try:
                        jsonl_url = response.json()["data"]["resultUrl"]["jsonUrl"]
                    except (KeyError, TypeError) as exc:
                        raise OCRInvalidResponseError("任务完成但缺少 jsonUrl") from exc
                    return jsonl_url
                if state == "failed":
                    error_msg = response.json()["data"].get("errorMsg") or "未知错误"
                    raise OCRJobFailedError(f"OCR 任务失败: {error_msg}")

                import asyncio

                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                elapsed += _POLL_INTERVAL_SECONDS
        raise OCRTimeoutError(f"OCR 任务轮询超时（{int(deadline)} 秒）")

    async def _fetch_jsonl(self, jsonl_url: str) -> list[dict]:
        """下载并解析结果 jsonl，返回每行的 result 字典。"""
        if self._client is not None:
            raise OCRNotConfiguredError("替身客户端未实现 _fetch_jsonl")
        try:
            async with httpx.AsyncClient(
                timeout=plugin_config.fanqie_ocr_timeout
            ) as client:
                response = await client.get(jsonl_url)
        except httpx.HTTPError as exc:
            raise _map_http_error(exc) from exc
        if response.status_code != _STATUS_OK:
            raise _map_status_error(response.status_code, response.text)

        results: list[dict] = []
        for raw_line in response.text.strip().split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                results.append(record["result"])
            except (ValueError, KeyError, TypeError) as exc:
                raise OCRInvalidResponseError(
                    f"jsonl 行解析失败: {line[:200]}"
                ) from exc
        return results

    async def _recognize(
        self,
        *,
        file_path: str | None = None,
        file_url: str | None = None,
        model: str | None = None,
    ) -> OCRResult:
        """向云端提交识别任务并返回规范化结果。

        Args:
            file_path: 本地图片文件路径。
            file_url: 可公开访问的图片地址。
            model: 使用的 PaddleOCR 模型名；为 ``None`` 时使用配置默认。

        """
        if file_path is None and file_url is None:
            raise ValueError("file_path 与 file_url 必须提供其一")

        model = _normalize_model(model or plugin_config.fanqie_ocr_model)
        logger.debug("提交 OCR 任务: model=%s file_url=%s", model, bool(file_url))

        if self._client is not None:
            fake = self._client
            result = await fake.ocr(file_path=file_path, file_url=file_url, model=model)
            pages = [
                _pruned_result_to_page(page.pruned_result) for page in result.pages
            ]
            return OCRResult(job_id=result.job_id, pages=pages)

        job_id = await self._submit_job(
            file_path=file_path,
            file_url=file_url,
            model=model,
        )
        jsonl_url = await self._poll_job(job_id)
        results = await self._fetch_jsonl(jsonl_url)

        pages: list[OCRPage] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            # PaddleOCR-VL 系列：布局解析结果。
            layout_results = result.get("layoutParsingResults", [])
            if isinstance(layout_results, list) and layout_results:
                for layout_result in layout_results:
                    pruned = (
                        layout_result.get("prunedResult")
                        if isinstance(layout_result, dict)
                        else None
                    )
                    if isinstance(pruned, dict):
                        pages.append(_layout_result_to_page(pruned))
                continue
            # PP-OCR 系列：逐行识别结果。
            ocr_results = result.get("ocrResults", [])
            for ocr_result in ocr_results:
                pruned = (
                    ocr_result.get("prunedResult")
                    if isinstance(ocr_result, dict)
                    else None
                )
                if isinstance(pruned, dict):
                    pages.append(_pruned_result_to_page(pruned))

        result_model = OCRResult(job_id=job_id, pages=pages)
        logger.info(
            "OCR 识别完成: job=%s pages=%s lines=%s",
            job_id,
            len(pages),
            sum(len(page.lines) for page in pages),
        )
        return result_model

    async def recognize_path(
        self,
        path: str | Path,
        *,
        model: str | None = None,
    ) -> OCRResult:
        """识别本地图片文件。

        Args:
            path: 本地图片文件路径。
            model: 使用的 PaddleOCR 模型名（默认配置）。

        Returns:
            规范化的识别结果。

        """
        return await self._recognize(file_path=str(path), model=model)

    async def recognize_url(
        self,
        url: str,
        *,
        model: str | None = None,
    ) -> OCRResult:
        """识别图片 URL。

        Args:
            url: 可公开访问的图片地址。
            model: 使用的 PaddleOCR 模型名（默认配置）。

        Returns:
            规范化的识别结果。

        """
        return await self._recognize(file_url=url, model=model)

    async def recognize_multi_url(
        self,
        url: str,
        *,
        models: list[str] | tuple[str, ...],
    ) -> dict[str, OCRResult]:
        """用多个模型并行识别同一图片 URL。

        各模型独立提交并获取规范化结果，返回 ``{model: OCRResult}`` 映射。
        用于多模型融合（逐字段取置信度最高者）。

        Args:
            url: 可公开访问的图片地址。
            models: 依次使用的 PaddleOCR 模型名列表。

        Returns:
            模型名到识别结果的映射。

        """
        results: dict[str, OCRResult] = {}
        for model in models:
            try:
                results[model] = await self._recognize(file_url=url, model=model)
            except OCRError as exc:
                logger.warning("模型 %s 识别失败，跳过: %s", model, exc)
        return results

    async def recognize_bytes(self, data: bytes, *, suffix: str = ".png") -> OCRResult:
        """识别内存中的图片字节。

        通过临时文件提交给云端服务，调用后自动清理。

        Args:
            data: 图片二进制内容。
            suffix: 临时文件后缀，用于推断图片格式。

        Returns:
            规范化的识别结果。

        """
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
            temp.write(data)
            temp_path = temp.name
        try:
            return await self._recognize(file_path=temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)


__all__ = ["OCRClient"]
