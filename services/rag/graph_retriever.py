"""图检索与混合召回（T032，D017 轻量 GraphRAG）。

机制：
1. 实体链接：查询文本与图实体名做子串匹配（双向：查询含实体名 / 实体名含查询中的关键片段）
2. 图扩展：命中实体出发 BFS ≤2 跳，收集路径上的实体与关系，拼装事实三元组
   （如：急性阑尾炎 ←covers— 安心医疗旗舰版 —applies_to_rule→ 等待期30天）
3. 融合：图上下文与 Qdrant 向量检索互补——图给"结构性事实"（谁保障谁、适用什么规则），
   向量给"原文片段"；两路信号一起写入 shared_data 供 synthesize 节点消费

开关：GRAPH_RAG_ENABLED（默认 true；false 时行为与 T031 前完全一致）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from services.rag.knowledge_graph import GRAPH_PATH, KnowledgeGraph, load_graph

log = get_logger(__name__)

# 图扩展跳数：疾病→险种（1）→规则（2）
_MAX_HOPS = 2
# 实体名参与链接的最小长度（防 1-2 字误配；险种名通常较长不受限）
_MIN_LINK_LEN = 3
# 实体链接命中上限（防爆炸）
_MAX_LINKED = 5
# graph_context 注入的最大事实条数（Token 控制）
_MAX_FACTS_IN_CONTEXT = 12


@dataclass
class GraphSearchResult:
    """图检索结果。"""

    matched_entities: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


# 全局图实例（惰性加载，进程内共享；图谱 JSON 只读，无并发问题）
_graph: KnowledgeGraph | None = None
_graph_loaded = False


def get_knowledge_graph() -> KnowledgeGraph:
    """获取图谱单例（文件缺失时返回空图，检索侧无感降级）。"""
    global _graph, _graph_loaded
    if not _graph_loaded:
        _graph_loaded = True
        if settings.graph_rag_enabled:
            _graph = load_graph(GRAPH_PATH)
            if len(_graph) > 0:
                log.info("knowledge_graph_loaded", entities=len(_graph))
        else:
            from services.rag.knowledge_graph import KnowledgeGraphData

            _graph = KnowledgeGraph(KnowledgeGraphData())
    assert _graph is not None
    return _graph


def reset_knowledge_graph() -> None:
    """重置单例（测试用）。"""
    global _graph, _graph_loaded
    _graph = None
    _graph_loaded = False


def _bigrams(s: str) -> set[str]:
    """中文 2-gram 集合（无分词器的轻量跳词匹配用）。"""
    return {s[i : i + 2] for i in range(len(s) - 1)}


def link_entities(query: str, graph: KnowledgeGraph) -> list[str]:
    """实体链接：三级匹配，宽进严出 → 命中实体 id 列表。

    1. 实体名 in query：查询提到完整实体名
    2. query in 实体名：简写匹配（query="阑尾炎" 命中 "急性阑尾炎"）
    3. bigram 重叠率 ≥50%：跳词简写容忍——
       "安心医疗旗舰版" 能命中 "安心医疗保险（旗舰版）"（用户习惯不写"保险"），
       "阑尾炎手术" 能命中 "急性阑尾炎"；重叠率分母是实体名 gram 数，
       实体名越长要求覆盖越多，防"医疗险"被泛泛的"医疗费用"类查询误配
    """
    matched: list[str] = []
    query_grams = _bigrams(query) if len(query) >= _MIN_LINK_LEN else set()
    for entity in graph.find_entities():
        name = entity.name
        if len(name) < _MIN_LINK_LEN:
            continue  # 过短实体跳过（防误配）
        if name in query or (query_grams and query in name):
            matched.append(entity.id)
        elif query_grams:
            name_grams = _bigrams(name)
            if name_grams and len(name_grams & query_grams) / len(name_grams) >= 0.5:
                matched.append(entity.id)
            else:
                continue
        else:
            continue
        if len(matched) >= _MAX_LINKED:
            break
    return matched


# 关系类型 → 人话动词（供 LLM 消费的事实描述）
_VERBS = {
    "covers": "保障",
    "excludes": "不保/除外",
    "applies_to_rule": "适用规则",
    "disease_rule": "适用规则",
}


def _describe(rel_type: str, source: str, target: str) -> str:
    verb = _VERBS.get(rel_type, rel_type)
    return f"{source} [{verb}] {target}"


def _facts_along_path(path: list[str], graph: KnowledgeGraph) -> list[dict[str, Any]]:
    """把一条 BFS 路径转成事实列表：相邻实体对自动识别正向/反向边。

    - 正向：a -[rel]-> b（如 险种 -covers-> 疾病）
    - 反向：b -[rel]-> a（沿入边遍历到的对，事实主语仍是关系源点，
      如从疾病反向走到险种时描述为 "险种 保障 疾病"）
    """
    facts: list[dict[str, Any]] = []
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        rel_found: tuple[str, str, str] | None = None  # (relation_type, source_id, target_id)
        for rel, _ in graph.neighbors(a):
            if rel.target == b:
                rel_found = (rel.type, a, b)
                break
        if rel_found is None:
            for rel, _ in graph.neighbors(a, reverse=True):
                if rel.source == b:
                    rel_found = (rel.type, b, a)
                    break
        if rel_found is None:
            continue
        rel_type, src_id, dst_id = rel_found
        src_e, dst_e = graph.get_entity(src_id), graph.get_entity(dst_id)
        if src_e and dst_e:
            facts.append(
                {
                    "fact": _describe(rel_type, src_e.name, dst_e.name),
                    "source": src_e.name,
                    "relation": rel_type,
                    "target": dst_e.name,
                }
            )
    return facts


def expand_from_entities(entity_ids: list[str], graph: KnowledgeGraph) -> GraphSearchResult:
    """从命中实体出发正反向 BFS ≤2 跳，收集去重的事实三元组。

    反向遍历不可省：covers/excludes 关系是 险种→疾病 方向，
    用户问"XX 病能赔吗"时必须沿入边回溯到保障它的险种。
    """
    facts: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    matched_names = []
    for eid in entity_ids:
        entity = graph.get_entity(eid)
        if entity is None:
            continue
        matched_names.append(entity.name)
        for reverse in (False, True):
            for path in graph.multi_hop(eid, _MAX_HOPS, reverse=reverse).values():
                if len(path) < 2:
                    continue
                for fact in _facts_along_path(path, graph):
                    key = (fact["source"], fact["target"])
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    facts.append(fact)

    facts = facts[:_MAX_FACTS_IN_CONTEXT]
    summary = "\n".join(f"- {f['fact']}" for f in facts)
    return GraphSearchResult(matched_entities=matched_names, facts=facts, summary=summary)


async def search_graph(query: str) -> GraphSearchResult:
    """图检索入口：实体链接 + 多跳扩展。禁用或空图返回空结果（零影响降级）。"""
    graph = get_knowledge_graph()
    if len(graph) == 0:
        return GraphSearchResult()

    matched = link_entities(query, graph)
    if not matched:
        return GraphSearchResult()

    result = expand_from_entities(matched, graph)
    log.info(
        "graph_search_done",
        query=query[:50],
        matched=len(result.matched_entities),
        facts=len(result.facts),
    )
    return result
