# app/ —— FastAPI 入口与 API 层

只做 HTTP 边界：请求校验、依赖注入、调服务层、组装响应。
业务规则一律下沉 `services/` 与 `nodes/`，本层不写核赔逻辑。

## 结构

```
app/
├── main.py              # FastAPI 实例：lifespan（DB 连接 / BGE 预热 / 交付队列）、路由挂载
├── api/
│   ├── dependencies.py  # 依赖注入（DB session / 服务装配）
│   └── v1/              # RESTful 路由（复数名词）
│       ├── cases.py          # 案件提交 / 详情 / 材料上传 / 材料目录 / 叙述抽评
│       ├── interventions.py  # HITL 工单列表 / 处理 / 抽评审队列
│       ├── support.py        # 在线客服：会话 / 消息 / 坐席工单（T134/T135）
│       ├── memory.py         # 申请人记忆删除（T138）
│       └── health.py         # /health 依赖状态 + /metrics
└── core/
    ├── config.py        # pydantic-settings，全部从 .env 读取（APP_PROFILE 切 dev/prod）
    ├── logging.py       # structlog JSON 结构化日志
    ├── eventloop.py     # 事件循环守卫（防同步阻塞）
    └── exceptions.py    # 异常层级与 HTTP 映射
```

## 约定

- 案件提交为**受理即返回**：建档 + 入交付队列（`services/case_jobs.py`），
  前端轮询终态；小数据量 inline 直驱双档；
- 材料上传：两段式提取（图片/PDF/Word），补件挂起时自动恢复流程；
- 接口口径见根 README §8；API 模型在 `schemas/api.py`。
