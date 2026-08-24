"""理赔规则 RAG 检索工具（F06）。

用户询问条款、等待期、免责、报销规则、理赔材料等知识类问题时，
检索理赔规则知识库（Qdrant + BGE-M3）返回相关条款片段。

失败语义（T007 约定）：
- 知识库为空 / 无相关结果 → success=False（Agent 走兜底话术或追问）
- 向量化 / Qdrant 故障 → 抛异常，交给 ToolExecutor（可配 Fallback 兜底模板）
"""

from __future__ import annotations

from pydantic import Field

from schemas.tools import ToolInput, ToolOutput
from services.rag.retriever import search_kb
from tools.base import BaseTool


class ClaimRuleRagInput(ToolInput):
    """知识库检索入参。"""

    query: str = Field(description="要检索的问题或关键词，如'阑尾炎手术有等待期吗'", min_length=1)
    top_k: int = Field(default=4, description="返回的最相关片段数量", ge=1, le=10)


class ClaimRuleRagOutput(ToolOutput):
    """检索输出：data.results 为按相似度降序的条款片段列表。"""


class ClaimRuleRagTool(BaseTool[ClaimRuleRagInput, ClaimRuleRagOutput]):
    name = "claim_rule_rag"
    description = (
        "检索理赔规则知识库（保险条款、等待期、免责说明、理赔材料清单、常见问题）。"
        "用户咨询保险知识、条款规则、'需要什么材料'、'有等待期吗'、'能报销吗'时使用。"
        "返回最相关的条款片段（含来源文档与相似度分数）。"
    )
    input_schema = ClaimRuleRagInput
    output_schema = ClaimRuleRagOutput

    async def _run(self, input_data: ClaimRuleRagInput) -> ClaimRuleRagOutput:
        chunks = await search_kb(query=input_data.query, top_k=input_data.top_k)

        if not chunks:
            return ClaimRuleRagOutput(
                success=False,
                error_message="知识库检索无结果（知识库可能未初始化）",
            )

        results = [
            {
                "text": c.text,
                "title": c.title,
                "category": c.category,
                "source_file": c.source_file,
                "score": round(c.score, 4),
            }
            for c in chunks
        ]
        return ClaimRuleRagOutput(success=True, data={"results": results})
