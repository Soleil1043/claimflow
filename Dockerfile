# 保险理赔智能核赔平台 — 运行镜像
# Python 3.12 + uv（torch 走 CPU 源，见 pyproject.toml [tool.uv]）
FROM python:3.12-slim

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# 安装 uv（pip 走阿里云 PyPI 镜像；ghcr.io 国内不可达，不用 COPY --from=ghcr.io/astral-sh/uv）
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv

WORKDIR /app

# 依赖层：只拷贝清单，利用构建缓存
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 源码层：安装项目本身
COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8000

# 模型缓存命中时注入 EMBEDDING_MODEL_PATH（快照目录直载，绕开 hub 缓存校验与在线
# 版本检查；Windows 拷入的真实文件快照在 Linux 会被 hub 判为无效缓存）+ HF_OFFLINE。
# 选中"含 config.json 的快照目录"而非第一个目录（T156 实锤：失败下载留下的幽灵
# 快照只有悬空符号链接，字典序靠前被 head -1 选中 → 容器内加载空壳崩溃）；
# -maxdepth 2 排除 onnx/ 等子目录里的同名文件
CMD ["sh", "-c", "MODEL_DIR=$(find /root/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots -mindepth 2 -maxdepth 2 -name config.json 2>/dev/null | head -n 1 | xargs -r dirname); if [ -n \"$MODEL_DIR\" ]; then export EMBEDDING_MODEL_PATH=\"$MODEL_DIR\" HF_OFFLINE=1; fi; uv run --no-dev alembic upgrade head && uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port 8000"]
