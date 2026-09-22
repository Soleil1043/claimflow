# schemas/ —— Pydantic 模型层

全部类型注解的单一事实来源。节点 / 服务 / API / 评测共享这些定义，
改字段先看消费方（grep 引用）再动手。

## 文件清单

| 文件 | 职责 |
|------|------|
| `case.py` | 案件模型：CaseInput / CaseOutput / CaseStatus 状态机 / 终态与签发集合单源 |
| `stages.py` | **StageSpec 注册表**：六阶段的名字 / 结论 channel / 产出模型 / 前置 / 快照字段 / 回边唯一权威（D040） |
| `lines.py` | **险种 pack**：四线（medical/auto/property/accident）的受理分类 / 材料清单 / 条款 / 除外关键词 / 责任兜底规则 / 材料目录派生 |
| `contract.py` | **规格契约**：自动签发线 / 等待期 / 调度预算 / 金额公式——运行时、金样本期望、评测门阈值三方引用同一常量 |
| `api.py` | API 请求/响应模型（案件 / 工单 / 客服 / 记忆组） |
| `tools.py` | 工具入参出参模型 |

## 三大单源

1. **StageSpec**（stages.py）——加阶段只改一处，守卫 / 图回边 / prompt 清单 / 评测断言全派生；
2. **InsuranceLinePack**（lines.py）——上新险种 = 加一份声明式数据，节点零改动；
3. **contract.py**——评测门 = 运行时门，不会漂移。

改任何一份都必须跑金样本回归（`evals/adjudication_suite`）。
架构见 [`../docs/architecture.md`](../docs/architecture.md) §6/§7/§14。
