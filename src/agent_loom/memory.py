"""记忆层：把 LangGraph 的状态持久化。

checkpointer 的语义是"每执行完一个节点就写一次状态快照"。
它解决三个真实问题：进程重启不丢上下文、服务多副本时任意副本都能续跑、
用户关掉页面回来还能接着说。

本模块提供两种实现，通过 CHECKPOINTER 配置切换：
- redis：生产形态，状态落在 Redis Stack（依赖 RediSearch 索引）
- memory：本地开发形态，状态在进程内存，免 Docker

两者由同一个 LangGraph 接口约束，所以换实现完全不需要动 graph.py。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from .settings import Settings


@asynccontextmanager
async def redis_checkpointer(settings: Settings) -> AsyncIterator[AsyncRedisSaver]:
    """Redis 实现。连接池在整个进程生命周期内共享：检查点是 IO 操作，
    每请求新建连接会迅速打满 Redis 连接数。
    """
    ttl: dict[str, Any] | None = (
        {
            # 单位是分钟。refresh_on_read：用户还在聊就续期，避免聊到一半记忆过期
            "default_ttl": settings.redis_checkpoint_ttl_minutes,
            "refresh_on_read": True,
        }
        if settings.redis_checkpoint_ttl_minutes > 0
        else None
    )
    async with AsyncRedisSaver.from_conn_string(settings.redis_url, ttl=ttl) as saver:
        # asetup() 幂等地创建所需索引，重复调用是安全的
        await saver.asetup()
        yield saver


@asynccontextmanager
async def in_memory_checkpointer() -> AsyncIterator[InMemorySaver]:
    """内存实现：不落盘、不跨进程，仅用于本地开发和测试。

    保留它不是为了"图省事"，而是为了让测试能在没有 Redis 的环境里验证路由与状态逻辑。
    """
    yield InMemorySaver()


@asynccontextmanager
async def build_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver]:
    """按配置选择实现，交给 FastAPI 的 lifespan 管理生命周期。"""
    if settings.checkpointer == "memory":
        async with in_memory_checkpointer() as saver:
            yield saver
    else:
        async with redis_checkpointer(settings) as saver:
            yield saver
