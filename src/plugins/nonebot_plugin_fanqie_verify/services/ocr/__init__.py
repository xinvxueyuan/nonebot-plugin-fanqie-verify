"""OCR 识别服务包。

对外暴露规范化结果模型、自定义异常与高层识别入口。

"""

from .client import OCRClient
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
from .service import (
    get_ocr_client,
    recognize_file,
    recognize_image_bytes,
    recognize_image_url,
    recognize_image_url_multi,
)

__all__ = [
    "OCRAuthError",
    "OCRBadRequestError",
    "OCRClient",
    "OCRError",
    "OCRInvalidResponseError",
    "OCRJobFailedError",
    "OCRNetworkError",
    "OCRNotConfiguredError",
    "OCRPage",
    "OCRRateLimitError",
    "OCRResult",
    "OCRServiceUnavailableError",
    "OCRTextLine",
    "OCRTimeoutError",
    "get_ocr_client",
    "recognize_file",
    "recognize_image_bytes",
    "recognize_image_url",
    "recognize_image_url_multi",
]
