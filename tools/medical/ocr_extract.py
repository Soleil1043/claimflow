"""OCR 图片材料提取工具（T020，F12）。

vision 模型（deepseek-v4-flash-vision-exp，独立配置）识别图片中的结构化字段：
姓名 / 诊断 / 金额 / 日期。

降级策略（D008）：vision API 异常或输出解析失败时，返回预置 Mock 数据
（data/mock/ocr_fallback.json），source 标记 mock_fallback，接口不报错。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import Field

from app.core.logging import get_logger
from schemas.tools import ToolInput, ToolOutput
from services.llm.prompts import OCR_EXTRACT_PROMPT
from tools.base import BaseTool

log = get_logger(__name__)

_FALLBACK_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "mock" / "ocr_fallback.json"


class OcrExtractInput(ToolInput):
    """OCR 提取入参。"""

    image_base64: str = Field(description="图片的 base64 编码（不带 data: 前缀）", min_length=1)
    # 图片 MIME 类型（默认 png，用于多模态消息的 data URL）
    mime_type: str = Field(default="image/png", description="图片 MIME 类型，如 image/png")


class OcrExtractOutput(ToolOutput):
    """提取输出：data 含 patient_name / diagnosis / amount / date / source。"""


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """解析 vision 模型输出的 JSON（容忍 markdown 包裹/前后缀文本）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _load_fallback() -> dict[str, Any]:
    """读取预置 Mock 兜底数据。"""
    data = json.loads(_FALLBACK_FILE.read_text(encoding="utf-8"))
    data["source"] = "mock_fallback"
    return data


def _normalize_amount(value: Any) -> float | None:
    """金额宽松归一化：'15800 元' / '15,800.00' / 15800 → 15800.0。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("，", "").replace("元", "").replace("¥", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


class OcrExtractTool(BaseTool[OcrExtractInput, OcrExtractOutput]):
    name = "ocr_extract"
    description = (
        "从诊断证明/病历/发票图片中提取结构化字段（患者姓名、诊断、金额、日期）。"
        "用户上传理赔材料图片时使用；vision API 异常时自动返回预置 Mock 数据。"
    )
    input_schema = OcrExtractInput
    output_schema = OcrExtractOutput

    async def _run(self, input_data: OcrExtractInput) -> OcrExtractOutput:
        try:
            fields = await self._extract_via_vision(input_data)
        except Exception as exc:  # noqa: BLE001 vision API 任何异常 → Mock 兜底
            log.warning("ocr_vision_error", error=str(exc)[:200])
            fields = None

        if fields is not None:
            log.info("ocr_extract_done", source="vision")
            return OcrExtractOutput(success=True, data=fields)

        # Mock 兜底：接口不报错（F12）
        fallback = _load_fallback()
        log.info("ocr_extract_done", source="mock_fallback")
        return OcrExtractOutput(success=True, data=fallback)

    async def _extract_via_vision(self, input_data: OcrExtractInput) -> dict[str, Any] | None:
        """调 vision 模型提取字段；失败返回 None（由上层兜底）。"""
        from services.llm.client import get_vision_model

        data_url = f"data:{input_data.mime_type};base64,{input_data.image_base64}"
        message = HumanMessage(
            content=[
                {"type": "text", "text": OCR_EXTRACT_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        )
        model = get_vision_model(temperature=0.0)
        response = await model.ainvoke([message])

        parsed = _parse_llm_json(response.content or "")
        if parsed is None:
            log.warning("ocr_vision_unparsed", raw=(response.content or "")[:100])
            return None

        amount = _normalize_amount(parsed.get("amount"))
        if amount is None and parsed.get("amount") is not None:
            # 金额存在但无法归一化 → 视为识别失败，走兜底
            return None

        return {
            "patient_name": parsed.get("patient_name"),
            "diagnosis": parsed.get("diagnosis"),
            "amount": amount,
            "date": parsed.get("date"),
            "source": "vision",
        }
