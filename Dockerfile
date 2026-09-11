# syntax=docker/dockerfile:1
# uv 官方镜像里同时带了 uv 和 Python 3.12，省掉自己装工具链的步骤
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# 先只拷依赖清单：源码改动不会让依赖层缓存失效，重建镜像只需几秒
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uvicorn", "agent_loom.main:app", "--host", "0.0.0.0", "--port", "8000"]
