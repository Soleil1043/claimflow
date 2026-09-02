"""Claimflow 设计系统（Phase 6 / D030）：两个 Gradio 应用的共享主题与 CSS。

设计来源：Apple Design 方法论可移植到 Web 的静态部分——
- 排版：系统字体栈 + 尺寸分级 tracking（大标题负、小字正）
- 色彩：Apple 系统调色板（蓝 #007AFF / 绿 #34C759 / 橙 #FF9500 / 红 #FF3B30）
- 材质：半透明浮层 chrome（backdrop-filter），内容从其下滚过
- 动效：:active scale(0.97)/100ms 按压即时反馈；cubic-bezier(0.32, 0.72, 0, 1) 标准缓动；
  prefers-reduced-motion / reduced-transparency 降级
- 反馈：四态分级（status/completion/warning/error）语义化 pill

workbench/app/globals.css 以 Tailwind v4 @theme 定义同名同值令牌，双栈同源。
"""

from __future__ import annotations

import gradio as gr

# ---------------------------------------------------------------------------
# 设计令牌（与 workbench/app/globals.css @theme 同名同值，双栈单一事实源）
# ---------------------------------------------------------------------------

TOKENS: dict[str, str] = {
    # 色彩 — Apple 系统调色板
    "color_blue": "#007AFF",
    "color_blue_dark": "#0A66C2",
    "color_green": "#34C759",
    "color_orange": "#FF9500",
    "color_red": "#FF3B30",
    "color_violet": "#AF52DE",
    # 中性色阶
    "color_bg": "#F5F5F7",
    "color_surface": "#FFFFFF",
    "color_text": "#1D1D1F",
    "color_text_secondary": "#86868B",
    "color_hairline": "rgba(0, 0, 0, 0.08)",
    # 排版
    "font_stack": (
        "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', "
        "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', "
        "Arial, sans-serif"
    ),
    "font_mono": "ui-monospace, 'SF Mono', 'JetBrains Mono', Consolas, 'Courier New', monospace",
    # 圆角（连续圆角近似）
    "radius_card": "16px",
    "radius_bubble": "18px",
    "radius_control": "10px",
    # 动效
    "ease_standard": "cubic-bezier(0.32, 0.72, 0, 1)",
    "press_scale": "0.97",
}


def build_theme() -> gr.themes.Base:
    """Gradio 主题：以 Base 为底，注入 Apple 令牌（色彩/字体/圆角/阴影）。"""
    return gr.themes.Base(
        primary_hue=gr.themes.Color(
            name="cf_blue",
            c50="#EAF3FF",
            c100="#D6E8FF",
            c200="#ADD1FF",
            c300="#85BFFF",
            c400="#5CA8FF",
            c500="#007AFF",
            c600="#0A66C2",
            c700="#0A529C",
            c800="#0A427A",
            c900="#0B335C",
            c950="#08243F",
        ),
        neutral_hue=gr.themes.Color(
            name="cf_gray",
            c50="#F5F5F7",
            c100="#E8E8ED",
            c200="#D2D2D7",
            c300="#BDBDC4",
            c400="#A1A1A8",
            c500="#86868B",
            c600="#6E6E73",
            c700="#55555A",
            c800="#3A3A3E",
            c900="#1D1D1F",
            c950="#121214",
        ),
        font=gr.themes.Font(
            [
                "-apple-system",
                "BlinkMacSystemFont",
                "SF Pro Text",
                "Segoe UI",
                "PingFang SC",
                "Hiragino Sans GB",
                "Microsoft YaHei",
                "Helvetica Neue",
                "Arial",
                "sans-serif",
            ]
        ),
        font_mono=gr.themes.Font(
            ["ui-monospace", "SF Mono", "JetBrains Mono", "Consolas", "Courier New", "monospace"]
        ),
    ).set(
        body_background_fill=TOKENS["color_bg"],
        body_text_color=TOKENS["color_text"],
        body_text_color_subdued=TOKENS["color_text_secondary"],
        block_background_fill=TOKENS["color_surface"],
        block_border_color=TOKENS["color_hairline"],
        block_radius=TOKENS["radius_card"],
        block_shadow="0 1px 2px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.05)",
        block_title_text_color=TOKENS["color_text"],
        input_radius=TOKENS["radius_control"],
        button_primary_background_fill=TOKENS["color_blue"],
        button_primary_background_fill_hover=TOKENS["color_blue_dark"],
        button_primary_text_color="#FFFFFF",
        button_secondary_background_fill=TOKENS["color_surface"],
        button_secondary_background_fill_hover="#E8E8ED",
        button_secondary_text_color=TOKENS["color_text"],
        button_secondary_border_color=TOKENS["color_hairline"],
        button_large_radius=TOKENS["radius_control"],
        button_primary_shadow="none",
        button_secondary_shadow="0 1px 2px rgba(0,0,0,0.04)",
        button_transition="all 150ms cubic-bezier(0.32, 0.72, 0, 1)",
        button_transform_active="scale(0.97)",
    )


# ---------------------------------------------------------------------------
# 共享 CSS（两个 Gradio 应用经 gr.Blocks(css=APP_CSS) 注入）
# ---------------------------------------------------------------------------

APP_CSS = f"""
/* ===== Claimflow 设计系统（D030） ===== */
:root {{
  --cf-blue: {TOKENS["color_blue"]};
  --cf-blue-dark: {TOKENS["color_blue_dark"]};
  --cf-green: {TOKENS["color_green"]};
  --cf-orange: {TOKENS["color_orange"]};
  --cf-red: {TOKENS["color_red"]};
  --cf-bg: {TOKENS["color_bg"]};
  --cf-surface: {TOKENS["color_surface"]};
  --cf-text: {TOKENS["color_text"]};
  --cf-text-2: {TOKENS["color_text_secondary"]};
  --cf-hairline: {TOKENS["color_hairline"]};
  --cf-ease: {TOKENS["ease_standard"]};
  --cf-radius-card: {TOKENS["radius_card"]};
  --cf-radius-bubble: {TOKENS["radius_bubble"]};
  --cf-radius-control: {TOKENS["radius_control"]};
}}

/* 排版：层级 = 字重 + 字号 + 行距；大标题负 tracking，正文 0 */
.gradio-container {{
  font-family: {TOKENS["font_stack"]};
  letter-spacing: 0;
  color: var(--cf-text);
  max-width: 1080px !important;
}}
.gradio-container h1 {{
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.1;
}}
.gradio-container h2, .gradio-container h3 {{
  font-weight: 600;
  letter-spacing: -0.01em;
}}
.gradio-container .prose {{ color: var(--cf-text); }}

/* ===== 按压即时反馈（pointer-down 高亮，100ms）===== */
.gradio-container button {{
  transition: transform 100ms var(--cf-ease), background-color 150ms var(--cf-ease),
    box-shadow 150ms var(--cf-ease), border-color 150ms var(--cf-ease);
}}
.gradio-container button:active {{
  transform: scale({TOKENS["press_scale"]});
  transition-duration: 100ms;
}}
.gradio-container button:focus-visible,
.gradio-container a:focus-visible,
.gradio-container input:focus-visible,
.gradio-container textarea:focus-visible {{
  outline: 2px solid var(--cf-blue);
  outline-offset: 2px;
}}

/* ===== 浮层 chrome：半透明材质，内容从其下滚过 ===== */
.cf-header {{
  position: sticky;
  top: 0;
  z-index: 40;
  margin: -16px -16px 12px;
  padding: 14px 20px;
  background: rgba(245, 245, 247, 0.72);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  backdrop-filter: blur(20px) saturate(180%);
  border-bottom: 1px solid var(--cf-hairline);
}}
.cf-header .cf-title {{
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.1;
  color: var(--cf-text);
}}
.cf-header .cf-subtitle {{
  margin-top: 2px;
  font-size: 12.5px;
  letter-spacing: 0.01em;
  color: var(--cf-text-2);
}}
.cf-status-dot {{
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  margin-right: 6px;
  vertical-align: 1px;
}}
.cf-status-dot.ok {{ background: var(--cf-green); }}
.cf-status-dot.warn {{ background: var(--cf-orange); }}
.cf-status-dot.err {{ background: var(--cf-red); }}

/* ===== 卡片与分组 ===== */
.cf-card {{
  background: var(--cf-surface);
  border: 1px solid var(--cf-hairline);
  border-radius: var(--cf-radius-card);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04), 0 8px 24px rgba(0, 0, 0, 0.05);
  padding: 16px 18px;
}}

/* 状态 pill：四态语义色 */
.cf-pill {{
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.01em;
}}
.cf-pill.ok {{ background: rgba(52, 199, 89, 0.14); color: #1F7A38; }}
.cf-pill.warn {{ background: rgba(255, 149, 0, 0.16); color: #8A5A00; }}
.cf-pill.err {{ background: rgba(255, 59, 48, 0.12); color: #B3261E; }}
.cf-pill.info {{ background: rgba(0, 122, 255, 0.12); color: var(--cf-blue-dark); }}
.cf-pill.muted {{ background: rgba(0, 0, 0, 0.06); color: var(--cf-text-2); }}

/* ===== KPI 大数字卡（评测台摘要）===== */
.cf-kpi-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin: 8px 0 4px;
}}
.cf-kpi {{
  background: var(--cf-surface);
  border: 1px solid var(--cf-hairline);
  border-radius: var(--cf-radius-card);
  padding: 14px 16px 12px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}}
.cf-kpi .k-label {{
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.02em;
  color: var(--cf-text-2);
}}
.cf-kpi .k-value {{
  margin-top: 4px;
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  color: var(--cf-text);
}}
.cf-kpi .k-foot {{ margin-top: 2px; font-size: 12px; color: var(--cf-text-2); }}

/* ===== 进度条（替代 ASCII 条）===== */
.cf-progress {{
  height: 8px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.07);
  overflow: hidden;
}}
.cf-progress > div {{
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--cf-blue), #5CA8FF);
  transition: width 300ms var(--cf-ease);
}}

/* 运行日志：等宽暗色块 */
.cf-log textarea, .cf-log pre {{
  font-family: {TOKENS["font_mono"]} !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
  background: #1D1D1F !important;
  color: #E8E8ED !important;
  border-radius: var(--cf-radius-control) !important;
}}

/* ===== 聊天气泡（用户右/助手左，18px 连续圆角）===== */
.chatbot .message-row {{ margin: 6px 0; }}
.chatbot .message {{
  border-radius: var(--cf-radius-bubble) !important;
  border: 1px solid var(--cf-hairline) !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
  line-height: 1.65;
}}
.chatbot .message.user, .chatbot .message.user .message-content {{
  background: var(--cf-blue) !important;
  color: #FFFFFF !important;
}}
.chatbot .message.bot, .chatbot .message.bot .message-content {{
  background: var(--cf-surface) !important;
  color: var(--cf-text) !important;
}}
.chatbot .message.bot .message-content a {{ color: var(--cf-blue-dark); }}

/* 输入区浮起：材质底 + 圆角 */
.cf-composer {{
  background: rgba(255, 255, 255, 0.8);
  -webkit-backdrop-filter: blur(16px) saturate(160%);
  backdrop-filter: blur(16px) saturate(160%);
  border: 1px solid var(--cf-hairline);
  border-radius: var(--cf-radius-card);
  padding: 10px 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
}}

/* 示例问题 chips */
.cf-chips button {{
  border-radius: 999px !important;
  border: 1px solid var(--cf-hairline) !important;
  background: var(--cf-surface) !important;
  color: var(--cf-text) !important;
  font-size: 13px !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
}}
.cf-chips button:hover {{ border-color: var(--cf-blue) !important; color: var(--cf-blue-dark) !important; }}

/* ===== 无障碍降级 ===== */
@media (prefers-reduced-motion: reduce) {{
  .gradio-container button {{ transition: none; }}
  .gradio-container button:active {{ transform: none; }}
  .cf-progress > div {{ transition: none; }}
}}
@media (prefers-reduced-transparency: reduce) {{
  .cf-header {{ background: var(--cf-bg); -webkit-backdrop-filter: none; backdrop-filter: none; }}
  .cf-composer {{ background: var(--cf-surface); -webkit-backdrop-filter: none; backdrop-filter: none; }}
}}
"""
