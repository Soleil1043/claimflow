"""Claimflow 设计系统（Phase 6 / D030，Phase 6.5 / D031 视觉深化）。

两个 Gradio 应用的共享主题与 CSS。

设计来源：Apple Design 方法论可移植到 Web 的静态部分 + impeccable 打磨原则——
- 排版：系统字体栈 + 尺寸分级 tracking（大标题负、小字正）；区块标题 kicker 体系；数字 tabular-nums
- 色彩：Apple 系统调色板（蓝 #007AFF / 绿 #34C759 / 橙 #FF9500 / 红 #FF3B30）；
  拒绝纯灰死板背景（页面顶部极淡蓝色 radial wash）
- 材质：半透明浮层 chrome（backdrop-filter），内容从其下滚过；分层阴影（--cf-shadow-1/2/3）
- 动效：:active scale(0.97)/100ms 按压即时反馈；cubic-bezier(0.32, 0.72, 0, 1) 标准缓动；
  入场 cf-rise/cf-fade（ease-out-quint，交错延迟）；全部走 prefers-reduced-motion 降级
- 反馈：四态分级（status/completion/warning/error）语义化 pill
- 细节：::selection 选区、自定义滚动条、隐藏 Gradio footer、placeholder 对比度、
  表格行 hover、输入焦点光环、primary 按钮 hover 升起

workbench/app/globals.css 以 Tailwind v4 @theme 定义同名同值令牌，双栈同源。
D031 新增变量均为派生令牌，TOKENS 核心值不动，契约不受影响。
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
# 共享 CSS（两个 Gradio 应用经 launch(css=APP_CSS) 注入）
# ---------------------------------------------------------------------------

APP_CSS = f"""
/* ===== Claimflow 设计系统（D030 / D031 视觉深化） ===== */
:root {{
  --cf-blue: {TOKENS["color_blue"]};
  --cf-blue-dark: {TOKENS["color_blue_dark"]};
  --cf-blue-soft: #EAF3FF;
  --cf-blue-glow: rgba(0, 122, 255, 0.14);
  --cf-green: {TOKENS["color_green"]};
  --cf-orange: {TOKENS["color_orange"]};
  --cf-red: {TOKENS["color_red"]};
  --cf-bg: {TOKENS["color_bg"]};
  --cf-surface: {TOKENS["color_surface"]};
  --cf-surface-2: #FBFBFD;
  --cf-text: {TOKENS["color_text"]};
  --cf-text-2: {TOKENS["color_text_secondary"]};
  --cf-hairline: {TOKENS["color_hairline"]};
  --cf-ease: {TOKENS["ease_standard"]};
  --cf-ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --cf-shadow-1: 0 1px 2px rgba(0, 0, 0, 0.04), 0 1px 1px rgba(0, 0, 0, 0.02);
  --cf-shadow-2: 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 24px rgba(16, 24, 40, 0.06);
  --cf-shadow-3: 0 4px 12px rgba(0, 0, 0, 0.06), 0 16px 40px rgba(16, 24, 40, 0.10);
  --cf-radius-card: {TOKENS["radius_card"]};
  --cf-radius-bubble: {TOKENS["radius_bubble"]};
  --cf-radius-control: {TOKENS["radius_control"]};
}}

/* 排版：层级 = 字重 + 字号 + 行距；大标题负 tracking，正文 0；数字 tabular 对齐 */
.gradio-container {{
  font-family: {TOKENS["font_stack"]};
  letter-spacing: 0;
  color: var(--cf-text);
  max-width: 1080px !important;
  background:
    radial-gradient(1100px 480px at 50% -120px, rgba(0, 122, 255, 0.055), transparent 70%),
    var(--cf-bg);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
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

/* ===== 区块标题体系（kicker 大写小字 + 蓝色短划线 + 标题）===== */
.cf-section {{ margin: 20px 0 10px; }}
.cf-kicker {{
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--cf-text-2);
}}
.cf-kicker::before {{
  content: "";
  display: inline-block;
  width: 14px;
  height: 2px;
  border-radius: 999px;
  background: var(--cf-blue);
  margin-right: 8px;
  vertical-align: 3px;
}}
.cf-h2 {{
  margin-top: 4px;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.015em;
  line-height: 1.2;
  color: var(--cf-text);
}}
.cf-h3 {{
  margin-top: 4px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--cf-text);
}}

/* ===== 品牌块（与 workbench 导航 logo 同语言）===== */
.cf-logo {{
  width: 34px;
  height: 34px;
  border-radius: 9px;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #007AFF, #5CA8FF);
  color: #FFFFFF;
  font-size: 14px;
  font-weight: 800;
  letter-spacing: 0.02em;
  box-shadow: 0 2px 8px rgba(0, 122, 255, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.25);
  user-select: none;
}}

/* ===== 入场动效（交错延迟；poll 高频更新区禁用，防 2s 重放）===== */
@keyframes cf-rise {{
  from {{ opacity: 0; transform: translateY(10px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
@keyframes cf-fade {{
  from {{ opacity: 0; }}
  to {{ opacity: 1; }}
}}
.cf-rise {{ animation: cf-rise 420ms var(--cf-ease-out) both; }}
.cf-rise-2 {{ animation: cf-rise 420ms var(--cf-ease-out) 60ms both; }}
.cf-rise-3 {{ animation: cf-rise 420ms var(--cf-ease-out) 120ms both; }}
.cf-fade {{ animation: cf-fade 500ms var(--cf-ease-out) both; }}

/* ===== 按压即时反馈（pointer-down 高亮，100ms）===== */
.gradio-container button {{
  transition: transform 100ms var(--cf-ease), background-color 150ms var(--cf-ease),
    box-shadow 150ms var(--cf-ease), border-color 150ms var(--cf-ease);
}}
.gradio-container button:active {{
  transform: scale({TOKENS["press_scale"]});
  transition-duration: 100ms;
}}
.gradio-container .gr-button-primary {{ box-shadow: 0 1px 2px rgba(0, 122, 255, 0.25); }}
.gradio-container .gr-button-primary:hover {{
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(0, 122, 255, 0.32);
}}
.gradio-container .gr-button-primary:active {{ transform: scale(0.97) translateY(0); }}
.gradio-container button:focus-visible,
.gradio-container a:focus-visible,
.gradio-container input:focus-visible,
.gradio-container textarea:focus-visible {{
  outline: 2px solid var(--cf-blue);
  outline-offset: 2px;
}}

/* ===== 输入焦点光环与 placeholder 对比度 ===== */
.gradio-container input:not([type="checkbox"]):not([type="radio"]):focus,
.gradio-container textarea:focus {{
  border-color: var(--cf-blue) !important;
  box-shadow: 0 0 0 3px var(--cf-blue-glow) !important;
}}
.gradio-container input::placeholder,
.gradio-container textarea::placeholder {{
  color: var(--cf-text-2);
  opacity: 1;
}}

/* ===== 细节：选区 / 滚动条 / footer ===== */
.gradio-container ::selection {{ background: rgba(0, 122, 255, 0.18); }}
.gradio-container * {{
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 0, 0, 0.18) transparent;
}}
.gradio-container ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
.gradio-container ::-webkit-scrollbar-thumb {{
  background: rgba(0, 0, 0, 0.18);
  border-radius: 999px;
}}
.gradio-container ::-webkit-scrollbar-thumb:hover {{ background: rgba(0, 0, 0, 0.30); }}
.gradio-container ::-webkit-scrollbar-track {{ background: transparent; }}
.gradio-container .footer,
.gradio-container footer {{ display: none !important; }}

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
.cf-status-dot.warn {{ background: var(--cf-orange); animation: cf-pulse 1.2s ease-in-out infinite; }}
.cf-status-dot.err {{ background: var(--cf-red); }}
@keyframes cf-pulse {{
  0%, 100% {{ opacity: 1; }}
  50% {{ opacity: 0.35; }}
}}

/* ===== 卡片与分组（微渐变表面 + 分层阴影）===== */
.cf-card {{
  background: linear-gradient(180deg, var(--cf-surface) 0%, var(--cf-surface-2) 100%);
  border: 1px solid var(--cf-hairline);
  border-radius: var(--cf-radius-card);
  box-shadow: var(--cf-shadow-2);
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

/* ===== KPI 大数字卡（评测台摘要；hover 升起）===== */
.cf-kpi-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin: 8px 0 4px;
}}
.cf-kpi {{
  background: linear-gradient(180deg, var(--cf-surface) 0%, var(--cf-surface-2) 100%);
  border: 1px solid var(--cf-hairline);
  border-radius: var(--cf-radius-card);
  padding: 14px 16px 12px;
  box-shadow: var(--cf-shadow-1);
  transition: transform 200ms var(--cf-ease), box-shadow 200ms var(--cf-ease);
}}
.cf-kpi:hover {{ transform: translateY(-2px); box-shadow: var(--cf-shadow-3); }}
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

/* ===== mini 进度条（分类明细表；通过率语义配色）===== */
.cf-bar {{
  display: inline-block;
  width: 110px;
  height: 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.07);
  overflow: hidden;
  vertical-align: middle;
  margin-left: 8px;
}}
.cf-bar > i {{
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--cf-blue), #5CA8FF);
}}
.cf-bar.ok > i {{ background: linear-gradient(90deg, #34C759, #7DDC8F); }}
.cf-bar.warn > i {{ background: linear-gradient(90deg, #FF9500, #FFB84D); }}
.cf-bar.err > i {{ background: linear-gradient(90deg, #FF3B30, #FF7A73); }}

/* ===== 表格：行 hover + 表头精修 ===== */
.gradio-container table tbody tr {{ transition: background 150ms var(--cf-ease); }}
.gradio-container table tbody tr:hover {{ background: rgba(0, 122, 255, 0.04); }}
.gradio-container .gr-dataframe thead th {{
  font-size: 12px;
  font-weight: 600;
  color: var(--cf-text-2);
  letter-spacing: 0.02em;
}}

/* ===== Tabs：胶囊分段（评测台 趋势/历史报告 分区）===== */
.gradio-container .tabs .tab-nav {{
  border-bottom: 1px solid var(--cf-hairline);
  gap: 4px;
  padding-bottom: 8px;
}}
.gradio-container .tabs .tab-nav button {{
  background: transparent !important;
  border: 1px solid transparent !important;
  border-radius: 999px !important;
  padding: 6px 16px !important;
  font-size: 13.5px !important;
  font-weight: 600 !important;
  color: var(--cf-text-2) !important;
  transition: all 150ms var(--cf-ease) !important;
}}
.gradio-container .tabs .tab-nav button:hover {{ color: var(--cf-text) !important; }}
.gradio-container .tabs .tab-nav button.selected {{
  background: var(--cf-surface) !important;
  border-color: var(--cf-hairline) !important;
  color: var(--cf-text) !important;
  box-shadow: var(--cf-shadow-1) !important;
}}

/* 运行日志：等宽暗色终端 */
.cf-log textarea, .cf-log pre {{
  font-family: {TOKENS["font_mono"]} !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
  background: #1D1D1F !important;
  color: #E8E8ED !important;
  border: 1px solid rgba(0, 0, 0, 0.25) !important;
  border-radius: var(--cf-radius-control) !important;
  box-shadow: var(--cf-shadow-2) !important;
}}

/* ===== 聊天气泡（用户右/助手左，18px 连续圆角；容器去卡片化——气泡直接浮于底色）===== */
.chatbot {{
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
}}
.chatbot .message-row {{ margin: 10px 0; }}
.chatbot .message {{
  border-radius: var(--cf-radius-bubble) !important;
  border: 1px solid var(--cf-hairline) !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
  line-height: 1.65;
}}
.chatbot .message.user, .chatbot .message.user .message-content {{
  background: linear-gradient(135deg, #007AFF, #3D95FF) !important;
  color: #FFFFFF !important;
  border-color: transparent !important;
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
  box-shadow: var(--cf-shadow-2);
}}

/* 示例问题 chips（Gradio 6 渲染为 .gallery-item 按钮，需提高特异性覆盖默认透明样式） */
.gradio-container .cf-chips .gallery-item,
.gradio-container .cf-chips button {{
  border-radius: 999px !important;
  border: 1px solid var(--cf-hairline) !important;
  background: var(--cf-surface) !important;
  color: var(--cf-text) !important;
  font-size: 13px !important;
  padding: 6px 14px !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
  transition: all 150ms var(--cf-ease) !important;
}}
.gradio-container .cf-chips .gallery-item:hover,
.gradio-container .cf-chips button:hover {{
  border-color: rgba(0, 122, 255, 0.5) !important;
  color: var(--cf-blue-dark) !important;
  background: #EAF3FF !important;
  transform: translateY(-1px) !important;
  box-shadow: 0 2px 8px rgba(0, 122, 255, 0.12) !important;
}}

/* ===== 无障碍降级 ===== */
@media (prefers-reduced-motion: reduce) {{
  .gradio-container button {{ transition: none; }}
  .gradio-container button:active {{ transform: none; }}
  .gradio-container .gr-button-primary:hover {{ transform: none; }}
  .cf-progress > div {{ transition: none; }}
  .cf-rise, .cf-rise-2, .cf-rise-3, .cf-fade {{ animation: none; }}
  .cf-kpi {{ transition: none; }}
  .cf-kpi:hover {{ transform: none; }}
  .cf-status-dot.warn {{ animation: none; }}
  .gradio-container table tbody tr {{ transition: none; }}
}}
@media (prefers-reduced-transparency: reduce) {{
  .cf-header {{ background: var(--cf-bg); -webkit-backdrop-filter: none; backdrop-filter: none; }}
  .cf-composer {{ background: var(--cf-surface); -webkit-backdrop-filter: none; backdrop-filter: none; }}
}}
"""
