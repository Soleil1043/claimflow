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


def test_app_css_contains_visual_polish() -> None:
    """D031 视觉深化：分层阴影/入场动效/区块标题/细节打磨的关键选择器锁定。"""
    css = theme.APP_CSS
    # 分层阴影与 ease-out-quint 派生缓动
    for token in ("--cf-shadow-1", "--cf-shadow-2", "--cf-shadow-3", "--cf-ease-out"):
        assert token in css, f"派生令牌 {token} 缺失"
    assert "cubic-bezier(0.22, 1, 0.36, 1)" in css
    # 入场动效（cf-rise 交错 + cf-fade；warn 点脉冲）
    assert "@keyframes cf-rise" in css
    assert "@keyframes cf-fade" in css
    assert "@keyframes cf-pulse" in css
    # 区块标题体系与品牌块
    assert ".cf-kicker" in css
    assert ".cf-h2" in css
    assert ".cf-logo" in css
    # 细节打磨：选区/滚动条/footer 隐藏/焦点光环/primary hover 升起/mini 进度条/Tab 胶囊
    assert "::selection" in css
    assert "::-webkit-scrollbar" in css
    assert ".footer" in css
    assert "0 0 0 3px" in css
    assert ".gr-button-primary:hover" in css
    assert ".cf-bar" in css
    assert ".tabs .tab-nav button.selected" in css
    # 宽屏水平居中：宽度约束在内层 .main（外层容器全宽保背景，T062 修复）
    assert ".gradio-container > .main" in css
    assert "margin-left: auto !important" in css
    # 移动端适配：≤640px 头部收敛 + 行内组件纵向堆叠
    # 注意：Gradio 6 对 @media 块内规则只保留「.gradio-container-X .contain <sel>」加前缀副本，
    # 选择器不得以 .gradio-container 开头（双重前缀永不命中，T062 修复）
    assert "max-width: 640px" in css
    assert ".row > * { min-width: 100% !important; }" in css
    assert "button { transition: none; }" in css


def test_app_css_contains_accessibility_fallbacks() -> None:
    css = theme.APP_CSS
    assert "prefers-reduced-motion: reduce" in css
    assert "prefers-reduced-transparency: reduce" in css
    assert "focus-visible" in css
    # D031：动效类与 hover 升起在 reduced-motion 下全部关闭（断言渲染后的 CSS）
    assert ".cf-rise, .cf-rise-2, .cf-rise-3, .cf-fade { animation: none; }" in css
    assert ".cf-kpi:hover { transform: none; }" in css
