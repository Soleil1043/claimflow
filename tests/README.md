# tests/ —— 单元与集成测试

目录结构与源码一一对应（`tests/nodes/` 对 `nodes/`，`tests/services/` 对 `services/`…）。
全量 **528 passed**，CI 每次 push 全跑。

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
- **多实例租约**：交付队列 CAS 认领 + 租约过期回收（T155）；
- **Prompt 缓存指标**（T162/T164/T165）：
  - `_extract_cache_usage` 双来源（DeepSeek 非标字段 / langchain 标准字段回退 / 无字段不记）
  - **stage 维度分列**（`track_phase` 上下文携带，默认 `other` 向后兼容）
  - `_model_name` 沿 `.bound`/`.first` 递归探测（结构化输出包装产物无 `model_name`）
  - `LlmUsageCallbackHandler` 成功/错误路径（create_agent 子图口径）
  - **结构化输出分支**：`with_structured_output` 返回纯 Pydantic 对象（无
    `usage_metadata`），usage 只能经 `on_llm_end` 的 `llm_output` 取——判据是
    「是否自带模型身份属性」而非 `isinstance(BaseChatModel)`（测试替身同为
    非 BaseChatModel 但直返 AIMessage，用 isinstance 会误判）
- **Prompt 数据边界**（T165 跨消息）：system 全静态（无快照内容）+ user 带
  `<<<DATA…>>>DATA` 定界，注入文本被关在数据区内；
- **评测门**：`tests/evals/` 数据集校验 + 门限边界 + 红队变体期望继承（T159）。

运行：`uv run pytest -q`。
