"""T031 知识图谱测试：schema 校验、图结构、多跳遍历、三元组容错、落盘回读。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from services.rag.knowledge_graph import (
    GraphEntity,
    GraphRelation,
    KnowledgeGraph,
    KnowledgeGraphData,
    build_graph_from_triples,
    load_graph,
    save_graph,
)


def _sample_data() -> KnowledgeGraphData:
    """样例图谱：安心医疗 -(covers)-> K35 阑尾炎；安心医疗 -(applies_to_rule)-> 等待期30天。"""
    return KnowledgeGraphData(
        entities=[
            GraphEntity(id="insurance:安心医疗旗舰版", type="insurance", name="安心医疗保险旗舰版"),
            GraphEntity(id="disease:K35急性阑尾炎", type="disease", name="急性阑尾炎",
                        properties={"icd10": "K35"}),
            GraphEntity(id="rule:疾病等待期30天", type="rule", name="疾病医疗等待期30天"),
            GraphEntity(id="disease:N20肾结石", type="disease", name="肾结石",
                        properties={"icd10": "N20"}),
        ],
        relations=[
            GraphRelation(source="insurance:安心医疗旗舰版", target="disease:K35急性阑尾炎",
                          type="covers", evidence="kb03"),
            GraphRelation(source="insurance:安心医疗旗舰版", target="rule:疾病等待期30天",
                          type="applies_to_rule", evidence="kb01"),
            GraphRelation(source="insurance:安心医疗旗舰版", target="disease:N20肾结石",
                          type="covers", evidence="kb10"),
        ],
    )


# ===== schema =====


def test_entity_id_prefix_validated() -> None:
    """实体 id 前缀必须与 type 一致（防脏数据）。"""
    with pytest.raises(ValidationError, match="前缀"):
        GraphEntity(id="disease:错误前缀", type="rule", name="x")


def test_relation_schema() -> None:
    """关系默认权重 1.0。"""
    r = GraphRelation(source="a", target="b", type="covers")
    assert r.weight == 1.0


# ===== 图结构 =====


def test_neighbors_and_reverse() -> None:
    g = KnowledgeGraph(_sample_data())
    covers = g.neighbors("insurance:安心医疗旗舰版", relation_types={"covers"})
    assert {e.name for _, e in covers} == {"急性阑尾炎", "肾结石"}

    # 反向：疾病 → 险种
    rev = g.neighbors("disease:K35急性阑尾炎", reverse=True)
    assert len(rev) == 1 and rev[0][1].type == "insurance"


def test_find_entities() -> None:
    g = KnowledgeGraph(_sample_data())
    assert len(g.find_entities(type="disease")) == 2
    assert len(g.find_entities(name_contains="阑尾")) == 1
    assert g.find_entities(type="disease", name_contains="不存在") == []


def test_multi_hop() -> None:
    """多跳 BFS：险种 → 规则（1 跳）。"""
    g = KnowledgeGraph(_sample_data())
    paths = g.multi_hop("insurance:安心医疗旗舰版", hops=2)
    assert "rule:疾病等待期30天" in paths
    assert paths["rule:疾病等待期30天"] == ["insurance:安心医疗旗舰版", "rule:疾病等待期30天"]


def test_stats() -> None:
    """统计输出实体数/关系数/类型分布/度。"""
    g = KnowledgeGraph(_sample_data())
    s = g.stats()
    assert s["entities"] == 4
    assert s["relations"] == 3
    assert s["entities_by_type"]["disease"] == 2
    assert s["relations_by_type"]["covers"] == 2
    assert s["top5_degree"][0] == 3  # 安心医疗：出度 3


# ===== 三元组容错 =====


def test_build_from_triples_dedup_and_skip() -> None:
    """重复实体去重合并；非法三元组跳过不中断。"""
    triples = [
        # 合法 ×2（同一实体重复出现）
        {"source": {"id": "insurance:A", "type": "insurance", "name": "A"},
         "target": {"id": "disease:K35", "type": "disease", "name": "K35"},
         "relation": "covers", "evidence": "kb03"},
        {"source": {"id": "insurance:A", "type": "insurance", "name": "A"},
         "target": {"id": "rule:等待期", "type": "rule", "name": "等待期"},
         "relation": "applies_to_rule", "evidence": "kb01"},
        # 非法（id 前缀不匹配）→ 跳过
        {"source": {"id": "bad-id", "type": "insurance", "name": "x"},
         "target": {"id": "disease:K35", "type": "disease", "name": "K35"},
         "relation": "covers"},
    ]
    data = build_graph_from_triples(triples, source_files=["a.md"])
    assert len(data.entities) == 3  # A / K35 / 等待期（非法条的 bad-id 被跳过）
    assert len(data.relations) == 2
    assert data.source_files == ["a.md"]


# ===== 落盘回读 =====


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """落盘 → 加载往返：实体关系无损。"""
    data = _sample_data()
    path = tmp_path / "kg.json"
    save_graph(data, path)
    g = load_graph(path)
    assert len(g) == 4
    assert len(g.neighbors("insurance:安心医疗旗舰版")) == 3


def test_load_missing_file_returns_empty_graph(tmp_path: Path) -> None:
    """文件不存在返回空图（检索侧兜底）。"""
    g = load_graph(tmp_path / "nope.json")
    assert len(g) == 0
    assert g.stats()["relations"] == 0
