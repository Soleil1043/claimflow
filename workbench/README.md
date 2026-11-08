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
app/cases/          # 核赔工单列表 + [caseId] 详情
app/support/        # 客服工单
app/samples/        # 叙述抽评审
app/login/          # 坐席登录页（T215）
app/staff-session/  # 登录 / 退出路由：校验 Key 后写 HttpOnly Cookie
middleware.ts       # 浏览器 /api/v1/* 请求从 Cookie 注入 X-Staff-Key（剥除客户端自带头）
components/         # CaseTimeline / CaseResolveForm / ApplicantMemoryCard / SupportReplyForm / StaffSessionLink…
lib/api.ts          # 后端 API 封装（浏览器走同源代理 /api/*，RSC 直连 :8000）
```

## 坐席登录（T215，D092）

后端配置了 `STAFF_KEYS` 时，先在 `/login` 输入分配的坐席 Key。Key 存在 HttpOnly
Cookie 里，浏览器脚本读不到；浏览器请求由 `middleware.ts` 注入 `X-Staff-Key`，服务端
组件从 Cookie 读取后注入。导航栏可退出。后端未配置 `STAFF_KEYS`（dev 开放态）时
无需登录。旧的 `NEXT_PUBLIC_STAFF_KEY` 已不再使用。

## 开发

```bash
npm install && npm run dev    # http://localhost:5173
npm run build                 # CI 校验构建
```

设计系统：Apple Design 移植（毛玻璃 sticky 导航 / 分段控件 / 按压反馈 /
reduced-motion 媒体查询，D030）。
