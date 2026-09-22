# tests/ —— 单元与集成测试

目录结构与源码一一对应（`tests/nodes/` 对 `nodes/`，`tests/services/` 对 `services/`…）。
全量 **444 passed**，CI 每次 push 全跑。

## 约定

- 测试文件命名 `test_<模块名>.py`；核心业务逻辑（工具 / 状态流转 / 容错）必须有单测；
- 外部 LLM 调用一律 mock / 脚本化模型（`set_script` 按测试编排回复）；
- HF 模型元数据校验离线护栏（conftest）防测试挂起；子图缓存隔离防跨用例污染；
- SQLite 测试库用 `tmp_path` 文件库（StaticPool 内存库会被双会话事务污染，T080 实锤）；
- 换库四处伸手收敛到 `services/db/session.py::swap_engine` 公开接口（D050）；
- `tests/exercises/` 为练习残留，已在 pytest norecursedirs 排除，不参与收集。

## 重点测试面

- **图结构断言**：静态合规门不可绕过 / 回边派生一致（看 `builder.edges`）；
- **守卫注入**：违规路由 100% 拦截参数化；
- **interrupt 生命周期**：挂起 → resume → 跨重启恢复；
- **checkpoint 版本门卫**：版本不匹配删旧重跑（T141）；
- **评测门**：`tests/evals/` 数据集校验 + 门限边界。

运行：`uv run pytest -q`。
