# workbench/ —— 坐席工作台

Next.js 15 + React 19 + Tailwind 单页应用，坐席（内勤）侧入口（`:5173`）。

## 功能

- **核赔工单**：挂起案件列表（补件 / 复核 / 升级筛选）→ 详情
  （审计时间线 seq 回放 / 决定书版本卡 / 申请人核赔档案卡）→
  处理表单（签批 / 改判 / 补传 / 升级，坐席文本必过红线复审）；
- **客服工单**（T135）：escalated 会话队列 → transcript 三方气泡
  （user / assistant / agent）→ 坐席回复 / 关闭备注；
- **叙述抽评**（T139）：抽评审队列（pending 样本 + 决定书全文）→
  行内 pass / revise + 评语 → 通过率统计。

## 结构

```
app/cases/      # 核赔工单列表 + [caseId] 详情
app/support/    # 客服工单
app/samples/    # 叙述抽评审
components/     # Timeline / DecisionVersionCard / ApplicantMemoryCard / 气泡组件…
lib/api.ts      # 后端 API 封装（dev 代理 :8000）
```

## 开发

```bash
npm install && npm run dev    # http://localhost:5173
npm run build                 # CI 校验构建
```

设计系统：Apple Design 移植（毛玻璃 sticky 导航 / 分段控件 / 按压反馈 /
reduced-motion 媒体查询），设计令牌与 `ui/`（Gradio 评测台）同源（D030）。
