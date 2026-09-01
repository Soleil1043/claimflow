"""理赔规则 RAG 检索工具（F06）。

用户询问条款、等待期、免责、报销规则、理赔材料等知识类问题时，
检索理赔规则知识库（Qdrant + BGE-M3）返回相关条款片段。

失败语义（T007 约定，T044 迁官方工具基类后保持）：
- 知识库为空 / 无相关结果 → 返回含 success=False 的结果 dict（Agent 走兜底话术或追问）
- 向量化 / Qdrant 故障 → 抛异常，交给守卫层（tools/guards.py，可配 Fallback 兜底模板）
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from schemas.tools import ToolInput
from services.rag.graph_retriever import search_graph
from services.rag.retriever import search_kb
from tools.base import ClaimflowTool


class ClaimRuleRagInput(ToolInput):
    """知识库检索入参。"""

    query: str = Field(description="要检索的问题或关键词，如'阑尾炎手术有等待期吗'", min_length=1)
    top_k: int = Field(default=4, description="返回的最相关片段数量", ge=1, le=10)


class ClaimRuleRagTool(ClaimflowTool):
    # 注：name/description 必须带类型注解——pydantic 要求子类覆盖父类字段时显式标注
    name: str = "claim_rule_rag"
    description: str = (
        "检索理赔规则知识库（保险条款、等待期、免责说明、理赔材料清单、常见问题）。"
        "用户咨询保险知识、条款规则、'需要什么材料'、'有等待期吗'、'能报销吗'时使用。"
        "返回最相关的条款片段（含来源文档与相似度分数）。"
    )
    args_schema: type[ClaimRuleRagInput] = ClaimRuleRagInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步壳（langchain 1.x 要求实现 _run）：本项目全链路 async，同步路径不可用。"""
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(self, *, query: str, top_k: int = 4) -> dict[str, Any]:
        chunks = await search_kb(query=query, top_k=top_k)

        # T032 混合召回：图检索补充结构性事实（故障/未命中零影响）
        graph_facts: list[dict[str, Any]] = []
        try:
            graph_result = await search_graph(query)
            graph_facts = graph_result.facts
        except Exception:  # noqa: BLE001 图检索故障不影响向量检索
            pass

        if not chunks and not graph_facts:
            return {
                "success": False,
                "error_message": "知识库检索无结果（知识库可能未初始化）",
            }

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
        data: dict[str, Any] = {"success": True, "results": results}
        if graph_facts:
            data["graph_facts"] = graph_facts
        return data
