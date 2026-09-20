"""OCR 识别服务层接口。

在 :class:`OCRClient` 之上提供稳定的业务入口，并缓存客户端实例以
复用连接。后续信息提取（FR3）将在识别结果之上继续处理。

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .client import OCRClient

if TYPE_CHECKING:
    from pathlib import Path

    from .models import OCRResult

_client: OCRClient | None = None


def get_ocr_client() -> OCRClient:
    """返回全局 OCR 客户端单例。

    客户端内部在每次识别时创建短生命周期会话，单例本身无状态。

    Returns:
        OCR 客户端实例。

    """
    global _client
    if _client is None:
        _client = OCRClient()
    return _client


async def recognize_file(path: str | Path) -> OCRResult:
    """识别本地图片文件。

    Args:
        path: 本地图片文件路径。

    Returns:
        规范化的 OCR 识别结果。

    """
    return await get_ocr_client().recognize_path(path)


async def recognize_image_url(
    url: str,
    *,
    model: str | None = None,
) -> OCRResult:
    """识别图片 URL。

    Args:
        url: 可公开访问的图片地址。
        model: 使用的 PaddleOCR 模型名（默认配置）。

    Returns:
        规范化的 OCR 识别结果。

    """
    return await get_ocr_client().recognize_url(url, model=model)


async def recognize_image_url_multi(
    url: str,
    *,
    models: list[str] | tuple[str, ...],
) -> dict[str, OCRResult]:
    """用多个模型并行识别图片 URL，返回模型名到结果的映射。

    Args:
        url: 可公开访问的图片地址。
        models: 依次使用的 PaddleOCR 模型名列表。

    Returns:
        模型名到 OCR 结果的映射。

    """
    return await get_ocr_client().recognize_multi_url(url, models=models)


async def recognize_image_bytes(data: bytes, *, suffix: str = ".png") -> OCRResult:
    """识别内存中的图片字节。

    Args:
        data: 图片二进制内容。
        suffix: 临时文件后缀，用于推断图片格式。

    Returns:
        规范化的 OCR 识别结果。

    """
    return await get_ocr_client().recognize_bytes(data, suffix=suffix)


def _reset_client() -> None:
    """重置全局客户端实例（主要供测试使用）。"""
    global _client
    _client = None


__all__ = [
    "OCRClient",
    "get_ocr_client",
    "recognize_file",
    "recognize_image_bytes",
    "recognize_image_url",
    "recognize_image_url_multi",
]
