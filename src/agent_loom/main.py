"""应用入口：显式组装依赖，并管理它们的完整生命周期。

这里用 lifespan 而不是全局变量，目的是让"启动顺序"变成可读的代码：
配置 -> 可观测 -> Redis 连接 -> 检查点 -> 编译图。顺序错了（比如先建图再开追踪）
会出现难以排查的行为差异。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .graph import build_agent
from .memory import build_checkpointer
from .observability import setup_observability
from .settings import get_settings

logger = logging.getLogger("agent_loom")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    # 必须在任何模型调用之前完成，否则本次进程的追踪不会被开启
    tracing = setup_observability(settings)
    app.state.settings = settings
    app.state.tracing = tracing
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async with build_checkpointer(settings) as checkpointer:
        app.state.checkpointer = checkpointer
        # 图只编译一次并复用：它内部持有连接池与编译结果，每请求新建会拖垮性能
        app.state.graph = build_agent(settings, checkpointer=checkpointer)
        logger.info(
            "AgentLoom ready | model=%s | redis=%s | tracing=%s",
            settings.llm_model,
            settings.redis_url,
            tracing,
        )
        yield

    await app.state.redis.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="LangGraph + Redis + LangSmith + FastAPI 的单智能体服务",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
