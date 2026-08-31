"""A07 材料上传 API 测试（T049，D024）：pdf/docx 提取、兼容别名端点、护栏 422。

内存 SQLite + mock extract_material（不依赖真实 LLM / PDF 解析）。
"""

from __future__ import annotations

import io

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.v1.conversations as conversations_module
import services.db.session as session_module
from app.main import app
from services.db.models import Base
from services.materials import MaterialExtraction, detect_material_type

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture()
async def client(monkeypatch):
    """内存 SQLite 引擎 + mock 提取服务 + 测试客户端。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)
    monkeypatch.setattr(session_module.settings, "llm_api_key", "sk-test")

    async def fake_extract(filename: str, mime: str, content: bytes) -> MaterialExtraction:
        return MaterialExtraction(
            patient_name="张三",
            diagnosis="急性阑尾炎",
            amount=15800.0,
            date="2026-08-20",
            source="text_model",
            file_type=detect_material_type(filename, mime),
        )

    monkeypatch.setattr(conversations_module, "extract_material", fake_extract)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


def _make_docx() -> bytes:
    """真实 .docx 字节（端点内提取已 mock，仅验证上传与路由）。"""
    import docx

    document = docx.Document()
    document.add_paragraph("患者姓名：张三 临床诊断：急性阑尾炎")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def _new_conversation(client: AsyncClient) -> str:
    resp = await client.post("/api/v1/conversations", json={"user_id": "user-a"})
    return resp.json()["conversation_id"]


# ---------- 正常提取 ----------


async def test_upload_docx_returns_fields(client: AsyncClient) -> None:
    conv_id = await _new_conversation(client)
    resp = await client.post(
        f"/api/v1/conversations/{conv_id}/materials",
        files={"file": ("诊断证明.docx", _make_docx(), _DOCX_MIME)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_type"] == "docx"
    assert body["source"] == "text_model"
    assert body["diagnosis"] == "急性阑尾炎"
    assert body["amount"] == 15800.0


async def test_upload_pdf_returns_fields(client: AsyncClient) -> None:
    conv_id = await _new_conversation(client)
    resp = await client.post(
        f"/api/v1/conversations/{conv_id}/materials",
        files={"file": ("病历.pdf", b"%PDF-1.4 fake-bytes", "application/pdf")},
    )
    assert resp.status_code == 200
    assert resp.json()["file_type"] == "pdf"


async def test_upload_image_via_materials(client: AsyncClient) -> None:
    conv_id = await _new_conversation(client)
    resp = await client.post(
        f"/api/v1/conversations/{conv_id}/materials",
        files={"file": ("证明.png", b"png-bytes", "image/png")},
    )
    assert resp.status_code == 200
    assert resp.json()["file_type"] == "image"


async def test_legacy_images_alias_still_works(client: AsyncClient) -> None:
    """旧端点 /images 保持兼容（deprecated 别名，同一处理函数）。"""
    conv_id = await _new_conversation(client)
    resp = await client.post(
        f"/api/v1/conversations/{conv_id}/images",
        files={"file": ("证明.jpg", b"jpg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 200
    assert resp.json()["file_type"] == "image"


async def test_upload_writes_audit_messages(client: AsyncClient) -> None:
    """上传行为 + 识别结果落审计消息（A05 可查）。"""
    conv_id = await _new_conversation(client)
    await client.post(
        f"/api/v1/conversations/{conv_id}/materials",
        files={"file": ("诊断证明.docx", _make_docx(), _DOCX_MIME)},
    )
    resp = await client.get(f"/api/v1/conversations/{conv_id}/messages")
    contents = [m["content"] for m in resp.json()["items"]]
    assert any("【上传材料】诊断证明.docx" in c for c in contents)
    assert any("【材料识别结果】" in c for c in contents)


# ---------- 护栏 422 ----------


async def test_upload_doc_rejected_with_hint(client: AsyncClient) -> None:
    """.doc 旧格式 → 422 并提示转存 .docx。"""
    conv_id = await _new_conversation(client)
    resp = await client.post(
        f"/api/v1/conversations/{conv_id}/materials",
        files={"file": ("旧文档.doc", b"doc-bytes", "application/msword")},
    )
    assert resp.status_code == 422
    assert "docx" in resp.json()["detail"]


async def test_upload_unsupported_type_rejected(client: AsyncClient) -> None:
    conv_id = await _new_conversation(client)
    resp = await client.post(
        f"/api/v1/conversations/{conv_id}/materials",
        files={"file": ("程序.exe", b"exe-bytes", "application/octet-stream")},
    )
    assert resp.status_code == 422


async def test_upload_empty_file_rejected(client: AsyncClient) -> None:
    conv_id = await _new_conversation(client)
    resp = await client.post(
        f"/api/v1/conversations/{conv_id}/materials",
        files={"file": ("空.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 422


async def test_upload_oversize_rejected(client: AsyncClient, monkeypatch) -> None:
    """超过大小上限 → 422（测试期把上限压到 0MB）。"""
    monkeypatch.setattr(conversations_module.settings, "material_max_size_mb", 0)
    conv_id = await _new_conversation(client)
    resp = await client.post(
        f"/api/v1/conversations/{conv_id}/materials",
        files={"file": ("大文件.docx", b"x", _DOCX_MIME)},
    )
    assert resp.status_code == 422
    assert "上限" in resp.json()["detail"]
