"""评测用例 Pydantic schema（T026，architecture.md 9.2）。

标注标准：每条用例 = 用户输入 + 期望工具调用序列 + 期望回答要点。
要点分为三类，评测器按类判分：
- must_include：回答必须包含（命中一个记一分，全部命中才过）
- any_of：任一命中即可
- must_not_include：命中即失败（如"保证赔付"违规话术）
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class EvalCategory(StrEnum):
    """评测用例分类。

    基础四分类（架构 9.2 比例：FAQ 30 / 单领域 60 / 多步 80 / 边界 30）；
    T033 新增 graph_assoc：复杂关联类（疾病↔险种↔规则跨实体），
    存放独立数据集 evals/datasets/eval_graph_assoc.json，用于纯 RAG vs 混合召回对比。
    """

    SIMPLE_FAQ = "simple_faq"  # 简单 FAQ（RAG 知识库问答）
    SINGLE_DOMAIN = "single_domain"  # 单领域查询（保单 / 医疗 / 合规各 20）
    MULTI_STEP = "multi_step"  # 多步复杂任务（规划 → 多 Agent 执行 → 合成）
    EDGE_CASE = "edge_case"  # 边界与异常（不存在数据 / 等待期 / 免责 / 越界）
    GRAPH_ASSOC = "graph_assoc"  # 复杂关联（跨实体多跳：疾病→险种→规则）


class EvalCase(BaseModel):
    """单条评测用例。"""

    id: str = Field(description="用例唯一 ID，如 FAQ-001")
    category: EvalCategory
    user_input: str = Field(min_length=1, description="用户输入")
    expected_tools: list[str] = Field(
        default_factory=list,
        description="期望被调用的工具名序列（子集匹配：实际调用集合 ⊇ 期望集合）",
    )
    expected_intent: str | None = Field(
        default=None,
        description="期望意图标签（simple_faq/single_domain/multi_step/chitchat/other）",
    )
    must_include: list[str] = Field(
        default_factory=list, description="回答必须包含的关键词（子串匹配，全部命中才得分）"
    )
    any_of: list[str] = Field(
        default_factory=list, description="回答包含任一关键词即得分（同义表述容错）"
    )
    must_not_include: list[str] = Field(
        default_factory=list, description="回答不得包含的关键词（命中即该用例失败，违规话术等）"
    )
    expect_human_intervention: bool = Field(
        default=False, description="是否期望转人工（need_human_intervention=True）"
    )
    note: str = Field(default="", description="标注说明（期望值来源，如 kb_docs/03 计算示例）")

    @model_validator(mode="after")
    def _validate_keys(self) -> EvalCase:
        """无任何判分要点且不期望转人工的用例无法判分，禁止。"""
        if not (
            self.must_include
            or self.any_of
            or self.must_not_include
            or self.expect_human_intervention
        ):
            raise ValueError(f"用例 {self.id} 缺少判分要点（must_include/any_of/must_not_include）")
        return self


class EvalDataset(BaseModel):
    """评测数据集（一个 JSON 文件一个数据集）。"""

    version: str = Field(description="数据集版本")
    description: str = Field(default="")
    cases: list[EvalCase]
