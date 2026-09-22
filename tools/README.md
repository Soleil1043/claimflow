# tools/ —— 工具层

核赔 Worker 与客服 Agent 消费的工具。全部继承 `ClaimflowTool`（langchain 官方
`BaseTool` 子类，`args_schema` Pydantic 校验，`_arun` 返回 dict）——业务失败是
正常返回值，系统异常交守卫层。

## 结构

```
tools/
├── base.py            # ClaimflowTool 基类
├── guards.py          # GuardedTool：熔断 / 缓存白名单 / 超时（D021 自研保留项）
├── factory.py         # 工厂装配：官方 .with_retry + 守卫（15 个注册工具）
├── claim/             # 保单查询 / 理算器 / 条款 RAG / 案件状态 / 报案链接
├── medical/           # 病历查询 / 诊断匹配 / OCR 提取
├── document/          # 材料分类（doc_type 关键词）/ 完整性规则
├── fraud/             # 欺诈规则评分 / 黑名单 / 历史出险
├── compliance/        # 规则检查 / 敏感脱敏 / 风险评分
└── support/           # 转人工标记（T133）
```

## 约定

- 工具经 `factory.py` 统一装配（重试 / 熔断 / 缓存白名单），不散装 new；
- 幂等工具（policy_query / record_query / claim_rule_rag…）结果走缓存（TTL 可配）；
- Mock 数据从 `data/mock/` 与 DB 种子读取，结构参考真实理赔场景；
- 工具描述面向 LLM 阅读写清楚"何时该用我"。

核赔 worker 不引用客服工具（claim/case_status 等仅客服 Agent 消费）。
架构见 [`../docs/architecture.md`](../docs/architecture.md) §2/§5/§9。
