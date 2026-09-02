"""Gradio 演示界面（T014，F13 基础版）。

架构：界面通过 HTTP 调用 FastAPI 后端（A02 创建会话 / A06 发消息），
与容器部署形态一致（ui 与 app 可分离部署）。

启动：
    uv run uvicorn app.main:app --port 8000   # 先起后端
    uv run python ui/app.py                    # 再起界面（默认 7860）
"""

from __future__ import annotations

import os

import gradio as gr
import httpx

from ui.theme import APP_CSS, build_theme

# 后端地址：默认本机，可用环境变量覆盖（容器部署时指向 app 服务）
API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

_WELCOME = (
    "您好，我是保险理赔智能助手。您可以问我：\n"
    "- 保单查询（如「查一下保单 POL-2025-0001」）\n"
    "- 赔付金额估算（如「保单 POL-2025-0001 住院花了15800元能赔多少？」）\n"
    "- 理赔规则咨询（如「阑尾炎手术有等待期吗」）"
)


class BackendClient:
    """后端 API 客户端：会话生命周期 + 消息发送。"""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self._base, timeout=180)

    async def create_conversation(self, user_id: str = "gradio-demo") -> str:
        resp = await self._http.post("/api/v1/conversations", json={"user_id": user_id})
        resp.raise_for_status()
        return resp.json()["conversation_id"]

    async def send_message(self, conversation_id: str, content: str) -> dict:
        resp = await self._http.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": content},
        )
        resp.raise_for_status()
        return resp.json()

    async def upload_material(self, conversation_id: str, file_path: str) -> dict:
        """A07 上传材料（图片/PDF/Word，T049）。"""
        import pathlib

        with open(file_path, "rb") as f:
            resp = await self._http.post(
                f"/api/v1/conversations/{conversation_id}/materials",
                files={"file": (pathlib.Path(file_path).name, f)},
            )
        resp.raise_for_status()
        return resp.json()

    async def health(self) -> bool:
        """后端健康探测（头部状态点）。"""
        try:
            resp = await self._http.get("/health", timeout=5)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


_client = BackendClient(API_BASE)


def _format_reply(result: dict) -> str:
    """A06 响应 → 展示文本（回答 + 工具轨迹脚注）。"""
    answer = result.get("answer", "")
    tools = result.get("used_tools") or []
    if not tools:
        return answer
    lines = [answer, "", "---", "⚙️ 本轮工具调用："]
    for t in tools:
        lines.append(f"- **{t['tool']}** `{_brief(t.get('input', {}))}`")
    return "\n".join(lines)


def _brief(data: dict) -> str:
    """入参摘要（截断长值）。"""
    parts = []
    for k, v in data.items():
        s = str(v)
        parts.append(f"{k}={s[:40]}{'…' if len(s) > 40 else ''}")
    return ", ".join(parts)


async def chat(message: str, history: list, session_state: dict) -> str:
    """Gradio 聊天回调：惰性创建会话，发送消息并格式化回复。"""
    if "conversation_id" not in session_state:
        try:
            session_state["conversation_id"] = await _client.create_conversation()
        except httpx.HTTPError as exc:
            return f"⚠️ 无法连接后端服务（{API_BASE}）：{exc!r}\n请确认后端已启动。"
    try:
        result = await _client.send_message(session_state["conversation_id"], message)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            pass
        return f"⚠️ 后端处理失败：{exc.response.status_code} {detail}"
    except httpx.HTTPError as exc:
        return f"⚠️ 网络错误：{exc!r}"
    return _format_reply(result)


async def _ensure_conversation(session_state: dict) -> str | None:
    """惰性创建会话，返回会话 id 或错误提示前的 None。"""
    if "conversation_id" not in session_state:
        session_state["conversation_id"] = await _client.create_conversation()
    return session_state["conversation_id"]


async def upload_material(file_path: str | None, history: list, session_state: dict) -> tuple[list, dict]:
    """上传材料回调：A07 提取结果以对话消息展示（F12/F13，T049 扩展 PDF/Word）。"""
    if not file_path:
        return history, session_state
    history = history + [{"role": "user", "content": f"📎 已上传材料：{file_path}"}]
    try:
        conversation_id = await _ensure_conversation(session_state)
        if conversation_id is None:
            raise httpx.HTTPError("会话创建失败")
        result = await _client.upload_material(conversation_id, file_path)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            pass
        history = history + [{"role": "assistant", "content": f"⚠️ 上传失败：{exc.response.status_code} {detail}"}]
        return history, session_state
    except httpx.HTTPError as exc:
        history = history + [{"role": "assistant", "content": f"⚠️ 无法连接后端服务（{API_BASE}）：{exc!r}"}]
        return history, session_state

    source_label = {
        "vision": "🔍 真实识别（vision）",
        "text_model": "📄 文本提取（PDF/Word）",
    }.get(result.get("source"), "🧪 Mock 兜底数据")
    lines = [
        "📋 **材料识别结果**",
        f"- 患者姓名：{result.get('patient_name') or '未识别'}",
        f"- 诊断：{result.get('diagnosis') or '未识别'}",
        f"- 金额：{result.get('amount') if result.get('amount') is not None else '未识别'}",
        f"- 日期：{result.get('date') or '未识别'}",
        f"- 来源：{source_label}",
    ]
    history = history + [{"role": "assistant", "content": "\n".join(lines)}]
    return history, session_state


def new_conversation() -> tuple[list, dict]:
    """清空对话并开新会话。"""
    return [], {}


def _header_html(backend_ok: bool | None) -> str:
    """浮层 chrome 头部：品牌块 + 状态 pill（材质半透明，内容从其下滚过，T060）。"""
    if backend_ok is None:
        pill, dot, label = "muted", "warn", "检测中…"
    elif backend_ok:
        pill, dot, label = "ok", "ok", "后端已连接"
    else:
        pill, dot, label = "err", "err", "后端不可达"
    return f"""
<div class="cf-header">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
    <div style="display:flex;align-items:center;gap:12px;">
      <div class="cf-logo">CF</div>
      <div>
        <div class="cf-title">保险理赔智能助手</div>
        <div class="cf-subtitle">多智能体理赔对话系统 · Orchestrator-Worker</div>
      </div>
    </div>
    <span class="cf-pill {pill}" style="white-space:nowrap;">
      <span class="cf-status-dot {dot}"></span>{label}
    </span>
  </div>
</div>"""


async def _check_backend() -> dict:
    """页面加载：探测后端健康，更新头部状态点。"""
    ok = await _client.health()
    return gr.update(value=_header_html(ok))


def build_ui() -> gr.Blocks:
    """组装界面（T055 Apple 化 + T060 视觉深化：品牌块/状态 pill/交错入场）。

    注：Gradio 6 起 theme/css 从 Blocks 构造器移至 launch()（构造器传参仅告警不生效）。
    """
    with gr.Blocks(title="保险理赔智能助手") as demo:
        header = gr.HTML(_header_html(None))
        session_state = gr.State({})

        chatbot = gr.Chatbot(
            value=[{"role": "assistant", "content": _WELCOME}],
            height=480,
            show_label=False,
            elem_classes=["chatbot"],
        )
        with gr.Group(elem_classes=["cf-composer", "cf-rise"]):
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="输入您的问题，如：保单 POL-2025-0001 住院花了15800元能赔多少？",
                    scale=5,
                    show_label=False,
                    autofocus=True,
                )
                submit = gr.Button("发送", variant="primary", scale=1)
            with gr.Row():
                upload_btn = gr.UploadButton(
                    "📎 上传并识别材料（图片 / PDF / Word，自动提取字段）",
                    file_types=[".png", ".jpg", ".jpeg", ".webp", ".bmp", ".pdf", ".docx"],
                    scale=5,
                )
        with gr.Row():
            with gr.Column(scale=5, elem_classes=["cf-chips", "cf-rise-2"]):
                gr.Examples(
                    examples=[
                        ["保单 POL-2025-0001 住院花了15800元能赔多少？"],
                        ["查一下保单 POL-2025-0002 的保障范围"],
                        ["阑尾炎手术有等待期吗"],
                        ["理赔需要准备什么材料"],
                    ],
                    inputs=msg,
                    label="示例问题",
                )
            with gr.Column(scale=1, min_width=120, elem_classes=["cf-rise-3"]):
                reset = gr.Button("🔄 新会话")

        async def respond(message: str, history: list, state: dict) -> tuple[str, list, dict]:
            if not message.strip():
                return "", history, state
            history = history + [{"role": "user", "content": message}]
            reply = await chat(message, history, state)
            history = history + [{"role": "assistant", "content": reply}]
            return "", history, state

        demo.load(_check_backend, outputs=[header])
        submit.click(respond, [msg, chatbot, session_state], [msg, chatbot, session_state])
        msg.submit(respond, [msg, chatbot, session_state], [msg, chatbot, session_state])
        upload_btn.click(upload_material, [upload_btn, chatbot, session_state], [chatbot, session_state])
        reset.click(new_conversation, outputs=[chatbot, session_state])
    return demo


demo = build_ui()

if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        server_port=int(os.getenv("GRADIO_PORT", "7860")),
        theme=build_theme(),
        css=APP_CSS,
    )
