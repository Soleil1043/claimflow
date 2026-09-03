# 生产测试踩坑手册（全部实测验证过）

按排查域分组。每条含症状 → 根因 → 处置。遇到对应症状先查这里，能省大量时间。

## §Git Bash / Windows

- **docker run 的 `/root/...` 路径参数被改写**：Git Bash 把以 `/` 开头的参数转译成本地盘符
  （`/root/...` → `D:/Program Files/Git/root/...`）。处置：命令前 `export MSYS_NO_PATHCONV=1`，
  或把路径包进 `sh -c "..."` 字符串内（字符串内的路径不转译）。
- **中文 JSON 假阳性 422**：Git Bash curl 内联中文走 GBK 编码。处置：`printf '...' > /tmp/q.json`
  再 `--data-binary @/tmp/q.json`。
- **Windows python 读不到 `/tmp`**：Git Bash 的 /tmp 是虚拟路径，`python -c "open('/tmp/...')"` 失败。
  处置：curl 结果直接 `| python -c "import sys,json; r=json.load(sys.stdin)..."` 走管道。
- **psycopg 异步 + Windows**：ProactorEventLoop 不兼容（psycopg 报错），prod 口径入口已由
  `app/core/eventloop.py` 的 run_async 统一切 SelectorEventLoop——新写宿主机 prod 脚本时记得复用。

## §Docker / compose

- **端口发布静默失败**：宿主机原生 postgres/redis 先占了 5432/6379，compose 发布失败不报错，
  容器内网正常但宿主机 `localhost:5432` 连的是原生库（症状：认证失败/数据"丢失"）。
  处置：netstat 查占用进程名；有冲突用 skill 的 assets/compose.prodtest.yaml（15432/16379）。
  **HTTP 探活对数据库端口完全无效**（永远 000），必须 netstat。
- **compose 命令里的 `$VAR` 被吃**：compose 在 YAML 层做变量替换，`$MODEL_DIR` 变空串。
  处置：写 `$$MODEL_DIR`（compose 渲染成 `$`）。曾导致启动守卫永不触发、反复在线连 HF。
- **`docker compose ps` 的 "Up X minutes" 不可信**：本机 Docker Desktop 显示的相对时间经常错。
  处置：`docker inspect <c> --format '{{.State.StartedAt}}'` 拿真实启动时间。
- **restart 杀死 exec 会话**：`docker compose restart app` 会让正在跑的 `exec app` 长任务
  退出码 137（被杀）。处置：exec 类验证脚本与 restart 操作串行。
- **docker cp 不支持宿主路径→stdout**：`docker cp <host-path> -` 报 "must specify at least one
  container source"。处置：宿主侧 `tar -cf - | docker run -i ... tar -xf -` 管道。

## §HF 模型缓存（坑最深的一域）

- **双重嵌套惨案**：tar 流顶层带目录名（如 `huggingface/` 或 `hub/`），解包目录选错一层就会出现
  `/卷根/huggingface/hub/...`。症状极具迷惑性：alpine 视角 `ls` 能看到文件（用 `/v/huggingface/hub`
  路径验证的），容器挂载视角 Python `Path.exists()` 全 False → 模型加载报各种错
  （tokenizer/Pooling/权重找不到）。处置：**灌卷后在容器挂载视角跑 Python exists() 自检**
  （seed_hf_cache.sh 已内置）；修嵌套时注意 `mv dir/* .` 不搬点文件，且目标已存在同名目录时
  mv 会往里再套一层。
- **Windows 缓存快照在 Linux 无效**：Windows HF 缓存是真实文件（无符号链接），Linux
  huggingface_hub 要求快照为指向 blobs 的符号链接，校验不过判无效缓存 → 在线重下载。
  处置：项目已实现 `embedding_model_path` 本地目录直载（绕开 hub 校验），容器守卫自动注入。
- **在线模式校验最新 revision**：缓存快照落后远端时每次启动都 HEAD 校验并可能整模型重下载
  （弱网反复停摆 15+ 分钟）。处置：同上，守卫命中即 `HF_OFFLINE=1` + 本地直载。
- **sentencepiece 依赖**：BGE-M3 是 sentencepiece 分词，离线转换路径需要 `sentencepiece` 包
  （在线模式拉现成 tokenizer.json 会掩盖缺失）。已在 pyproject 固化。

## §记忆（长期记忆 prod 链路）

- **验证看事件不看答案**：跨会话答案可能碰巧来自 mock 默认人设（张伟是第一条种子数据，
  "我上次问的那张保单"可能直接查库命中）。判定标准是日志事件：
  `memory_written` → 新会话 `memory_search_done hits:1` → `memory_context_injected`。
  任何 `memory_*_failed` 都是问题。
- **store.setup 不能进请求路径**：迁移含 `CREATE INDEX CONCURRENTLY`，要等所有先行事务，
  同请求已开的 DB 事务（如 messages 计数）让 CIC 等自己 → 请求挂死。
  处置：已移到 app lifespan 启动期预建。排查此类挂死：
  `pg_stat_activity` 里找 `idle in transaction` + `CREATE INDEX CONCURRENTLY` 等锁对。
- **AsyncPostgresStore 只能用异步接口**：同步 `put/search` 触发 langgraph 主循环守卫直接抛错。
  已统一 `aput/asearch`（同步回退仅为兼容测试桩）。新代码直接调 store 时注意。
- **pgvector 扩展**：Store 向量索引迁移要 `CREATE EXTENSION vector`，compose 已用
  `pgvector/pgvector:pg16` 镜像。换回官方 alpine 镜像会在 setup 时炸。

## §Qdrant / RAG

- **向量命中为 0 = RAG 静默降级**：图谱和 LLM 兜底会掩盖故障（对话看起来正常）。
  评测报告"向量 X 条/例"（正常 ~3.8）是首要观测点；`curl :6333/collections/claim_rules`
  看 points_count。dev 的 Qdrant local mode 与 prod 的 Qdrant 服务是两套数据，
  评测的 dev 隔离副本机制只在 dev profile 生效。
- **client/server 版本窗口**：qdrant-client 与 server minor 差 ≤1，否则启动警告
  （compose 已对齐 v1.18.0 ↔ client 1.19）。升级依赖时记得同步 compose 镜像。

## §评测

- **宿主机跑 verify 脚本 = dev profile**：`scripts/verify_*.py` 是进程内起图，宿主机直跑读的是
  `.env`（APP_PROFILE=dev）+ 本地 Qdrant。要测 prod 必须 `docker compose exec -T app` 进容器跑，
  或宿主机显式 `APP_PROFILE=prod + 端口 override`。
- **FAQ-015 基线即失败**：与 baseline.json 相同的失败不是回归。
- **e2e 场景 3 模型方差**：worker 重查记录时模型可能改用 mock 库他人证号（1101...8888 是
  另一持有者），不复算 4640 不算链路故障——断言只保"历史保留 + 阑尾炎回忆"。
