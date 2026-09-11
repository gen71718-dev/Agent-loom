"""图的行为测试：验证"循环、路由、记忆"三件核心事实，全部离线运行。"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent_loom.graph import build_agent
from agent_loom.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, llm_api_key="sk-test")


def _tool_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "calculator", "args": {"expression": "2+2"}, "id": "call_1"}],
    )


async def test_agent_loops_through_tool_and_back(settings, make_scripted_model) -> None:
    """模型要求调工具 -> 工具真的被执行 -> 结果回到模型 -> 模型给出最终答案。"""
    graph = build_agent(
        settings,
        checkpointer=InMemorySaver(),
        llm=make_scripted_model([_tool_call(), AIMessage(content="答案是 4。")]),
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage("2+2 等于几")]},
        {"configurable": {"thread_id": "t-tool"}},
    )

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_messages, "工具没有被执行"
    assert tool_messages[0].content == "4"
    assert result["messages"][-1].content == "答案是 4。"


async def test_history_persists_across_turns(settings, make_scripted_model) -> None:
    """同一个 thread_id 的第二轮请求，能看到第一轮的全部消息。"""
    graph = build_agent(
        settings,
        checkpointer=InMemorySaver(),
        llm=make_scripted_model([AIMessage(content="第一次回答")]),
    )
    config = {"configurable": {"thread_id": "t-memory"}}
    await graph.ainvoke({"messages": [HumanMessage("第一句")]}, config)
    second = await graph.ainvoke({"messages": [HumanMessage("第二句")]}, config)

    contents = [m.content for m in second["messages"]]
    assert contents == ["第一句", "第一次回答", "第二句", "第一次回答"]


async def test_threads_are_isolated(settings, make_scripted_model) -> None:
    """不同 thread_id 之间没有记忆泄漏——这是多租户场景的底线。"""
    graph = build_agent(
        settings,
        checkpointer=InMemorySaver(),
        llm=make_scripted_model([AIMessage(content="回答")]),
    )
    await graph.ainvoke(
        {"messages": [HumanMessage("会话 A")]}, {"configurable": {"thread_id": "a"}}
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage("会话 B")]}, {"configurable": {"thread_id": "b"}}
    )

    assert [m.content for m in result["messages"]] == ["会话 B", "回答"]
