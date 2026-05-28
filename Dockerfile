FROM python:3.12-slim

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 复制应用代码
COPY dashboard/cache.py \
     dashboard/gitlab_client.py \
     dashboard/server.py \
     dashboard/dashboard.html \
     ./

COPY scripts/analyze.py ./analyze.py
COPY scripts/period_utils.py ./period_utils.py
COPY scripts/members.json ./scripts/members.json

ENV DASHBOARD_PORT=8880
EXPOSE 8880

CMD ["uv", "run", "python", "server.py"]
