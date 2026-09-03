#!/usr/bin/env bash
# 灌注 hf_cache 卷：宿主机 HF 缓存 → claimflow_hf_cache 命名卷（幂等，先清后灌）。
# 用法：bash .agents/skills/claimflow-prod-test/scripts/seed_hf_cache.sh
# 前置：宿主机 ~/.cache/huggingface/hub/models--BAAI--bge-m3 已完整（dev 环境日常在用即完整）。
set -euo pipefail

VOL=claimflow_hf_cache
HOST_HUB="$USERPROFILE/.cache/huggingface"   # Git Bash 下 $USERPROFILE 指向用户目录

# Git Bash 路径转译关闭：docker run 的 /root/... 参数否则会被改写成本地盘符
export MSYS_NO_PATHCONV=1

if [ ! -d "$HOST_HUB/hub/models--BAAI--bge-m3" ]; then
  echo "宿主机缓存缺 BAAI/bge-m3：先在 dev 环境跑一次 uv run python -m services.rag.ingest 下载模型" >&2
  exit 1
fi

docker volume create "$VOL" >/dev/null

# 清空卷（含点文件），保证布局唯一：卷根/hub/models--...
docker run --rm -v "$VOL":/v alpine sh -c "rm -rf /v/* /v/.[!.]* 2>/dev/null; ls -a /v/"

# 关键：在卷根（/v）解包，tar 顶层是 hub/——解错层级会出现 /v/huggingface/hub 双重嵌套，
# 容器挂载后 hub 不在预期路径，加载必挂且症状迷惑（曾因此排查半天，见 gotchas.md §HF）
echo "拷贝中（~6GB，约 1-3 分钟）..."
tar -C "$HOST_HUB" -cf - hub | docker run --rm -i -v "$VOL":/v alpine sh -c "cd /v && tar -xf - && ls /v/"

# 自检：必须从容器挂载视角验证（不是 alpine 的 /v 视角），且验证 Python 的 Path.exists()
# —— 曾出现 ls 能看到但 Python exists() 为 False 的双重嵌套惨案
echo "== 自检：容器挂载视角 =="
docker run --rm -v "$VOL":/root/.cache/huggingface claimflow:ci sh -c \
  "uv run --no-dev python -c \"
from pathlib import Path
snap = next(Path('/root/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots').glob('*'))
pool = snap / '1_Pooling' / 'config.json'
tok  = snap / 'tokenizer.json'
wts  = (snap / 'pytorch_model.bin').exists() or (snap / 'model.safetensors').exists()
assert pool.exists() and tok.exists() and wts, f'快照不完整: {snap}'
print('SNAPSHOT OK:', snap.name)
\""

echo "完成。下次 docker compose up 时 init/app 守卫会自动发现快照并离线直载（秒级）。"
