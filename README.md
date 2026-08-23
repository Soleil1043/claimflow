# Vibecoding SOP 脚手架

> 适配所有 Agent Harness 的结构化项目构建模板。
> 复制此目录到你的新项目根目录，按 5 阶段流程推进即可。

---

## 快速开始

### 第 1 步：复制脚手架到新项目

脚手架是空模板，复制一份作为新项目的起点。

**方式 A — 文件管理器（推荐）：**

1. 找到 `vibecoding-scaffold` 文件夹，右键复制
2. 粘贴到你的项目存放位置（如 `C:\Users\10432\Projects\`）
3. 将副本重命名为你的项目名（如 `my-project`）

**方式 B — PowerShell：**

```powershell
Copy-Item -Recurse "路径\vibecoding-scaffold" "C:\Users\10432\Projects\my-project"
cd "C:\Users\10432\Projects\my-project"
```

**方式 C — Linux / macOS：**

```bash
cp -r vibecoding-scaffold my-project
cd my-project
```

### 第 2 步：适配你的 Harness 规则文件

根据你使用的 Agent Harness，将 `AGENTS.md` 内容复制到对应文件：

| Harness     | 规则文件             | 自动读取 |
| ----------- | ---------------- | ---- |
| TRAE        | `AGENTS.md`      | ✅    |
| Claude Code | `CLAUDE.md`      | ✅    |
| Cursor      | `.cursorrules`   | ✅    |
| Windsurf    | `.windsurfrules` | ✅    |
| Cline       | `.clinerules`    | ✅    |
| 其他          | `AGENTS.md`      | 手动引用 |

### 第 3 步：编辑 AGENTS.md

打开 `AGENTS.md`，修改以下内容：

- `[PROJECT_NAME]` → 你的项目名
- 技术栈 → 你的实际技术栈
- 代码约定 → 你的团队规范

### 第 4 步：开始 Phase 1

将 `.agent/prompts.md` 中的 **Phase 1 Prompt** 复制到 Agent 对话框，开始需求定义。

---

## 目录结构

```
multi-Agent-insurance-claims-assistant/
├── AGENTS.md              ← 项目规则（Agent 全局指令）
├── README.md              ← 本文件
└── .agent/                ← 项目状态目录（核心）
    ├── spec.md            ← Phase 1: 需求文档模板
    ├── plan.md            ← Phase 2: 技术方案模板
    ├── tasks.md           ← Phase 3: 任务清单模板
    ├── progress.md        ← Phase 4: 构建日志模板
    ├── decisions.md       ← 全程: 决策记录模板
    └── prompts.md         ← 全阶段 Prompt 模板
├── app/                    # FastAPI 应用入口、路由
├── agents/                 # Agent 定义：每个 Agent 有独立配置
├── nodes/                  # 节点逻辑：LLM、工具调用、Router、Guard
├── routes/                 # 路由/决策逻辑：意图分类、条件分支
├── state.py                # 状态定义：Pydantic state、checkpoint 结构
├── tools/                  # 工具定义：检索、计算、外部 API、合规检查
├── services/               # LLM、数据库、记忆、评测服务
├── evals/                  # 评测脚本：工具调用准确率、任务完成率
├── schemas/                # 请求/响应 Pydantic schema
├── alembic/                # 数据库迁移
├── grafana/                # 监控面板
├── prometheus/             # 监控配置
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

---

## 五阶段工作流

| 阶段            | 产出文件                                          | 门禁（用户检查点）            |
| ------------- | --------------------------------------------- | -------------------- |
| Phase 1 需求定义  | `.agent/spec.md`                              | 功能边界清晰，每条功能有验收标准     |
| Phase 2 架构设计  | `.agent/plan.md`                              | 表结构支撑所有功能，API 覆盖所有需求 |
| Phase 3 任务拆解  | `.agent/tasks.md`                             | 任务顺序合理，依赖无环，验收标准明确   |
| Phase 4 逐任务构建 | `src/` + `.agent/progress.md`                 | 每个任务可运行、无报错          |
| Phase 5 验证交付  | `tests/` + `.github/workflows/` + `README.md` | 测试全绿，CI 通过，spec 覆盖完整 |

---

## 使用流程详解

### Phase 1 → 需求定义

1. 复制 `prompts.md` 中的 Phase 1 Prompt 到 Agent
2. Agent 会先问你 3-5 个澄清问题
3. 回答后，Agent 将需求写入 `spec.md`
4. 你检查 `spec.md`：功能边界是否清晰？验收标准是否可验证？
5. 确认后 → 进入 Phase 2

### Phase 2 → 架构设计

1. 复制 Phase 2 Prompt 到 Agent
2. Agent 读取 `spec.md`，将方案写入 `plan.md` 和 `decisions.md`
3. 你检查 `plan.md`：技术选型是否合理？数据模型能否支撑功能？
4. 确认后 → 进入 Phase 3

### Phase 3 → 任务拆解

1. 复制 Phase 3 Prompt 到 Agent
2. Agent 读取 `plan.md`，将任务清单写入 `tasks.md`
3. 你检查 `tasks.md`：任务粒度是否合适？依赖关系是否正确？
4. 确认后 → 进入 Phase 4

### Phase 4 → 逐任务构建（循环）

1. 复制 Phase 4 Prompt 到 Agent
2. Agent 实现 `tasks.md` 中下一个未完成任务
3. 完成后 Agent 更新 `tasks.md`（标记 [x]）和 `progress.md`
4. Agent 告诉你验证方法
5. 你验证 → 通过则回复"继续下一个" → 回到步骤 1
6. 验证 → 不通过则让 Agent 在当前任务内修复

### Phase 5 → 验证交付

1. 所有任务标记 [x] 后，复制 Phase 5 Prompt 到 Agent
2. Agent 运行全量测试、检查 spec 覆盖、补文档
3. 你最终验收 → 交付

---

## 会话中断恢复

会话断了不用担心，所有状态都在文件里。新会话开始时：

1. 复制 `prompts.md` 中的 **上下文恢复 Prompt** 到 Agent
2. Agent 读取 `.agent/` 下所有文件，报告当前状态
3. 从断点继续

---

## Git 检查点

每个阶段和每个任务都是独立 commit，出错可精确回退：

```
Phase 0: chore: init project rules
Phase 1: docs: add spec
Phase 2: docs: add architecture plan
Phase 3: docs: add task breakdown
Phase 4: feat: T001 项目初始化      ← 每个任务一个 commit
         feat: T002 数据库连接
         feat: T003 数据模型定义
         ...
Phase 5: test: full verification pass
```

---

## 关键原则

1. **文件即状态** — 所有进度写入 `.agent/` 文件，不依赖会话记忆
2. **一次一任务** — 不批量实现，完成一项验证一项
3. **确认才放行** — 每个阶段产出必须用户确认后才进入下一步
4. **决策留痕** — 技术选型记录在 `decisions.md`，可追溯
5. **可回退** — Git per-task commit，出错精准回退
