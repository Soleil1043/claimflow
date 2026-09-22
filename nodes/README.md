# nodes/ —— 核赔主图节点

LangGraph 主图（`workflows/case_graph.py`）的节点实现：**只做编排与状态流转**，
确定性业务规则下沉 `services/`，LLM 交互经 `services/llm/` 与 `services/worker_agent.py`。

## 节点清单

| 文件 | 职责 |
|------|------|
| `intake.py` | 受理论证：保单产品类型 → 险种分类；unknown 转人工（escape）；写 schema_version |
| `orchestrator.py` | LLM 调度装配：RoutingDecision 结构化输出 + Send 并行派发 + 审计 |
| `guards.py` | 确定性守卫纯函数层：enforce_guards / default_route / 前置查表（T118 拆出） |
| `material_review.py` | 三段流水线：@task 并行提取 → 完整性/金额交叉规则 → AI 一致性审查 |
| `policy_verify.py` | 保单核验：等待期 / 除外 / 限额（条款要素由 pack 提供） |
| `fraud_check.py` | 风控筛查：黑名单 / 历史出险双信号，高风险短路 |
| `liability_judge.py` | 责任认定三段：确定性前置 → ReAct Agent → 关键词兜底 |
| `amount_calc.py` | 金额理算：纯确定性（`services/amounts.py` 规格公式） |
| `decision_generate.py` | 决定书生成：骨架代码渲染 + LLM 只写叙述段 |
| `compliance_gate.py` | 静态合规门：金额三方断言 + 红线检查，三态（PASS/MODIFY/REJECT） |
| `auto_adjudicate.py` | 分级自动：低风险小额签发；其余转 human；叙述 5% 采样（T139） |
| `human_gate.py` | 人工介入门：interrupt 挂起；三类工单决议纯函数（resolve_review） |

## 关键约定

- **阶段知识单源**：六阶段的名字 / 前置 / 快照字段 / 回边全部查 `schemas/stages.py`
  StageSpec 注册表，节点内不复制阶段清单；
- **险种知识单源**：材料清单 / 条款 / 除外关键词查 `schemas/lines.py` pack；
- interrupt 消费节点返回普通 dict + 条件边路由（返回 Command 会让挂起记录残留）；
- worker 阶段耗时经 `_timed` 包装出 OTel span 与 Prometheus 指标。

架构细节见 [`../docs/architecture.md`](../docs/architecture.md) §3-§8。
