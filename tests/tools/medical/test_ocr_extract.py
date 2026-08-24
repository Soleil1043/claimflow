"""OCR 图片上传测试（T020，F12）。

两层：
- 工具层：vision 成功（source=vision）/ vision 异常与解析失败（source=mock_fallback，不抛错）/
  金额归一化 / schema 导出（mock vision 模型，不耗真实 token）
- API 层（A07）：上传图片 200 + 结构化字段 / 非图片 422 / 会话不存在 404 /
  vision 异常时接口不报错走 Mock 兜底 / 审计消息落库

真实 vision API 端到端验收见 scripts/verify_ocr.py。
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
import services.llm.client as llm_client_module
from app.main import app
from services.db.models import Base
from tools.medical.ocr_extract import OcrExtractTool, _normalize_amount, _parse_llm_json

# 一张 1x1 像素 PNG 的 base64（内容无关紧要，vision 模型全 mock）
_TINY_PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63fcffff3f030005fe02fea72d9f440000000049454e44ae426082"
    )
).decode("ascii")


class FakeVisionModel:
    """可控 vision 模型：返回预设响应或抛异常。"""

    def __init__(self, response: Any = None, raise_exc: Exception | None = None) -> None:
        self._response = response
        self._raise = raise_exc

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        if self._raise:
            raise self._raise

        class _Resp:
            def __init__(self, content: str) -> None:
                self.content = content

        return _Resp(self._response)


def _patch_vision(monkeypatch: pytest.MonkeyPatch, model: FakeVisionModel) -> None:
    monkeypatch.setattr(llm_client_module, "get_vision_model", lambda *a, **k: model)


# ---------- 纯函数 ----------


def test_parse_llm_json_variants() -> None:
    assert _parse_llm_json('{"patient_name": "张伟"}') == {"patient_name": "张伟"}
    assert _parse_llm_json('```json\n{"amount": 158}\n```') == {"amount": 158}
    assert _parse_llm_json("前置文本 {\"a\": 1} 后置") == {"a": 1}
    assert _parse_llm_json("不是 JSON") is None


def test_normalize_amount() -> None:
    assert _normalize_amount(15800) == 15800.0
    assert _normalize_amount("15800 元") == 15800.0
    assert _normalize_amount("15,800.00") == 15800.0
    assert _normalize_amount(None) is None
    assert _normalize_amount("abc") is None


# ---------- 工具层 ----------


async def test_tool_vision_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """vision 正常识别：source=vision。"""
    raw = '{"patient_name": "张伟", "diagnosis": "急性阑尾炎", "amount": 15800, "date": "2026-08-10"}'
    _patch_vision(monkeypatch, FakeVisionModel(response=raw))
    result = await OcrExtractTool().execute({"image_base64": _TINY_PNG})
    assert result.success is True
    assert result.data["source"] == "vision"
    assert result.data["patient_name"] == "张伟"
    assert result.data["diagnosis"] == "急性阑尾炎"
    assert result.data["amount"] == 15800.0
    assert result.data["date"] == "2026-08-10"


async def test_tool_vision_exception_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """vision API 异常：Mock 兜底，接口不抛错（F12 核心）。"""
    _patch_vision(monkeypatch, FakeVisionModel(raise_exc=RuntimeError("vision API 超时")))
    result = await OcrExtractTool().execute({"image_base64": _TINY_PNG})
    assert result.success is True
    assert result.data["source"] == "mock_fallback"
    assert result.data["patient_name"] == "张伟"
    assert result.data["amount"] == 15800.0


async def test_tool_vision_unparsable_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """vision 输出非 JSON：Mock 兜底。"""
    _patch_vision(monkeypatch, FakeVisionModel(response="我看不清图片内容"))
    result = await OcrExtractTool().execute({"image_base64": _TINY_PNG})
    assert result.success is True
    assert result.data["source"] == "mock_fallback"


async def test_tool_vision_bad_amount_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """金额存在但无法归一化：识别失败走兜底。"""
    raw = '{"patient_name": "张三", "diagnosis": "感冒", "amount": "很多钱", "date": "2026-01-01"}'
    _patch_vision(monkeypatch, FakeVisionModel(response=raw))
    result = await OcrExtractTool().execute({"image_base64": _TINY_PNG})
    assert result.data["source"] == "mock_fallback"


async def test_tool_amount_string_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    """金额字符串归一化为 float。"""
    raw = '{"patient_name": "李四", "diagnosis": "胃炎", "amount": "15,800.00 元", "date": "2026-08-01"}'
    _patch_vision(monkeypatch, FakeVisionModel(response=raw))
    result = await OcrExtractTool().execute({"image_base64": _TINY_PNG})
    assert result.data["amount"] == 15800.0
    assert result.data["source"] == "vision"


async def test_tool_empty_input_rejected() -> None:
    result = await OcrExtractTool().execute({"image_base64": ""})
    assert result.success is False


def test_tool_schema_export() -> None:
    spec = OcrExtractTool().to_openai_tool()
    assert spec["function"]["name"] == "ocr_extract"
    assert "image_base64" in spec["function"]["parameters"]["properties"]


async def test_tool_registered() -> None:
    import tools.medical  # noqa: F401
    from tools.registry import get_default_registry

    assert "ocr_extract" in get_default_registry().list_names()


# ---------- API 层（A07） ----------


@pytest.fixture()
async def api_client(monkeypatch):
    """内存 SQLite + mock vision 的 A07 测试客户端。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)

    raw = '{"patient_name": "张伟", "diagnosis": "急性阑尾炎", "amount": 15800, "date": "2026-08-10"}'
    _patch_vision(monkeypatch, FakeVisionModel(response=raw))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


def _png_bytes() -> bytes:
    return base64.b64decode(_TINY_PNG)


async def test_a07_upload_image_returns_fields(api_client) -> None:
    """A07：上传 png → 200 + 结构化字段 + source=vision + 审计落库。"""
    conv = (await api_client.post("/api/v1/conversations", json={})).json()
    cid = conv["conversation_id"]

    resp = await api_client.post(
        f"/api/v1/conversations/{cid}/images",
        files={"file": ("diagnosis.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "vision"
    assert body["patient_name"] == "张伟"
    assert body["diagnosis"] == "急性阑尾炎"
    assert body["amount"] == 15800.0
    assert body["date"] == "2026-08-10"
    assert body["filename"] == "diagnosis.png"

    # 审计落库：上传 + 识别结果两条消息
    history = (await api_client.get(f"/api/v1/conversations/{cid}/messages")).json()
    assert history["total"] == 2
    assistant = history["items"][1]
    assert "材料识别结果" in assistant["content"]
    assert assistant["tool_trace"][0]["tool"] == "ocr_extract"


async def test_a07_non_image_rejected_422(api_client) -> None:
    """A07：非图片文件（pdf MIME + txt 扩展名）→ 422（F12 验收）。"""
    conv = (await api_client.post("/api/v1/conversations", json={})).json()
    cid = conv["conversation_id"]

    resp = await api_client.post(
        f"/api/v1/conversations/{cid}/images",
        files={"file": ("report.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 422

    # MIME 缺失但扩展名非图片 → 422
    resp2 = await api_client.post(
        f"/api/v1/conversations/{cid}/images",
        files={"file": ("data.txt", b"plain text", None)},
    )
    assert resp2.status_code == 422


async def test_a07_image_extension_inferred(api_client) -> None:
    """A07：MIME 缺失但扩展名为 png → 放行（按扩展名推断）。"""
    conv = (await api_client.post("/api/v1/conversations", json={})).json()
    cid = conv["conversation_id"]

    resp = await api_client.post(
        f"/api/v1/conversations/{cid}/images",
        files={"file": ("photo.png", _png_bytes(), None)},
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "vision"


async def test_a07_vision_failure_returns_mock(api_client, monkeypatch) -> None:
    """A07：vision API 异常 → 接口 200，source=mock_fallback，不报错（F12 验收）。"""
    _patch_vision(monkeypatch, FakeVisionModel(raise_exc=RuntimeError("vision API 故障")))
    conv = (await api_client.post("/api/v1/conversations", json={})).json()
    cid = conv["conversation_id"]

    resp = await api_client.post(
        f"/api/v1/conversations/{cid}/images",
        files={"file": ("invoice.jpg", _png_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "mock_fallback"
    assert body["patient_name"] == "张伟"  # 预置 Mock 数据


async def test_a07_conversation_not_found(api_client) -> None:
    """A07：不存在的会话 404。"""
    import uuid as uuid_mod

    resp = await api_client.post(
        f"/api/v1/conversations/{uuid_mod.uuid4()}/images",
        files={"file": ("x.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 404


async def test_a07_empty_file_rejected(api_client) -> None:
    """A07：空文件 422。"""
    conv = (await api_client.post("/api/v1/conversations", json={})).json()
    cid = conv["conversation_id"]

    resp = await api_client.post(
        f"/api/v1/conversations/{cid}/images",
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert resp.status_code == 422
