# 构建日志 (Progress)

> Phase 4 产出。每完成一个任务追加一条记录，只追加不删除。
> 格式：时间 | 任务编号 | 操作 | 涉及文件 | 验证方式 | 状态

---

## 日志条目

<!-- 每条记录格式：
### [T0XX] 任务名称 — YYYY-MM-DD HH:MM

**操作**：
- 创建/修改了哪些文件

**涉及文件**：
- `path/to/file.py` — [做了什么]

**验证方式**：
- [运行什么命令 / 看什么结果]

**状态**：✅ 通过验证 / ❌ 有问题（附描述）

**Git**：`abc1234` feat: T0XX 任务名称
-->

### [T001] 项目初始化与目录结构 — 2026-08-24

**操作**：
- 创建 pyproject.toml（Python 3.12 锁定、全量依赖、ruff/pytest 配置）
- 创建 .env.example（APP_PROFILE / LLM 双模型 / PG / Qdrant / Redis / Embedding 配置模板）与 .gitignore
- 按 AGENTS.md 第 5 节创建目录树（app/agents/nodes/tools/services/workflows/schemas/ui/scripts/data/evals/tests/grafana/prometheus/alembic），Python 包带 __init__.py，空目录带 .gitkeep
- `uv sync` 安装 146 个依赖（torch 2.13、langgraph、langgraph-checkpoint-postgres、qdrant-client、sentence-transformers 6.0、gradio 等）
- 核心包导入验证通过（含 AsyncPostgresSaver）
- git 初始化并首次 commit

**涉及文件**：
- `pyproject.toml` — 项目与依赖定义
- `.env.example` / `.gitignore` — 配置模板与忽略规则
- 目录树 + `__init__.py` × 18 + `.gitkeep` × 10
- `uv.lock` — 依赖锁文件

**验证方式**：
- `uv sync` exit 0，146 包安装成功
- `uv run python -c "import fastapi, langgraph, ..."` 输出 all imports OK

**状态**：✅ 通过验证

**问题与修正**：
- plan 中包名 `langgraph-checkpoint-postgresql` 有误，PyPI 实际为 `langgraph-checkpoint-postgres`（3.1.2），已修正（见 decisions.md D009）
- psycopg 纯 Python 实现在 Windows 缺 libpq，补 `psycopg[binary,pool]`（见 D009）

---

## 问题追踪

<!-- 遇到的问题记录在此，方便回溯 -->
| 编号 | 任务 | 问题 | 解决方案 | 状态 |
|------|------|------|---------|------|
| - | - | - | - | - |
