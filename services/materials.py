"""材料提取服务（T049，D024）：图片 / PDF / Word → 结构化字段。

分派策略：
- image：走既有 OcrExtractTool（vision 模型，自带 Mock 兜底）
- pdf：两段式——pypdf 抽文本，足量（≥ material_pdf_text_min_chars）走主链路模型提取；
  扫描件（文本不足）→ pypdfium2 渲染前 N 页逐页走 vision，取首个有效页
- docx：python-docx 抽正文 + 表格文本 → 主链路模型提取（文本版 prompt，schema 与图片版一致）

任何失败（解析异常 / 模型异常 / 输出非法）→ 预置 Mock 数据（source=mock_fallback），
接口不报错（D008 语义延续）。.doc 旧格式由 API 层 422 提示转存 .docx。
"""

from __future__ import annotations

import base64
import io
from typing import Any

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger
from services.llm.prompts import OCR_EXTRACT_TEXT_PROMPT
from tools.medical.ocr_extract import _load_fallback, _normalize_amount, _parse_llm_json

log = get_logger(__name__)

# docx 的 MIME（Word 2007+ OpenXML）
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp"}

# PDF 抽文本的总页数上限（防超大文件 token 失控；渲染另有更严的页数护栏）
_PDF_TEXT_PAGE_LIMIT = 10

# 材料文本送入主链路模型的截断长度（字符）
_TEXT_INPUT_LIMIT = 6000


class MaterialExtraction(BaseModel):
    """提取结果：结构化字段 + 来源 + 材料类型。"""

    patient_name: str | None = None
    diagnosis: str | None = None
    amount: float | None = None
    date: str | None = None
    # vision（图片/扫描件识别） / text_model（PDF/Word 文本提取） / mock_fallback（失败降级）
    source: str
    # image / pdf / docx（unknown 表示类型未识别即失败）
    file_type: str


def detect_material_type(filename: str, mime: str) -> str:
    """识别材料类型：image / pdf / docx；无法识别返回空串（API 层 422）。

    MIME 优先（浏览器上传会带 content_type），扩展名兜底（MIME 缺失或 generic 时）。
    注意 .doc 旧格式故意不识别（python-docx 不支持），由 API 层给出转存提示。
    """
    mime = (mime or "").lower()
    name = (filename or "").lower()
    if mime in _IMAGE_MIMES or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        return "image"
    if mime == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if mime == _DOCX_MIME or name.endswith(".docx"):
        return "docx"
    return ""


def _fields_from(data: dict[str, Any]) -> dict[str, Any]:
    """结果 dict → 四字段（金额归一化；容忍缺键）。"""
    return {
        "patient_name": data.get("patient_name") or None,
        "diagnosis": data.get("diagnosis") or None,
        "amount": _normalize_amount(data.get("amount")),
        "date": data.get("date") or None,
    }


def _fields_or_none(data: dict[str, Any]) -> dict[str, Any] | None:
    """LLM 输出 → 四字段；全空（模型没提到任何字段）视为无效返回 None。"""
    fields = _fields_from(data)
    return fields if any(v is not None for v in fields.values()) else None


def _image_mime(filename: str) -> str:
    """按扩展名取图片 MIME（构造 data URL 用；调用方已确保是图片）。"""
    suffix = (filename or "").rsplit(".", 1)[-1].lower()
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }.get(suffix, "image/png")


async def _extract_via_text_model(text: str) -> dict[str, Any] | None:
    """主链路 flash 模型从材料文本提取字段；失败/无字段返回 None。"""
    from langchain_core.messages import HumanMessage

    from services.llm.client import get_chat_model
    from services.observability.token_tracker import phase_ainvoke

    model = get_chat_model(temperature=0.0)
    response = await phase_ainvoke(
        model,
        [HumanMessage(content=OCR_EXTRACT_TEXT_PROMPT.format(content=text[:_TEXT_INPUT_LIMIT]))],
        phase="ocr",
    )
    parsed = _parse_llm_json(response.content or "")
    return _fields_or_none(parsed) if parsed else None


async def _vision_extract_png(png: bytes) -> dict[str, Any] | None:
    """单页 PNG 走 vision 模型提取（无 Mock，由调用方决定兜底）。"""
    from langchain_core.messages import HumanMessage

    from services.llm.client import get_vision_model
    from services.llm.prompts import OCR_EXTRACT_PROMPT
    from services.observability.token_tracker import phase_ainvoke

    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    message = HumanMessage(
        content=[
            {"type": "text", "text": OCR_EXTRACT_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    )
    response = await phase_ainvoke(get_vision_model(temperature=0.0), [message], phase="ocr")
    parsed = _parse_llm_json(response.content or "")
    return _fields_or_none(parsed) if parsed else None


async def _extract_image_via_tool(content: bytes, mime: str) -> dict[str, Any]:
    """图片走既有 OcrExtractTool（内部自带 vision→Mock 兜底）。"""
    from tools.medical.ocr_extract import OcrExtractTool

    return await OcrExtractTool().ainvoke(
        {
            "image_base64": base64.b64encode(content).decode("ascii"),
            "mime_type": mime,
        }
    )


def _pdf_text(content: bytes) -> str:
    """pypdf 抽取 PDF 文本（前 N 页；解析异常向上抛由统一兜底接住）。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:_PDF_TEXT_PAGE_LIMIT])


def _pdf_page_images(content: bytes, max_pages: int) -> list[bytes]:
    """pypdfium2 渲染 PDF 前 N 页为 PNG 字节（扫描件走 vision 用）。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(content)
    try:
        images: list[bytes] = []
        for i in range(min(len(pdf), max_pages)):
            bitmap = pdf[i].render(scale=2.0)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            images.append(buf.getvalue())
        return images
    finally:
        pdf.close()


def _docx_text(content: bytes) -> str:
    """python-docx 抽取 Word 正文与表格文本。"""
    import docx

    document = docx.Document(io.BytesIO(content))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


async def extract_material(filename: str, mime: str, content: bytes) -> MaterialExtraction:
    """公共入口：按类型分派提取；任何失败降级 Mock 兜底（不抛错，D008 语义）。"""
    file_type = detect_material_type(filename, mime)
    try:
        if file_type == "image":
            data = await _extract_image_via_tool(content, _image_mime(filename))
            return MaterialExtraction(
                file_type="image",
                source=str(data.get("source", "vision")),
                **_fields_from(data),
            )

        if file_type == "pdf":
            text = _pdf_text(content)
            if len(text.strip()) >= settings.material_pdf_text_min_chars:
                fields = await _extract_via_text_model(text)
                if fields is not None:
                    return MaterialExtraction(file_type="pdf", source="text_model", **fields)
            # 扫描件（文本不足/提取失败）→ 渲染走 vision，取首个有效页
            for png in _pdf_page_images(content, settings.material_pdf_render_pages):
                fields = await _vision_extract_png(png)
                if fields is not None:
                    return MaterialExtraction(file_type="pdf", source="vision", **fields)
            raise ValueError("PDF 未提取到有效字段")

        if file_type == "docx":
            fields = await _extract_via_text_model(_docx_text(content))
            if fields is None:
                raise ValueError("Word 未提取到有效字段")
            return MaterialExtraction(file_type="docx", source="text_model", **fields)

        raise ValueError(f"不支持的文件类型: {filename}")
    except Exception as exc:  # noqa: BLE001 任何失败 → Mock 兜底，接口不报错
        log.warning(
            "material_extract_failed", file_type=file_type or "unknown", error=str(exc)[:200]
        )
        return MaterialExtraction(
            file_type=file_type or "unknown", source="mock_fallback", **_fields_from(_load_fallback())
        )
