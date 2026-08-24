# 多智能体保险理赔对话系统 — 运行镜像
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

# prod 容器启动前先执行数据库迁移（连接 PostgreSQL）
CMD ["sh", "-c", "uv run --no-dev alembic upgrade head && uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port 8000"]
