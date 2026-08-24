"""Medical Agent（医疗审核代理）：就诊记录 / ICD-10 匹配 / 材料核对。

工具 record_query / diagnosis_matcher 随 T016 实现，OCR 随 T020 实现；
定义先行（resolve_tools 自动过滤未注册工具，不阻断）。
"""

from __future__ import annotations

from agents.base import AgentDefinition
from schemas.agent_outputs import MedicalAgentOutput
from services.llm.prompts import MEDICAL_AGENT_PROMPT

MEDICAL_AGENT = AgentDefinition(
    name="medical",
    display_name="医疗审核 Agent",
    system_prompt=MEDICAL_AGENT_PROMPT,
    tool_names=[
        # 以下三个随 T016/T020 实现
        "record_query",
        "diagnosis_matcher",
        "ocr_extract",
        # 等待期等医学规则判断需要 RAG（已实现，直接复用）
        "claim_rule_rag",
    ],
    output_schema=MedicalAgentOutput,
    description="核对就诊记录、ICD-10 诊断匹配与保障范围判断、理赔材料核对",
)
