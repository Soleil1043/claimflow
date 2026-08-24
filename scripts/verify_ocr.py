"""F12 验收脚本：OCR 图片上传（真实 vision API + Mock 兜底演示）。

验收标准（tasks.md T020）：
- 上传诊断证明图片返回结构化字段（姓名/诊断/金额/日期）+ source: vision
- 模拟 vision API 异常时返回 source: mock_fallback 且接口不报错
- 非图片文件 422（已由单测覆盖，此脚本补真实 API 两场景）

前置：.env 配置真实 LLM API Key（同 T012）。
测试图：PIL 现场生成一张诊断证明图片（白底黑字）。
"""

from __future__ import annotations

import asyncio
import base64
import io

from PIL import Image, ImageDraw, ImageFont

from tools.medical.ocr_extract import OcrExtractTool

# 诊断证明图片内容（与 Mock 人物张伟的阑尾炎场景一致）
CERTIFICATE_LINES = [
    "杭州市第一人民医院  诊断证明书",
    "姓名：张伟    性别：男    年龄：34 岁",
    "诊断：急性阑尾炎（ICD-10: K35）",
    "住院日期：2026-08-10 至 2026-08-15",
    "费用合计（元）：15800.00",
    "医师签名：王医生",
]


def _make_certificate_png() -> str:
    """PIL 生成诊断证明图片，返回 base64。"""
    width, height = 760, 420
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    # 字体：Windows 自带微软雅黑，缺失时回退默认位图字体
    try:
        font_title = ImageFont.truetype("msyh.ttc", 28)
        font_body = ImageFont.truetype("msyh.ttc", 22)
    except OSError:
        font_title = font_body = ImageFont.load_default()

    draw.text((width // 2, 30), CERTIFICATE_LINES[0], fill="black", font=font_title, anchor="ma")
    for i, line in enumerate(CERTIFICATE_LINES[1:], start=1):
        draw.text((60, 80 + i * 48), line, fill="black", font=font_body)
    draw.rectangle([20, 20, width - 20, height - 20], outline="black", width=2)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


async def main() -> None:
    image_b64 = _make_certificate_png()
    print(f"已生成诊断证明测试图（base64 {len(image_b64)} 字符）")
    print("图片内容：")
    for line in CERTIFICATE_LINES:
        print(f"  {line}")
    print()

    # ===== 场景 1：真实 vision OCR =====
    print("===== 场景 1：真实 vision OCR（期望 source=vision） =====")
    tool = OcrExtractTool()
    result = await tool.execute({"image_base64": image_b64})
    data = result.data
    print(
        f"识别结果：姓名={data.get('patient_name')} | 诊断={data.get('diagnosis')} | "
        f"金额={data.get('amount')} | 日期={data.get('date')} | source={data.get('source')}"
    )

    assert data.get("source") == "vision", f"期望 vision，实际 {data.get('source')}"
    assert data.get("patient_name") == "张伟", f"姓名识别错误：{data.get('patient_name')}"
    assert "阑尾炎" in str(data.get("diagnosis")), f"诊断识别错误：{data.get('diagnosis')}"
    assert data.get("amount") == 15800.0, f"金额识别错误：{data.get('amount')}"
    assert data.get("date") == "2026-08-10", f"日期识别错误：{data.get('date')}"

    # ===== 场景 2：vision API 异常 → Mock 兜底 =====
    print("\n===== 场景 2：模拟 vision API 异常（期望 source=mock_fallback，不报错） =====")
    import services.llm.client as llm_client_module

    original = llm_client_module.get_vision_model

    def _broken_vision(*args: object, **kwargs: object):
        raise RuntimeError("模拟 vision API 故障")

    llm_client_module.get_vision_model = _broken_vision  # type: ignore[assignment]
    try:
        result2 = await tool.execute({"image_base64": image_b64})
    finally:
        llm_client_module.get_vision_model = original  # type: ignore[assignment]

    data2 = result2.data
    print(
        f"兜底结果：姓名={data2.get('patient_name')} | 诊断={data2.get('diagnosis')} | "
        f"金额={data2.get('amount')} | 日期={data2.get('date')} | source={data2.get('source')}"
    )
    assert result2.success is True, "vision 故障时接口不应报错"
    assert data2.get("source") == "mock_fallback"
    assert data2.get("patient_name") == "张伟"  # 预置 Mock 数据

    print("\nF12 验收通过：真实图片字段识别正确（source=vision）；vision 异常返回 Mock 兜底且接口不报错")


asyncio.run(main())
