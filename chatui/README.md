# chatui/ —— 案件提交门户

Next.js 15 + React 19 + Tailwind 4 单页应用，客户侧入口（`:3000`）。

## 功能

- **案件提交**：险种选择 → 表单（保单号 / 金额 / 出险日）→ 材料上传
  （材料目录来自 `GET /api/v1/cases/material-catalog`，pack 单源，新险种上线零改动）；
- **进度时间线**：轮询案件详情，阶段推进自动刷新；
- **决定书查看**：版本化决定书渲染；
- **补件上传**：挂起案件在详情页直接补传，自动恢复流程；
- **悬浮 AI 客服**（T136）：全站气泡——条款问答 / 案件进度查询 / 报案链接预填。
  案件进度查询需会话级授权：在案件详情页打开客服时自动用该案令牌授权（T202）；
  转人工后 3s 轮询坐席回复；closed 终态只读 + 一键新会话。

## 结构

```
app/            # 路由：首页（提交）/ cases/[caseId]（详情）
components/     # CaseForm / MaterialUpload / SupportBubble / AutoRefresh（时间线与决定书内联在详情页）
lib/            # case-api.ts / support-api.ts（后端 8000 代理）
```

## 开发

```bash
npm install && npm run dev    # http://localhost:3000（dev 代理直连后端 :8000）
npm run build                 # CI 校验构建
```

后端需先起（`uv run uvicorn app.main:app --port 8000`）。
客服域架构见 [`../docs/architecture.md`](../docs/architecture.md) §9。
