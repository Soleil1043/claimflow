"""理赔知识图谱（T031，D017 轻量自建路径）。

从 12 篇 kb_docs 抽取的实体关系三元组构成的内存图：
- 实体类型：险种 / 疾病(ICD-10) / 规则条款（等待期/免赔/赔付比例/免责/材料/时效）
- 关系：covers(险种→疾病) / excludes(险种→疾病) / applies_to_rule(险种→条款) /
  disease_rule(疾病→条款) 等

数据结构：邻接表 + 实体索引（entity_id → 节点），
支持多跳遍历（疾病→险种→规则条款），供 T032 混合召回使用。
图谱 JSON 落盘 data/graph/claim_rules_kg.json，构建脚本幂等重建。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

# ===== 图谱 schema =====


class GraphEntity(BaseModel):
    """图实体。"""

    id: str = Field(description="实体 ID，格式 {type}:{name}，如 'disease:K35 急性阑尾炎'")
    type: str = Field(description="实体类型：insurance / disease / rule")
    name: str = Field(description="实体名称（展示用）")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="附加属性（如 ICD 码、免赔额数值）"
    )

    @model_validator(mode="after")
    def _validate_id(self) -> GraphEntity:
        prefix = f"{self.type}:"
        if not self.id.startswith(prefix):
            raise ValueError(f"实体 id 必须以 {prefix} 开头：{self.id}")
        return self


class GraphRelation(BaseModel):
    """图关系（有向边）。"""

    source: str = Field(description="起点实体 id")
    target: str = Field(description="终点实体 id")
    type: str = Field(description="关系类型：covers / excludes / applies_to_rule / disease_rule 等")
    weight: float = Field(default=1.0, description="关系权重（融合排序用，默认 1.0）")
    evidence: str = Field(default="", description="出处（kb_docs 文件名或片段摘要）")


class KnowledgeGraphData(BaseModel):
    """图谱可序列化整体（落盘格式）。"""

    version: str = "1.0.0"
    entities: list[GraphEntity] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list, description="构建来源文档")
    built_at: str = Field(default="", description="构建时间")


# ===== 内存图结构 =====


class KnowledgeGraph:
    """理赔知识图谱：邻接表 + 实体索引，支持多跳邻接查询。"""

    def __init__(self, data: KnowledgeGraphData) -> None:
        self._entities: dict[str, GraphEntity] = {e.id: e for e in data.entities}
        self._relations = list(data.relations)
        # 正向邻接：source → [(relation, target)]
        self._adjacency: dict[str, list[tuple[GraphRelation, str]]] = {}
        # 反向邻接：target → [(relation, source)]
        self._reverse: dict[str, list[tuple[GraphRelation, str]]] = {}
        for rel in self._relations:
            if rel.source in self._entities and rel.target in self._entities:
                self._adjacency.setdefault(rel.source, []).append((rel, rel.target))
                self._reverse.setdefault(rel.target, []).append((rel, rel.source))

    # ===== 查询接口 =====

    def get_entity(self, entity_id: str) -> GraphEntity | None:
        return self._entities.get(entity_id)

    def neighbors(
        self, entity_id: str, *, relation_types: set[str] | None = None, reverse: bool = False
    ) -> list[tuple[GraphRelation, GraphEntity]]:
        """一跳邻居（可按关系类型过滤；reverse=True 走反向边）。"""
        table = self._reverse if reverse else self._adjacency
        result = []
        for rel, other_id in table.get(entity_id, []):
            if relation_types is None or rel.type in relation_types:
                entity = self._entities.get(other_id)
                if entity is not None:
                    result.append((rel, entity))
        return result

    def find_entities(
        self, *, type: str | None = None, name_contains: str | None = None
    ) -> list[GraphEntity]:
        """按类型/名称模糊查找实体。"""
        result = []
        for e in self._entities.values():
            if type is not None and e.type != type:
                continue
            if name_contains is not None and name_contains not in e.name:
                continue
            result.append(e)
        return result

    def multi_hop(
        self,
        start_id: str,
        hops: int,
        *,
        relation_types: set[str] | None = None,
        reverse: bool = False,
    ) -> dict[str, list[str]]:
        """多跳 BFS：返回 {entity_id: 路径}（路径为实体 id 列表，含起点）。

        供图检索扩展用：疾病 →（1 跳）险种 →（2 跳）适用规则。
        reverse=True 沿入边遍历（covers 等关系多为 险种→疾病，
        从疾病出发需要反向才能找到保障它的险种）。
        """
        table = self._reverse if reverse else self._adjacency
        paths: dict[str, list[str]] = {start_id: [start_id]}
        frontier = [start_id]
        for _ in range(hops):
            next_frontier = []
            for node in frontier:
                for rel, other_id in table.get(node, []):
                    if relation_types is not None and rel.type not in relation_types:
                        continue
                    if other_id not in paths:
                        paths[other_id] = paths[node] + [other_id]
                        next_frontier.append(other_id)
            frontier = next_frontier
            if not frontier:
                break
        return paths

    # ===== 统计（验收要求） =====

    def stats(self) -> dict[str, Any]:
        """图谱统计：实体数/关系数/度分布。"""
        entity_count = len(self._entities)
        by_type: dict[str, int] = {}
        for e in self._entities.values():
            by_type[e.type] = by_type.get(e.type, 0) + 1
        rel_by_type: dict[str, int] = {}
        for r in self._relations:
            rel_by_type[r.type] = rel_by_type.get(r.type, 0) + 1
        degrees = sorted(
            (
                len(self._adjacency.get(eid, [])) + len(self._reverse.get(eid, []))
                for eid in self._entities
            ),
            reverse=True,
        )
        return {
            "entities": entity_count,
            "relations": len(self._relations),
            "entities_by_type": by_type,
            "relations_by_type": rel_by_type,
            "top5_degree": degrees[:5],
            "avg_degree": round(sum(degrees) / entity_count, 2) if entity_count else 0.0,
        }

    def __len__(self) -> int:
        return len(self._entities)


# ===== 落盘/加载 =====


GRAPH_PATH = Path("data/graph/claim_rules_kg.json")


def save_graph(data: KnowledgeGraphData, path: str | Path) -> None:
    """图谱落盘 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_graph(path: str | Path) -> KnowledgeGraph:
    """从 JSON 加载图谱。文件不存在返回空图。"""
    path = Path(path)
    if not path.exists():
        return KnowledgeGraph(KnowledgeGraphData())
    data = KnowledgeGraphData.model_validate_json(path.read_text(encoding="utf-8"))
    return KnowledgeGraph(data)


def build_graph_from_triples(
    triples: list[dict[str, Any]], source_files: list[str] | None = None
) -> KnowledgeGraphData:
    """LLM 抽取的三元组列表 → 图谱数据（实体自动去重收集）。

    三元组格式：{"source": {...实体}, "target": {...实体}, "relation": "covers", "evidence": "..."}
    单条非法跳过（抽取容错）。
    """
    entities: dict[str, GraphEntity] = {}
    relations: list[GraphRelation] = []
    for triple in triples:
        try:
            src = GraphEntity.model_validate(triple["source"])
            dst = GraphEntity.model_validate(triple["target"])
        except Exception:  # noqa: BLE001 非法三元组跳过
            continue
        entities.setdefault(src.id, src)
        entities.setdefault(dst.id, dst)
        relations.append(
            GraphRelation(
                source=src.id,
                target=dst.id,
                type=str(triple.get("relation", "related")),
                evidence=str(triple.get("evidence", ""))[:200],
            )
        )
    return KnowledgeGraphData(
        entities=list(entities.values()),
        relations=relations,
        source_files=source_files or [],
        built_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
