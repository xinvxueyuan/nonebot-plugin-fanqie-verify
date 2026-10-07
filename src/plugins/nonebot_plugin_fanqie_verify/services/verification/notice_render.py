"""群公告相关命令的图片渲染（``nonebot-plugin-htmlkit``）。

用户 2026-10-07 要求「获取群公告列表 handle（使用 htmlkit 插件）」—— 公告 id 在
QQ 客户端是隐藏的，必须出图把 id 打出来管理员才好绑定。

## 三条必须遵守的约定（与抽奖插件 CRUD 渲染同一套踩坑结论）

1. **失败一律回退文本，绝不抛**：渲染是锦上添花，渲染不通不能表现成「命令没反应」。
   :func:`render_card` 返回 ``None`` 时由调用方发纯文本。
2. **所有用户可见文本必须 ``html.escape``**：公告正文是**群成员/管理员随意写**的，
   里面有 ``<``/``&``/引号时不转义会破坏 HTML（轻则版面乱掉，重则注入标记）。
3. **别用浏览器语义写 CSS**：htmlkit 用自带原生渲染器（litehtml），**不是 Chromium**。
   实测 ``max_width`` 是**上限而非缩放器**（内容更宽会被压扁）、flex 的 ``gap``
   **不生效**；所以宽度/字号/间距全部显式写死，间距用 ``margin``。

另注意：**必须显式声明 CJK 字体栈** —— 宿主默认 ``sans-serif`` 若不含中文字形，
渲染出来全是方块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import html as html_mod
import logging
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger("nonebot_plugin_fanqie_verify")

#: 卡片宽度；``MAX_WIDTH`` 是 htmlkit 的上限，必须 ≥ 卡片宽 + padding（否则被压扁）。
CARD_WIDTH: Final[int] = 620
MAX_WIDTH: Final[int] = 700

#: 渲染放大倍率。
#:
#: ⚠️ 2026-10-07 实测（别再走弯路）：**htmlkit 的 ``dpi`` 在本版本里对输出像素零影响**
#: （96 / 144 / 192 / 288 都产出同样尺寸），``max_width`` 只是**上限**不是缩放器；
#: 输出宽度 = 内容自然宽度。所以「图太小」只能靠**放大内容本身**解决 ——
#: 本常量把卡片宽度与全部字号同乘一个倍率。
#: 实测：倍率 1.0 → 700x230；字号 2x → 702x469；CARD_WIDTH 1240 + 2x → 1322x353。
SCALE: Final[float] = 1.6


def _px(value: float) -> str:
    """按 :data:`SCALE` 放大像素值（返回 CSS 字符串）。"""
    return f"{round(value * SCALE)}px"


#: 卡片宽度按倍率放大后参与渲染（``MAX_WIDTH`` 必须同步放大，否则被压回原尺寸）。
RENDER_CARD_WIDTH: Final[int] = round(CARD_WIDTH * SCALE)
RENDER_MAX_WIDTH: Final[int] = round(MAX_WIDTH * SCALE)

#: 一张图最多画多少行（超出只提示条数 —— 否则公告多了能生成几十米长的图）。
MAX_ROWS: Final[int] = 40

#: 语气 → 主色（标题左边框色）。
TONE_COLORS: Final[dict[str, str]] = {
    "info": "#2563eb",
    "success": "#16a34a",
    "error": "#dc2626",
    "warn": "#d97706",
}

_CSS: Final[str] = f"""
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: {_px(16)}; background: #f1f5f9;
    font-family: "WenQuanYi Zen Hei", "Noto Sans CJK SC", "Microsoft YaHei",
                 "PingFang SC", sans-serif;
  }}
  .card {{
    width: {_px(CARD_WIDTH)}; background: #ffffff; border-radius: {_px(12)};
    padding: {_px(22)} {_px(24)}; border: 1px solid #e2e8f0;
  }}
  .title {{
    font-size: {_px(26)}; font-weight: bold; color: #0f172a;
    padding-left: {_px(12)}; border-left: {_px(6)} solid __COLOR__;
    margin-bottom: {_px(16)};
  }}
  .line {{
    font-size: {_px(20)}; color: #334155; line-height: {_px(30)};
    margin-bottom: {_px(6)};
  }}
  .row {{ margin-bottom: {_px(14)}; }}
  .id {{
    font-size: {_px(20)}; color: #0f172a; font-family: monospace;
    word-wrap: break-word;
  }}
  .badge {{
    display: inline-block; font-size: {_px(17)}; color: #1d4ed8;
    background: #eff6ff; border-radius: {_px(6)}; padding: {_px(2)} {_px(10)};
    margin-left: {_px(10)};
  }}
  .badge.warn {{ color: #b45309; background: #fef3c7; }}
  .preview {{
    font-size: {_px(19)}; color: #475569; line-height: {_px(28)};
    margin-top: {_px(4)};
  }}
  .meta {{ font-size: {_px(16)}; color: #94a3b8; margin-top: {_px(2)}; }}
  .footer {{
    margin-top: {_px(16)}; padding-top: {_px(12)}; border-top: 1px dashed #cbd5e1;
    font-size: {_px(17)}; color: #64748b; line-height: {_px(26)};
  }}
"""

_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><style>{css}</style></head>
<body><div class="card">
  <div class="title">{title}</div>
  {body}
  {footer}
</div></body>
</html>
"""


@dataclass(frozen=True, slots=True)
class Row:
    """列表里的一行。

    Attributes:
        id_text: 主文本（公告 id）。
        badge: 角标（如「需确认」/「未开确认」），空则不画。
        badge_warn: 角标是否用警示色。
        preview: 正文预览。
        meta: 次要信息（发布时间等）。

    """

    id_text: str
    badge: str = ""
    badge_warn: bool = False
    preview: str = ""
    meta: str = ""


@dataclass(frozen=True, slots=True)
class Card:
    """一张卡片的内容。"""

    title: str
    lines: Sequence[str] = field(default_factory=tuple)
    rows: Sequence[Row] = field(default_factory=tuple)
    tone: str = "info"
    footer: str | None = None


def build_html(card: Card) -> str:
    """把 :class:`Card` 变成完整 HTML。**纯函数**（便于单测；全部字段转义）。"""
    parts: list[str] = [
        f'<div class="line">{html_mod.escape(line)}</div>' for line in card.lines
    ]

    shown = list(card.rows)[:MAX_ROWS]
    for row in shown:
        badge = ""
        if row.badge:
            cls = "badge warn" if row.badge_warn else "badge"
            badge = f'<span class="{cls}">{html_mod.escape(row.badge)}</span>'
        preview = (
            f'<div class="preview">{html_mod.escape(row.preview)}</div>'
            if row.preview
            else ""
        )
        meta = (
            f'<div class="meta">{html_mod.escape(row.meta)}</div>' if row.meta else ""
        )
        parts.append(
            f'<div class="row"><div><span class="id">'
            f"{html_mod.escape(row.id_text)}</span>{badge}</div>"
            f"{preview}{meta}</div>"
        )

    hidden = len(card.rows) - len(shown)
    if hidden > 0:
        parts.append(f'<div class="line">…另有 {hidden} 条未显示</div>')
    if not parts:
        parts.append('<div class="line">（空）</div>')

    footer_html = (
        f'<div class="footer">{html_mod.escape(card.footer)}</div>'
        if card.footer
        else ""
    )
    color = TONE_COLORS.get(card.tone, TONE_COLORS["info"])
    return _TEMPLATE.format(
        css=_CSS.replace("__COLOR__", color),
        title=html_mod.escape(card.title),
        body="\n  ".join(parts),
        footer=footer_html,
    )


def _load_htmlkit() -> Any:
    """惰性取 ``html_to_pic``（单独抽出来，测试可 monkeypatch，不必真装 htmlkit）。

    ``nonebot-plugin-htmlkit`` 是**可选依赖**（extra ``htmlkit``），开发环境/CI
    不装它，所以这行 import 在静态检查里解析不到 —— 这是预期的，不是漏依赖。
    """
    import nonebot_plugin_htmlkit as _htmlkit  # pyright: ignore[reportMissingImports]

    return _htmlkit.html_to_pic


async def render_card(card: Card) -> bytes | None:
    """渲染成 PNG。**任何失败都只 warning 并返回 None**（调用方回退纯文本）。"""
    try:
        html_to_pic = _load_htmlkit()
        return await html_to_pic(build_html(card), max_width=RENDER_MAX_WIDTH)
    except Exception as exc:  # noqa: BLE001 - 渲染失败必须回退文本，绝不冒泡
        logger.warning("公告命令渲染失败，回退纯文本: %s: %s", type(exc).__name__, exc)
        return None


__all__ = [
    "CARD_WIDTH",
    "MAX_ROWS",
    "MAX_WIDTH",
    "RENDER_CARD_WIDTH",
    "RENDER_MAX_WIDTH",
    "SCALE",
    "TONE_COLORS",
    "Card",
    "Row",
    "build_html",
    "render_card",
]
