"""T032 图检索与混合召回测试：实体链接、多跳扩展、开关降级、真实图谱冒烟。"""

from __future__ import annotations

from services.rag.graph_retriever import (
    GraphSearchResult,
    expand_from_entities,
    link_entities,
    reset_knowledge_graph,
    search_graph,
)
from services.rag.knowledge_graph import (
    GraphEntity,
    GraphRelation,
    KnowledgeGraph,
    KnowledgeGraphData,
)


def _mini_graph() -> KnowledgeGraph:
    """迷你图谱：安心医疗 covers K35；安心医疗 applies 等待期；K35 disease_rule 等待期。"""
    return KnowledgeGraph(
        KnowledgeGraphData(
            entities=[
                GraphEntity(id="insurance:安心医疗旗舰版", type="insurance", name="安心医疗旗舰版"),
                GraphEntity(id="disease:K35急性阑尾炎", type="disease", name="急性阑尾炎"),
                GraphEntity(id="rule:等待期30天", type="rule", name="疾病等待期30天"),
            ],
            relations=[
                GraphRelation(
                    source="insurance:安心医疗旗舰版", target="disease:K35急性阑尾炎", type="covers"
                ),
                GraphRelation(
                    source="insurance:安心医疗旗舰版",
                    target="rule:等待期30天",
                    type="applies_to_rule",
                ),
                GraphRelation(
                    source="disease:K35急性阑尾炎", target="rule:等待期30天", type="disease_rule"
                ),
            ],
        )
    )


# ===== 实体链接 =====


def test_link_full_entity_name_in_query() -> None:
    """查询含完整实体名 → 命中。"""
    ids = link_entities("急性阑尾炎手术有等待期吗", _mini_graph())
    assert "disease:K35急性阑尾炎" in ids


def test_link_short_name_in_entity() -> None:
    """简写匹配：query="急性阑尾炎" 命中 "K35急性阑尾炎"（实体名含 query）。"""
    ids = link_entities("急性阑尾炎", _mini_graph())
    assert "disease:K35急性阑尾炎" in ids


def test_link_fuzzy_skip_word() -> None:
    """三级 bigram 匹配：跳词简写（查询少写"保险/括号"）也能命中。"""
    ids = link_entities("安心医疗旗舰版哪些疾病不保", _mini_graph())
    assert "insurance:安心医疗旗舰版" in ids


def test_link_fuzzy_disease_short() -> None:
    """三级 bigram 匹配：查询说"阑尾炎"命中"急性阑尾炎"（重叠 2/4=50%）。"""
    ids = link_entities("阑尾炎住院报销比例是多少", _mini_graph())
    assert "disease:K35急性阑尾炎" in ids


def test_link_no_match() -> None:
    assert link_entities("今天天气怎么样", _mini_graph()) == []


def test_link_skips_short_entities() -> None:
    """<3 字实体不参与链接（防误配）。"""
    data = KnowledgeGraphData(
        entities=[
            GraphEntity(id="disease:胃炎", type="disease", name="胃炎"),
            GraphEntity(id="disease:肺炎", type="disease", name="肺炎"),
        ],
        relations=[],
    )
    assert link_entities("胃炎能赔吗", KnowledgeGraph(data)) == []


# ===== 多跳扩展 =====


def test_expand_disease_forward_edges() -> None:
    """疾病出发：正向 disease_rule 边可达等待期规则。"""
    result = expand_from_entities(["disease:K35急性阑尾炎"], _mini_graph())
    assert result.matched_entities == ["急性阑尾炎"]
    facts_str = " ".join(f["fact"] for f in result.facts)
    assert "等待期" in facts_str


def test_expand_disease_reverse_cover() -> None:
    """疾病反向扩展：covers 是 险种→疾病 方向，"XX 病能赔吗"须沿入边回溯到险种。"""
    result = expand_from_entities(["disease:K35急性阑尾炎"], _mini_graph())
    facts_str = " ".join(f["fact"] for f in result.facts)
    assert "安心医疗旗舰版" in facts_str
    assert "保障" in facts_str


def test_expand_from_insurance_multi_hop() -> None:
    """险种出发 2 跳：险种→疾病、险种→规则全部扩展。"""
    result = expand_from_entities(["insurance:安心医疗旗舰版"], _mini_graph())
    facts_str = " ".join(f["fact"] for f in result.facts)
    assert "保障" in facts_str and "适用规则" in facts_str
    assert result.summary  # summary 非空（LLM 可读）


def test_expand_empty_entities() -> None:
    result = expand_from_entities([], _mini_graph())
    assert result.facts == [] and result.summary == ""


def test_expand_facts_capped() -> None:
    """事实条数上限（Token 控制）。"""
    entities = [GraphEntity(id="insurance:A", type="insurance", name="险种A")]
    entities += [
        GraphEntity(id=f"rule:规则{i:02d}", type="rule", name=f"规则条款{i:02d}号")
        for i in range(20)
    ]
    relations = [
        GraphRelation(source="insurance:A", target=f"rule:规则{i:02d}", type="applies_to_rule")
        for i in range(20)
    ]
    g = KnowledgeGraph(KnowledgeGraphData(entities=entities, relations=relations))
    result = expand_from_entities(["insurance:A"], g)
    assert len(result.facts) <= 12


# ===== search_graph 入口（真实图谱冒烟 + 降级） =====


async def test_search_graph_real_graph_smoke() -> None:
    """真实构建图谱（T031 产物）冒烟：疾病查询能扩展出事实。"""
    reset_knowledge_graph()
    try:
        result = await search_graph("急性阑尾炎在保障范围内吗")
        assert result.matched_entities, "真实图谱应能链接到阑尾炎实体"
        assert result.facts, "应扩展出至少一条事实"
    finally:
        reset_knowledge_graph()


async def test_search_graph_disabled(monkeypatch) -> None:
    """GRAPH_RAG_ENABLED=false → 空结果零影响。"""
    import services.rag.graph_retriever as gr

    monkeypatch.setattr(gr.settings, "graph_rag_enabled", False)
    reset_knowledge_graph()
    try:
        result = await search_graph("急性阑尾炎")
        assert result.facts == [] and result.matched_entities == []
    finally:
        reset_knowledge_graph()


async def test_search_graph_missing_file(tmp_path, monkeypatch) -> None:
    """图谱文件缺失 → 空图降级，不抛错。"""
    import services.rag.graph_retriever as gr

    monkeypatch.setattr(gr, "GRAPH_PATH", tmp_path / "missing.json")
    reset_knowledge_graph()
    try:
        result = await search_graph("任意查询")
        assert isinstance(result, GraphSearchResult)
        assert result.facts == []
    finally:
        reset_knowledge_graph()
