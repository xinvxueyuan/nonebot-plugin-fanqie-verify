"""公告命令图片渲染（``services/verification/notice_render.py``）的单元测试。

要点：
* **转义**：公告正文是群成员随手写的，含 ``<>`` 时必须转义（否则版面破坏/注入）；
* **截断**：公告多了不能生成几十米长的图；
* **失败回退**：htmlkit 缺失或渲染报错时 ``render_card`` 返回 ``None``（绝不抛），
  由调用方回退纯文本 —— 这条必须**走真实的 ``render_card`` 失败分支**，
  只用替身返回 None 测不出「把吞异常改成 raise」这类变异。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
    notice_render,
)


def _card(**overrides: Any) -> notice_render.Card:
    payload: dict[str, Any] = {
        "title": "本群公告列表",
        "lines": ["说明一行"],
        "rows": [
            notice_render.Row(
                id_text="123456",
                badge="可作验证公告",
                preview="公告正文",
                meta="发布 2026-10-07 18:00",
            )
        ],
        "tone": "info",
        "footer": "页脚",
    }
    payload.update(overrides)
    return notice_render.Card(**payload)


def test_build_html_escapes_all_user_visible_text() -> None:
    """标题/正文/角标/元信息里的 HTML 字符必须转义。"""
    html = notice_render.build_html(
        _card(
            title="<script>alert(1)</script>",
            lines=['a < b & "c"'],
            rows=[
                notice_render.Row(
                    id_text="<b>1</b>",
                    badge="<i>角标</i>",
                    preview="<img src=x>",
                    meta="<hr>",
                )
            ],
            footer="</div><script>bad()</script>",
        )
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img src=x&gt;" in html
    assert "&amp;" in html
    # 结构本身仍在（没有被转义掉标签结构）
    assert '<div class="row">' in html
    assert "&lt;b&gt;1&lt;/b&gt;" in html


def test_build_html_includes_ids_badges_and_rows() -> None:
    """id、角标、预览、meta 都要出现在图里（id 是这功能的全部意义）。"""
    html = notice_render.build_html(
        _card(
            rows=[
                notice_render.Row(
                    id_text="987",
                    badge="已绑定",
                    preview="预览文本",
                    meta="发布 2026-01-01",
                ),
                notice_render.Row(id_text="654", badge="未开确认", badge_warn=True),
            ]
        )
    )

    assert "987" in html and "654" in html
    assert "已绑定" in html
    assert "badge warn" in html  # 警示色角标
    assert "预览文本" in html


def test_build_html_truncates_long_lists() -> None:
    """超过上限只画 MAX_ROWS 行并提示剩余条数。"""
    rows = [
        notice_render.Row(id_text=f"id{index}")
        for index in range(notice_render.MAX_ROWS + 5)
    ]

    html = notice_render.build_html(_card(rows=rows))

    assert html.count('<div class="row">') == notice_render.MAX_ROWS
    assert "另有 5 条未显示" in html


def test_build_html_renders_empty_placeholder() -> None:
    """一行都没有时要有「（空）」占位，不能是白板。"""
    html = notice_render.build_html(notice_render.Card(title="标题", lines=[], rows=[]))

    assert "（空）" in html


def test_build_html_declares_cjk_font_stack() -> None:
    """必须显式声明中文字体栈 —— 否则渲染出来全是方块。"""
    html = notice_render.build_html(_card())

    assert "WenQuanYi Zen Hei" in html or "Noto Sans CJK SC" in html


def test_render_scales_up_card_metrics() -> None:
    """卡片必须按 SCALE 放大 —— htmlkit 的 dpi 无效，只能放大内容（用户反馈图太小）。"""
    html = notice_render.build_html(_card())

    assert notice_render.SCALE > 1.0
    assert (
        round(notice_render.MAX_WIDTH * notice_render.SCALE)
        == notice_render.RENDER_MAX_WIDTH
    )
    # 卡片宽度与字号都应带上放大后的像素值（而不是原始 px）
    assert f"width: {notice_render.RENDER_CARD_WIDTH}px" in html
    assert f"font-size: {round(26 * notice_render.SCALE)}px" in html
    assert f": {notice_render.CARD_WIDTH}px" not in html


def test_build_html_uses_tone_color() -> None:
    """语气色要落进 CSS（错误卡片与成功卡片应可区分）。"""
    error_html = notice_render.build_html(_card(tone="error"))

    assert notice_render.TONE_COLORS["error"] in error_html


@pytest.mark.asyncio
async def test_render_card_returns_png() -> None:
    """正常路径：拿到 htmlkit 渲染出的字节。"""
    captured: dict[str, Any] = {}

    async def fake_html_to_pic(html: str, *, max_width: int | None = None) -> bytes:
        captured["html"] = html
        captured["max_width"] = max_width
        return b"PNGDATA"

    notice_render._load_htmlkit = lambda: fake_html_to_pic  # type: ignore[assignment]
    try:
        result = await notice_render.render_card(_card())
    finally:
        del notice_render._load_htmlkit  # type: ignore[attr-defined]

    assert result == b"PNGDATA"
    assert "123456" in captured["html"]
    assert captured["max_width"] == notice_render.RENDER_MAX_WIDTH


@pytest.mark.asyncio
async def test_render_card_never_raises_when_htmlkit_missing() -> None:
    """**真实失败分支**：htmlkit 没装（ImportError）时必须返回 None 而不是抛。"""

    def boom() -> Any:
        raise ImportError("No module named 'nonebot_plugin_htmlkit'")

    notice_render._load_htmlkit = boom  # type: ignore[assignment]
    try:
        assert await notice_render.render_card(_card()) is None
    finally:
        del notice_render._load_htmlkit  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_render_card_never_raises_when_renderer_fails() -> None:
    """渲染器自己报错（字体/浏览器缺失等）时也必须吞掉并返回 None。"""

    async def failing(html: str, *, max_width: int | None = None) -> bytes:
        # 顺带锁定调用契约：渲染器拿到的是非空 HTML 与约定的最大宽度
        assert html
        assert max_width == notice_render.RENDER_MAX_WIDTH
        raise RuntimeError("渲染器崩了")

    notice_render._load_htmlkit = lambda: failing  # type: ignore[assignment]
    try:
        assert await notice_render.render_card(_card()) is None
    finally:
        del notice_render._load_htmlkit  # type: ignore[attr-defined]
