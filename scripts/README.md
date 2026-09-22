# scripts/ —— 种子与验证脚本

全部可重复执行（幂等），`uv run python -m scripts.<name>` 调用。

## 种子与数据

| 脚本 | 职责 |
|------|------|
| `seed.py` | 建库种子：mock 保单 / 病历 / 出险记录 / 黑名单 / 金样本案件（两遍幂等） |
| `gen_adjudication_cases.py` | 金样本生成器：确定性枚举 153 案（含三线与未上线组），重跑字节级一致 |
| `build_kg.py` | 知识图谱构建：LLM 从 kb_docs 抽三元组，幂等重建 |
| `rebuild_memories.py` | 申请人记忆离线重建（从 cases 表终态重渲染） |

## 验证与冒烟

| 脚本 | 职责 |
|------|------|
| `verify_adjudication.py` | 端到端冒烟：完整档 23 项（真 Key）/ `--offline` 离线档 22 项（零 Key，CI 默认） |
| `verify_orchestrator.py` | 路由专项验证（金样本路由一致率） |
| `smoke_support_e2e.py` | 客服全链路冒烟：问答 / 查进度 / 转人工 / 坐席闭环（T137） |
| `verify_rag.py` / `verify_rerank.py` / `verify_ocr.py` | RAG / 精排 / OCR 专项 |
| `compare_memory_experiment.py` | 记忆 A/B 对比（T102 证据脚本） |

## 约定

- 冒烟脚本走真实链路（现场生成 Word 材料上传），不是 mock 断言；
- 生成器改动后必须重跑数据集并确认字节级一致（`schemas/contract.py` 锚定期望）。
