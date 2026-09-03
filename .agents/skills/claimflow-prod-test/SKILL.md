---
name: claimflow-prod-test
description: 在本机对 claimflow 做生产环境（Docker prod profile）全链路测试。用户说"测试生产环境/生产部署验证/prod 冒烟/跑一遍 prod/验证 docker 部署"时使用。覆盖：预检（端口冲突/原生服务探测）、起栈自举、模型缓存灌卷、功能冒烟（对话/记忆/缓存/重启恢复）、评测回归、监控追踪栈、结果报告。
---

# claimflow 生产环境测试

把 Docker 化的 prod 栈（PostgreSQL+pgvector / Qdrant / Redis / init 自举 / app）当作真实生产部署，
分层验证并输出一份通过/发现问题报告。流程按阶段推进，**每阶段的验收标准不过就不进下一阶段**。

工作目录：`D:\Code\PythonProjects\claimflow`（下文相对路径均基于它）。
本机（Windows + Git Bash + Docker Desktop）特有坑很多，先读 [references/gotchas.md](references/gotchas.md)
再动手——尤其涉及 docker run 路径、中文 JSON、宿主机端口的部分。

## 阶段 0：预检

```bash
# 1. key 已配置（值隐藏，只看存在性）
grep -c "^LLM_API_KEY=sk-" .env
# 2. 端口占用——必须用 netstat，HTTP 探测对 5432/6379 等数据库端口无效（永远 000）
netstat -ano | grep -E ":8000|:5432|:6333|:6379" | grep LISTENING
# 3. Docker 存活 + 磁盘
docker version --format '{{.Server.Version}}'; df -h / | tail -1
```

**宿主机原生服务判定**：5432/6379 若被 `postgres.exe`/`redis-server.exe`（非 com.docker.backend）占用，
compose 的端口发布会**静默失败**（容器内网正常，宿主机 `localhost:5432` 连的是原生库）。
此时宿主机进程（评测等）必须用 override 端口，见 [assets/compose.prodtest.yaml](assets/compose.prodtest.yaml)
（postgres→15432、redis→16379），后续所有 compose 命令带：
`-f docker-compose.yml -f .agents/skills/claimflow-prod-test/assets/compose.prodtest.yaml`

## 阶段 1：起栈（含自举验证）

```bash
docker compose up -d --build          # 有端口冲突则按阶段 0 加 -f override
docker compose ps                     # postgres/qdrant/redis healthy；init Exited(0)；app Up
docker compose logs init | tail -5    # 必须看到 alembic 迁移 + seed_policies_done + kb_ingest_done
curl http://localhost:8000/health     # {"status":"ok"} 且四依赖 ok
```

init 容器自动完成 迁移→Mock种子→知识库向量化（BUG-004 修复后的自举链路），app 以
`service_completed_successfully` 依赖它。首次启动若 hf_cache 卷为空会在线下载 BGE-M3（~2.3G，
弱网可能卡死）——**建议先做阶段 2 灌缓存再起栈**。

验收：health 200；`docker compose exec postgres psql -U claim -d claim_agent -c "\dt"` 有 13+ 张表
（含 store/store_vectors 长期记忆表）；`curl http://localhost:6333/collections/claim_rules` points_count>0。

## 阶段 2：模型缓存灌卷（可选但推荐，弱网必做）

把宿主机 HF 缓存灌进 `claimflow_hf_cache` 卷，二次起栈秒级加载（容器守卫会自动发现快照目录并
离线直载）。用 [scripts/seed_hf_cache.sh](scripts/seed_hf_cache.sh)：

```bash
bash .agents/skills/claimflow-prod-test/scripts/seed_hf_cache.sh
```

⚠️ 布局必须严格：卷根下是 `hub/models--BAAI--bge-m3/...`。灌完务必按脚本尾部的自检命令从
**容器挂载视角**（`-v claimflow_hf_cache:/root/.cache/huggingface`）验证 `Path.exists()`，
不要只信 alpine 侧 ls——双重嵌套是本流程踩过最深的坑（详见 gotchas.md §HF）。

## 阶段 3：功能冒烟（真实 LLM）

```bash
# 3.1 主链路锚点：回答必须含 4640（mock 张伟：(15800-10000)*80%）
printf '{"content":"我是张伟，身份证号330106199203154817，做了急性阑尾炎手术住院花了15800元，能赔多少"}' > /tmp/q1.json
CONV=$(curl -s -X POST http://localhost:8000/api/v1/conversations -H "Content-Type: application/json" -d '{}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['conversation_id'])")
curl -s -m 180 -X POST "http://localhost:8000/api/v1/conversations/$CONV/messages" \
  -H "Content-Type: application/json" --data-binary @/tmp/q1.json | python -c "..."
# 3.2 记忆闭环：同会话发 3 轮（触发写入）→ 新会话问"我上次问的那张保单的免赔额"
docker compose logs app | grep -a "memory_written\|memory_context_injected\|memory_search_done\|memory.*_failed"
# 3.3 工具缓存：redis 键应含 record_query（白名单曾把注册名写错，已修）
docker compose exec redis redis-cli keys '*'
# 3.4 容器内验证脚本（宿主机跑的是 dev profile，必须 exec 进容器）
docker compose exec -T app sh -c "uv run --no-dev python scripts/verify_e2e.py"
docker compose exec -T app sh -c "uv run --no-dev python scripts/verify_compliance.py"
# 3.5 重启恢复：restart app 后拿旧会话追问，回答应引用此前上下文
```

验收：4640 命中；日志出现 `memory_written` + 跨会话 `memory_context_injected hits:1` 且**无**
`memory_*_failed`；verify_e2e 末行 "T021 验收通过"。记忆问题排查见 gotchas.md §记忆。
注意：restart 会杀死容器内的 exec 会话，别和 3.4 并行跑。

## 阶段 4：评测回归（宿主机对 prod 栈）

```bash
# 端口无冲突时用 5432/6379，有冲突用 override 的 15432/16379
APP_PROFILE=prod POSTGRES_HOST=localhost POSTGRES_PORT=15432 \
QDRANT_URL=http://localhost:6333 REDIS_URL=redis://localhost:16379/0 \
  uv run python -m evals.test_suite --limit 20 --out .workbuddy/prod_smoke.json
```

验收：完成率 ≥95%（FAQ 类 20 条基线 95-100%）；**向量命中 ~3.8 条/例**——若为 0 说明 RAG 静默降级，
按 gotchas.md §Qdrant 排查。Windows 下事件循环已由 `app/core/eventloop.py` 处理，直接跑即可。

## 阶段 5：监控与追踪

```bash
docker compose --profile monitoring up -d   # Prometheus :9090 抓取 up + Grafana :3000 免登录
curl -s http://localhost:8000/metrics | grep -c "^claimflow"   # 裸指标 >50 行
docker compose --profile tracing up -d      # Jaeger :16686
# 容器上报追踪：.env 设 OTEL_ENABLED=true 后 docker compose up -d app 重建
```

## 阶段 6：报告与收尾

输出一份含 通过项表格 / 发现的问题（P0-P3 分级）/ 与基线对比（evals/reports/baseline.json）/
未覆盖项 的报告。栈的处置要问用户：保留演示 or `docker compose --profile monitoring --profile tracing down`
（**不要带 -v**——会清掉 hf_cache 卷，弱网重建代价大；确要全新自举测试才 `-v` 且先重跑阶段 2）。

## 已知不算失败的情况

- FAQ-015 单条失败与 baseline 相同 → 模型方差非回归。
- 回答不复算 4640 但正确引用历史话题 → F03 模型行为方差（worker 可能改用 mock 他人证号）。
- Jaeger 中单轮对话拆成多条 trace → 已知 span 上下文不共享，低优先。
