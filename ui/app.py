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

    async def upload_image(self, conversation_id: str, file_path: str) -> dict:
        """A07 上传图片材料（vision OCR + Mock 兜底）。"""
        import pathlib

        with open(file_path, "rb") as f:
            resp = await self._http.post(
                f"/api/v1/conversations/{conversation_id}/images",
                files={"file": (pathlib.Path(file_path).name, f)},
            )
        resp.raise_for_status()
        return resp.json()


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


async def upload_image(file_path: str | None, history: list, session_state: dict) -> tuple[list, dict]:
    """上传图片回调：A07 OCR 识别结果以对话消息展示（F12/F13）。"""
    if not file_path:
        return history, session_state
    history = history + [{"role": "user", "content": f"📎 已上传图片材料：{file_path}"}]
    try:
        conversation_id = await _ensure_conversation(session_state)
        if conversation_id is None:
            raise httpx.HTTPError("会话创建失败")
        result = await _client.upload_image(conversation_id, file_path)
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

    source_label = "🔍 真实识别（vision）" if result.get("source") == "vision" else "🧪 Mock 兜底数据"
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


def build_ui() -> gr.Blocks:
    """组装界面。"""
    with gr.Blocks(title="保险理赔智能助手") as demo:
        gr.Markdown("# 🛡️ 保险理赔智能助手\n多智能体理赔对话系统演示（Phase 1：单 Agent ReAct）")
        session_state = gr.State({})

        chatbot = gr.Chatbot(
            value=[{"role": "assistant", "content": _WELCOME}],
            height=480,
        )
        with gr.Row():
            msg = gr.Textbox(
                placeholder="输入您的问题，如：保单 POL-2025-0001 住院花了15800元能赔多少？",
                scale=5,
                show_label=False,
                autofocus=True,
            )
            submit = gr.Button("发送", variant="primary", scale=1)
        with gr.Row():
            upload = gr.File(
                label="上传诊断证明/发票图片（OCR 识别材料字段）",
                file_types=["image"],
                scale=5,
            )
            upload_btn = gr.Button("📎 识别材料", scale=1)
        with gr.Row():
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
            reset = gr.Button("🔄 新会话")

        async def respond(message: str, history: list, state: dict) -> tuple[str, list, dict]:
            if not message.strip():
                return "", history, state
            history = history + [{"role": "user", "content": message}]
            reply = await chat(message, history, state)
            history = history + [{"role": "assistant", "content": reply}]
            return "", history, state

        submit.click(respond, [msg, chatbot, session_state], [msg, chatbot, session_state])
        msg.submit(respond, [msg, chatbot, session_state], [msg, chatbot, session_state])
        upload_btn.click(upload_image, [upload, chatbot, session_state], [chatbot, session_state])
        reset.click(new_conversation, outputs=[chatbot, session_state])
    return demo


demo = build_ui()

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=int(os.getenv("GRADIO_PORT", "7860")))
