# 技术方案（Plan）——保险理赔智能核赔平台

> Phase 8 产出（2026-09-04，D037-D039）。**主体架构以 docs/claimflow-新架构设计.md 为准**
> （本文件不重复架构细节，只记录落地映射、选型增量与风险）。
> 状态标记：✅ 已确认 | 🔄 待确认 | ❌ 需修改

**状态**：🔄 待确认

---

## 1. 技术选型（增量）

基础栈沿用 AGENTS.md 第 3 节（LangGraph / FastAPI / PostgreSQL / Qdrant / Redis /
BGE-M3 / uv / pytest / Docker / Prometheus+Grafana），增量与关键口径：

| 项 | 选择 | 依据 |
|---|---|---|
| 编排形态 | orchestrator 循环：`RoutingDecision` 结构化输出 + `Command(goto=[Send...])` 并行 + 静态合规门 | D039 |
| 调度/worker LLM | deepseek-v4-flash（主链路），vision-exp 专职 OCR | D002/D007/D008 |
| skill 机制 | `skills/<stage>/<line>.md` + `services/skills.py` 装载器（纯文本零依赖） | D039 |
| Checkpoint | AsyncPostgresSaver（prod）/ InMemorySaver（dev），thread_id=case_id | D009 |
| 业务表 | cases / case_events（含 kind=routing 审计）/ decision_documents / human_tickets | D006 职责分离原则延续 |
| 金额 | 全链路 Decimal，序列化 str/分 | F08 硬门前提 |
| 结构化输出 | with_structured_output(method="function_calling") | D022/T068 实测口径 |

## 2. 架构要点索引（详见架构设计文档）

- 第四节：orchestrator 设计（守卫表/失败兜底/决策审计/图骨架）——**本方案核心**
- 第五节：ClaimCaseState 分域 + 阶段 Pydantic 模型 + 字段所有权表（并行安全）
- 第八节：HITL 三类工单（interrupt + Command(resume)，移植 T037）
- 第九节：checkpoint 与业务表职责分离 + 幂等
- 第十节：静态合规门 + 金额一致性断言
- 第七/十三节：工具映射与目录结构（含 skills/）

## 3. 里程碑与任务映射（已全部交付）

| 里程碑 | 任务 | 门禁摘要 |
|---|---|---|
| M0 立项 | T077 | spec/plan/tasks/设计文档 就绪 |
| M1 骨架 | T078 域模型 → T079 主图骨架（兜底编排+守卫）→ T080 案件 API | 20 金样本（桩工具）金额/路由断言全绿 |
| M2 LLM 化 | T081 skill+orchestrator → T082 材料 → T083 保单/风控 → T084 责任 → T085 决定书+合规门 | 守卫注入 100% 拦截；金额注入 100% 拦截 |
| M3 HITL | T086 interrupt 全链 → T087 工作台 | 补件/签批/跨重启恢复 e2e |
| M4 评测观测 | T088 金样本判分 → T089 上线门 → T090 埋点 | 硬门全绿 + 路由 ≥95% + 调用 ≤15 |
| M5 收尾 | T091 演示门户 → T092 容器化 → T093 清理收尾 | 全量回归绿 |

依赖链严格串行（工作流约束：不跳依赖、不同时多任务）。

## 4. 风险与对策

| 风险 | 对策 |
|---|---|
| orchestrator 绕圈/失控 | recursion_limit + ModelCallLimit + 守卫必做集强制收敛 + routing_calls_per_case 指标（预算 15） |
| orchestrator 路由错误 | 前置条件守卫（代码层）+ 失败走险种默认计划 + 金样本路由判分（软门 ≥95%） |
| 合规被绕过 | decision_generate→compliance_gate 静态边（图结构断言进 T085 测试） |
| LLM 幻觉金额 | 理算纯确定性工具 + 决定书金额一致性断言（不依赖 LLM） |
| 金额精度 | 全链路 Decimal，序列化 str/分 |
| 多险种 mock 数据缺失 | pack 分批；未上线险种受理转人工；首批只做医疗险 |
| 金样本标注成本 | 程序化生成 + 人工校验（T026/T065 经验） |
| skill 改动回归风险 | skill 变更必须跑金样本回归；接 A/B 框架对比（skill_off/on 变体） |
