"""设计系统主题模块测试（T054 / D030）。

锁死共享设计源的关键内容：令牌齐备、主题可构建、共享 CSS 含关键选择器与无障碍媒体查询。
防止后续改动破坏双栈同源约定（ui/theme.py ↔ workbench/globals.css）。
"""

from __future__ import annotations

import gradio as gr

import ui.theme as theme

# Apple 系统调色板核心令牌（与 workbench @theme 同名同值）
_REQUIRED_TOKENS = {
    "color_blue": "#007AFF",
    "color_green": "#34C759",
    "color_orange": "#FF9500",
    "color_red": "#FF3B30",
    "color_bg": "#F5F5F7",
    "color_text": "#1D1D1F",
}


def test_tokens_contain_apple_palette() -> None:
    for key, value in _REQUIRED_TOKENS.items():
        assert theme.TOKENS.get(key) == value, f"令牌 {key} 缺失或变色"
    assert "PingFang SC" in theme.TOKENS["font_stack"]
    assert theme.TOKENS["ease_standard"] == "cubic-bezier(0.32, 0.72, 0, 1)"


def test_build_theme_returns_base_theme() -> None:
    t = theme.build_theme()
    assert isinstance(t, gr.themes.Base)


def test_app_css_contains_material_and_press_feedback() -> None:
    css = theme.APP_CSS
    # 材质：半透明浮层 chrome
    assert "backdrop-filter: blur" in css
    assert ".cf-header" in css
    # 按压即时反馈
    assert "scale(0.97)" in css
    assert ":active" in css
    # 四态 pill
    for state in (".cf-pill.ok", ".cf-pill.warn", ".cf-pill.err", ".cf-pill.info"):
        assert state in css
    # KPI 与进度条（评测台）
    assert ".cf-kpi" in css
    assert ".cf-progress" in css
    # 气泡圆角
    assert ".chatbot .message" in css


def test_app_css_contains_accessibility_fallbacks() -> None:
    css = theme.APP_CSS
    assert "prefers-reduced-motion: reduce" in css
    assert "prefers-reduced-transparency: reduce" in css
    assert "focus-visible" in css
