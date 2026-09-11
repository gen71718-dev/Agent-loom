"""集成测试：需要本地 Redis Stack。

没有 Redis 时自动跳过，所以 CI 上不必额外起服务；
但本地开发者可以通过它验证"记忆真的落在 Redis 里"，而不是进程内存。
"""

from __future__ import annotations

import uuid

import pytest
import redis as redis_sync
from langchain_core.messages import AIMessage, HumanMessage

from agent_loom.graph import build_agent
from agent_loom.memory import redis_checkpointer
from agent_loom.settings import Settings


@pytest.fixture
def redis_settings() -> Settings:
    settings = Settings(_env_file=None, llm_api_key="sk-test")
    client = redis_sync.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"本地没有可用的 Redis（{exc}）")
    finally:
        client.close()
    return settings


async def test_memory_survives_across_graph_instances(
    redis_settings: Settings, make_scripted_model
) -> None:
    """写入一次，换一个图实例再读——模拟服务重启或另一个副本。

    这条断言是 checkpointer 存在的唯一理由：状态必须活在 Redis 里。
    """
    thread_id = f"pytest-{uuid.uuid4().hex[:8]}"

    async with redis_checkpointer(redis_settings) as saver:
        graph_a = build_agent(
            redis_settings,
            checkpointer=saver,
            llm=make_scripted_model([AIMessage(content="记住了")]),
        )
        await graph_a.ainvoke(
            {"messages": [HumanMessage("我叫张三")]},
            {"configurable": {"thread_id": thread_id}},
        )

        graph_b = build_agent(
            redis_settings,
            checkpointer=saver,
            llm=make_scripted_model([AIMessage(content="你叫张三")]),
        )
        result = await graph_b.ainvoke(
            {"messages": [HumanMessage("我叫什么")]},
            {"configurable": {"thread_id": thread_id}},
        )

        contents = [m.content for m in result["messages"]]
        assert contents[:2] == ["我叫张三", "记住了"]
        assert contents[-1] == "你叫张三"

        # 清理测试数据，别把开发者的 Redis 堆满
        await saver.adelete_thread(thread_id)
