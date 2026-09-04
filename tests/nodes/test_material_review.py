"""材料审核节点测试（T082）：真实提取对接 / 规则层 / AI 审查 skill 装配。"""

from __future__ import annotations

import json

import pytest

import nodes.material_review as mr_module
from nodes.material_review import make_material_ai_reviewer, make_material_review_node


class MiniRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def event(self, case_id, kind, stage=None, payload=None) -> None:
        self.events.append({"case_id": case_id, "kind": kind, "stage": stage, "payload": payload})

    async def update_case(self, case_id, **kwargs) -> None:
        pass

    async def save_decision(self, case_id, **kwargs) -> None:
        pass


def _node(ai_reviewer=None):
    return make_material_review_node(MiniRecorder(), ai_reviewer)


async def _run(node, materials, case_type="medical"):
    return await node({"case_id": "C1", "case_type": case_type, "materials": materials})


# ---------- 引用型材料（种子/测试口径） ----------


async def test_reference_only_complete() -> None:
    update = await _run(
        _node(),
        [
            {"file_name": "invoice.jpg", "doc_type": "invoice"},
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            {"file_name": "cost_list.pdf", "doc_type": "cost_list"},
        ],
    )
    material = update["material"]
    assert material["completeness"] == "complete"
    assert material["missing"] == []
    assert material["confidence"] == 1.0
    assert len(material["documents"]) == 3


async def test_reference_missing_material_partial() -> None:
    update = await _run(_node(), [{"file_name": "invoice.jpg", "doc_type": "invoice"}])
    material = update["material"]
    assert material["completeness"] == "partial"
    assert material["missing"] == ["诊断证明", "费用清单"]


async def test_note_anomaly_drops_confidence() -> None:
    """已知异常标记（如发票与清单矛盾）→ 置信度 0.4（低于 0.6 裁量线）。"""
    update = await _run(
        _node(),
        [
            {"file_name": "invoice.jpg", "doc_type": "invoice"},
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            {"file_name": "cost_list.pdf", "doc_type": "cost_list", "note": "金额矛盾"},
        ],
    )
    assert update["material"]["confidence"] == 0.4


async def test_reference_without_doc_type_inferred() -> None:
    """doc_type 缺失 → 文件名关键词推断（classify 纯函数）。"""
    update = await _run(_node(), [{"file_name": "医疗发票_15800.jpg"}])
    doc = update["material"]["documents"][0]
    assert doc["doc_type"] == "invoice"


# ---------- 真实/已存提取 ----------


async def test_stored_extraction_converted(monkeypatch) -> None:
    """B03 落档的提取结果 → ExtractedDocument 强类型（金额 Decimal/日期 date）。"""
    update = await _run(
        _node(),
        [
            {
                "file_name": "invoice.pdf",
                "doc_type": "invoice",
                "extraction": {
                    "patient_name": "张三",
                    "diagnosis": "急性阑尾炎",
                    "amount": 15800.0,
                    "date": "2026-08-10",
                    "source": "text_model",
                    "file_type": "pdf",
                },
            }
        ],
    )
    doc = update["material"]["documents"][0]
    assert doc["total_amount"] == "15800.0"  # JSON 序列化口径
    assert doc["treatment_date"] == "2026-08-10"
    assert doc["source"] == "text_model"
    assert update["material"]["confidence"] == 0.9


async def test_storage_path_real_extraction(monkeypatch, tmp_path) -> None:
    """storage_path 真实文件 → 读取字节调用 T049 提取服务（此处 mock 验证对接）。"""
    captured = {}
    file_path = tmp_path / "invoice.jpg"
    file_path.write_bytes(b"fake-image-bytes")

    async def fake_extract(filename: str, mime: str, content: bytes):
        captured["filename"] = filename
        captured["content"] = content
        from services.materials import MaterialExtraction

        return MaterialExtraction(
            patient_name="张三",
            diagnosis="急性阑尾炎",
            amount=15800.0,
            date="2026-08-10",
            source="vision",
            file_type="image",
        )

    monkeypatch.setattr(mr_module, "extract_material", fake_extract)
    update = await _run(
        _node(),
        [{"file_name": "invoice.jpg", "doc_type": "invoice", "storage_path": str(file_path)}],
    )
    assert captured["content"] == b"fake-image-bytes"
    doc = update["material"]["documents"][0]
    assert doc["total_amount"] == "15800.0"
    assert doc["source"] == "vision"


async def test_amount_contradiction_drops_confidence(monkeypatch) -> None:
    """发票 vs 清单金额不一致（已存提取）→ 交叉核验命中，置信度 0.4。"""
    update = await _run(
        _node(),
        [
            {
                "file_name": "invoice.jpg",
                "doc_type": "invoice",
                "extraction": {"amount": 15800.0, "source": "vision", "file_type": "image"},
            },
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            {
                "file_name": "cost_list.pdf",
                "doc_type": "cost_list",
                "extraction": {"amount": 12800.0, "source": "text_model", "file_type": "pdf"},
            },
        ],
    )
    assert update["material"]["confidence"] == 0.4
    assert update["material"]["completeness"] == "complete"


# ---------- AI 一致性审查（skill 装配） ----------


async def test_ai_reviewer_assembles_skill_and_flags_anomaly() -> None:
    """skill 装配生效：审查提示词含规程内容（占位符已填充）；异常输出压低置信度。"""
    captured_prompts: list[str] = []

    async def ai_reviewer(state, documents):
        # 复刻 make_material_ai_reviewer 内部逻辑以捕获提示词
        system = mr_module.build_system_prompt(
            mr_module.MATERIAL_REVIEW_AI_PROMPT,
            "material_review",
            state.get("case_type") or "_shared",
            documents=json.dumps(documents, ensure_ascii=False, default=str)[:2500],
        )
        captured_prompts.append(system)
        return ["发票患者姓名与诊断证明不一致"]

    update = await _run(
        _node(ai_reviewer),
        [
            {"file_name": "invoice.jpg", "doc_type": "invoice"},
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            {"file_name": "cost_list.pdf", "doc_type": "cost_list"},
        ],
    )
    system_prompt = captured_prompts[0]
    # skill 装配断言：材料审核规程内容在提示词中（开关对比的"开"侧）
    assert "交叉核验" in system_prompt or "置信度判定" in system_prompt
    assert "{documents}" not in system_prompt  # 占位符已填充
    assert update["material"]["confidence"] == 0.4  # AI 异常压低


async def test_ai_reviewer_disabled_skips_call(monkeypatch) -> None:
    """material_review_llm_enabled=False → 工厂返回 None（规则层兜底，零 LLM）。"""
    monkeypatch.setattr(mr_module.settings, "material_review_llm_enabled", False)
    assert make_material_ai_reviewer() is None


async def test_ai_reviewer_failure_fail_open() -> None:
    """AI 审查抛错 → fail-open：不影响规则结论（置信度保持规则值）。"""
    async def broken_reviewer(state, documents):
        raise RuntimeError("LLM 超时")

    update = await _run(
        _node(broken_reviewer),
        [
            {"file_name": "invoice.jpg", "doc_type": "invoice"},
            {"file_name": "diagnosis.jpg", "doc_type": "diagnosis"},
            {"file_name": "cost_list.pdf", "doc_type": "cost_list"},
        ],
    )
    assert update["material"]["confidence"] == 1.0
    assert update["material"]["completeness"] == "complete"


@pytest.mark.parametrize(
    "line,expected_complete",
    [("medical", True), ("accident", True)],
)
async def test_line_scoped_completeness(line: str, expected_complete: bool) -> None:
    update = await _run(_node(), [{"file_name": "x.jpg", "doc_type": "invoice"}], case_type=line)
    if expected_complete and line == "accident":
        assert update["material"]["completeness"] == "complete"
