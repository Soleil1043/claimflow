# scripts/ —— 种子、验证与诊断脚本

全部可重复执行（幂等），`uv run python -m scripts.<name>` 调用。

## 种子与数据

| 脚本 | 职责 |
|------|------|
| `seed.py` | 建库种子：mock 保单 / 病历 / 出险记录 / 黑名单 / 金样本案件（两遍幂等） |
| `gen_adjudication_cases.py` | 金样本生成器：确定性枚举 153 案（含四线与未上线组），重跑字节级一致 |
| `build_kg.py` | 知识图谱构建：LLM 从 kb_docs 抽三元组，幂等重建 |
| `rebuild_memories.py` | 申请人记忆离线重建（从 cases 表终态重渲染） |
| `download_bge_ci.py` | BGE-M3 权重预热（CI 冷环境离线，避开 huggingface.co 不可达） |
| `load_test.py` | 负载压测（吞吐 / P95 / 错误率） |

## 验证与冒烟

| 脚本 | 职责 |
|------|------|
| `verify_adjudication.py` | 端到端冒烟：完整档 23 项（真 Key）/ `--offline` 离线档 22 项（零 Key，CI 默认） |
| `verify_multiinstance.py` | 双实例 live 冒烟（T156）：A 提交 → B 补件 → 终态 + 零 schema_reset = 跨实例证明；`--boot` 本地自起双实例，进 CI 的 replicas 阶段 |
| `verify_orchestrator.py` | 路由专项验证（金样本路由一致率） |
| `smoke_support_e2e.py` | 客服全链路冒烟：问答 / 查进度 / 转人工 / 坐席闭环（T137） |
| `verify_rag.py` / `verify_rerank.py` / `verify_ocr.py` | RAG / 精排 / OCR 专项 |
| `compare_memory_experiment.py` | 记忆 A/B 对比（T102 证据脚本） |
| `eval_rag.py` | RAG 质量评测运行器（T161）：Recall@1/2/4/8 + MRR + top_k 敏感度 + RAGAS 四指标（deepseek 判分 + BGE-M3 反向问题嵌入） |

## 红队与消融（T159/T160）

| 脚本 | 职责 |
|------|------|
| `redteam_adversarial.py` | 红队 CLI（T159）：`--split` 拆分 injection/holdout / `--generate` 生成变体 / `--probe` 编译图实跑判定 / `--archive-md` 归档证据。分类法对齐 Garak promptinject / PyRIT converters-attacks，**自研不引框架本体**（探测对象语义错位 + 依赖重） |

单 Agent 消融运行器在 `evals/single_agent_baseline.py`（不在本目录）。

## Prompt 缓存诊断（T162-T165）

| 脚本 | 职责 | 需要 Key |
|------|------|---------|
| `probe_deepseek_cache_usage.py` | langchain 响应链路探针：全 mock 构造带DeepSeek 非标 usage 字段的 ChatCompletion，实测 `prompt_cache_hit/miss_tokens` 在 usage_metadata / response_metadata / llm_output 三处的存活情况——**决定 `_extract_cache_usage` 提取策略**，langchain-openai 升级后可回归验证 | 否 |
| `measure_cache_hit_rate.py` | 真实命中率测量：1339 字符稳定前缀模拟 skill 规程规模，连打 N 轮打真实 API，输出稳态命中率与冷启动对比 | **是** |

## 约定

- 冒烟脚本走真实链路（现场生成 Word 材料上传），不是 mock 断言；
- 生成器改动后必须重跑数据集并确认字节级一致（`schemas/contract.py` 锚定期望）；
- 诊断脚本与验证脚本分离：前者回答"机制是否成立"（可 mock 免 Key），
  后者回答"本项目现状如何"（需真实环境）。
