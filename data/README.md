# data/ —— 运行数据

全部业务数据为**虚构 Mock**（种子脚本生成的仿真数据，不含任何真实 PII，
结构参考真实理赔场景）。

## 目录

| 目录 | 内容 |
|------|------|
| `mock/` | 种子源数据：保单（四险种）/ 病历 / 出险记录 / 黑名单 / 金样本案件 / OCR 兜底 |
| `kb_docs/` | 核赔知识库 12 篇条款文档（RAG 语料，ingest 向量化入 Qdrant） |
| `graph/` | 知识图谱三元组落盘（`claim_rules_kg.json`，`scripts/build_kg.py` 幂等重建） |
| `qdrant/` | Qdrant local mode 本地向量库（开发零容器） |
| `qdrant-mi-*/` | 多实例冒烟时每实例独立的 Qdrant local 目录（local mode 无跨进程锁，T222） |
| `uploads/{case_id}/` | 案件上传材料落盘（B03 闭环，storage_path 回链） |
| `claim_agent.db` | dev profile SQLite 业务库 |
| `checkpoints.sqlite` | dev 多实例共享 checkpoint（`CHECKPOINT_BACKEND=sqlite`，WAL） |

## 约定

- 种子源在 `mock/`，入库经 `scripts/seed.py`（两遍幂等）；
- 修改 kb_docs 后需重跑 `python -m services.rag.ingest`；
- 大体积 PDF/Word 样本材料也在本目录下（冒烟脚本现场生成，不入库的运行时产物不提交）。
