"""界面验收脚本（T014/T023）：模拟界面回调真实走通完整链路。

覆盖（F13）：
- 对话链路：BackendClient + chat 回调 → 后端 API → 工具链 → 合规回答（T014 原有）
- 上传链路：upload_image 回调 → A07 → OCR（vision 或 mock_fallback）→ 结果展示（T023 补齐）

前置：后端已启动（uv run uvicorn app.main:app --port 8000）；.env 配置真实 API Key。
"""

import asyncio
import io
from pathlib import Path

import ui.app as ui_app

# PIL 生成的诊断证明测试图（与 Mock 人物张伟的阑尾炎场景一致）
_CERTIFICATE_LINES = [
    "杭州市第一人民医院  诊断证明书",
    "姓名：张伟    性别：男    年龄：34 岁",
    "诊断：急性阑尾炎（ICD-10: K35）",
    "住院日期：2026-08-10 至 2026-08-15",
    "费用合计（元）：15800.00",
]


def _make_certificate_png(tmp_path: Path) -> Path:
    """PIL 生成诊断证明图片，保存到临时文件。"""
    from PIL import Image, ImageDraw, ImageFont

    width, height = 760, 420
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        font_title = ImageFont.truetype("msyh.ttc", 28)
        font_body = ImageFont.truetype("msyh.ttc", 22)
    except OSError:
        font_title = font_body = ImageFont.load_default()

    draw.text((width // 2, 30), _CERTIFICATE_LINES[0], fill="black", font=font_title, anchor="ma")
    for i, line in enumerate(_CERTIFICATE_LINES[1:], start=1):
        draw.text((60, 80 + i * 48), line, fill="black", font=font_body)
    draw.rectangle([20, 20, width - 20, height - 20], outline="black", width=2)

    out = tmp_path / "diagnosis_certificate.png"
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    out.write_bytes(buffer.getvalue())
    return out


async def main() -> None:
    session_state: dict = {}
    history: list = []

    # ===== 第一轮：对话链路（会话创建 + 工具链 + 合规回答） =====
    reply = await ui_app.chat("保单 POL-2025-0001 住院花了15800元能赔多少？", history, session_state)
    print("=== 第一轮回复（前 300 字）===")
    print(reply[:300])
    assert "conversation_id" in session_state, "会话未创建"
    assert "4,640" in reply or "4640" in reply, "回复中未包含预期赔付金额"
    assert "policy_query" in reply and "claim_calculator" in reply, "工具轨迹缺失"
    history = history + [
        {"role": "user", "content": "保单 POL-2025-0001 住院花了15800元能赔多少？"},
        {"role": "assistant", "content": reply},
    ]

    # ===== 第二轮：多轮上下文 =====
    reply2 = await ui_app.chat("那份保单的免赔额是多少？", history, session_state)
    print("=== 第二轮回复（前 150 字）===")
    print(reply2[:150])
    assert "10,000" in reply2 or "10000" in reply2 or "1万" in reply2.replace(" ", ""), "第二轮未正确引用上下文"

    # ===== 第三轮：上传链路（upload_image 回调 → A07 → OCR） =====
    cert_path = _make_certificate_png(Path("data"))
    history2, session_state2 = await ui_app.upload_image(str(cert_path), history, session_state)
    print("=== 上传识别展示（最后一条助手消息）===")
    upload_reply = history2[-1]["content"]
    print(upload_reply)
    assert upload_reply.startswith("📋"), f"上传回调异常：{upload_reply[:100]}"
    assert "材料识别结果" in upload_reply
    # vision 正常 → 张伟四字段；vision 故障 → mock_fallback（同为张伟场景）
    assert "张伟" in upload_reply, "识别结果未包含患者姓名"
    assert "15800" in upload_reply or "15,800" in upload_reply, "识别结果未包含金额"
    assert "vision" in upload_reply or "Mock" in upload_reply, "来源标记缺失"
    # 清理临时图片
    cert_path.unlink(missing_ok=True)

    print("T023 界面验收通过：对话链路 + 多轮上下文 + 上传 OCR 链路，F13 完整")


asyncio.run(main())
