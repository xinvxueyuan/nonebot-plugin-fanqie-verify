"""OCR 识别服务自定义异常。

将 PaddleOCR 官方 API 客户端抛出的各类异常归一化为插件可控的
异常层级，方便上层捕获并做重试、提示或审计。

"""

from __future__ import annotations


class OCRError(Exception):
    """OCR 识别错误基类。

    Args:
        message: 人类可读的错误描述。

    """


class OCRNotConfiguredError(OCRError):
    """未配置 OCR 凭据时抛出。

    当 ``fanqie_ocr_api_token`` 为空且环境变量 ``PADDLEOCR_ACCESS_TOKEN``
    也不存在时抛出。

    """


class OCRAuthError(OCRError):
    """认证失败：令牌无效或已被禁用。"""


class OCRRateLimitError(OCRError):
    """触发云端限流，需稍后重试。"""


class OCRServiceUnavailableError(OCRError):
    """云端服务不可用（如 5xx 或维护中）。"""


class OCRNetworkError(OCRError):
    """网络层错误：连接失败、DNS 解析失败等。"""


class OCRTimeoutError(OCRError):
    """请求或轮询超时。"""


class OCRJobFailedError(OCRError):
    """云端任务失败（结果解析错误、任务被拒绝等）。"""


class OCRInvalidResponseError(OCRError):
    """云端返回的数据结构不符合预期。"""


class OCRBadRequestError(OCRError):
    """请求参数非法（如图片无法解码）。"""
