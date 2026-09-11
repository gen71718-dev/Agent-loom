"""Agent 的核心：一张手工搭建的 ReAct 图。

不直接调高层封装，是因为这张图把 agent 的全部行为摊开在明面上，
每一步跳转你都能看到，出问题也定位得到。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from .prompts import SYSTEM_PROMPT
from .settings import Settings
from .tools import TOOLS


class AgentState(TypedDict):
    """图里流动的唯一状态。

    `Annotated[..., add_messages]` 里的函数叫 reducer：节点返回的新消息会被"追加"进列表，
    而不是覆盖整个字段。这是 LangGraph 最关键的约定——状态是共享的，更新靠 reducer 合并。
    换成普通 list 就会出现"每轮只剩最后一条消息"的经典 bug。
    """

    messages: Annotated[list[AnyMessage], add_messages]


def build_llm(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
    )


def build_agent(
    settings: Settings,
    checkpointer: Any | None = None,
    llm: BaseChatModel | None = None,
):
    """编译出可执行的图。

    结构就是一个 ReAct 循环：

        START -> agent ──有 tool_calls──> tools -> agent（回到模型继续想）
                    └────没有 tool_calls────> END（直接给出最终答案）

    agent 节点：把系统提示词和全部历史消息交给模型，模型自己决定"回答"还是"调工具"。
    tools 节点：由 ToolNode 执行模型请求的工具调用，把结果作为 ToolMessage 写回状态。
    tools_condition：官方预置的路由函数，读最后一条消息有没有 tool_calls 来决定走哪条边。

    `llm` 参数用于测试注入替身模型：图的路由与状态合并逻辑值得被真实验证，
    但测试不该真的去调外部 API。
    """
    model = llm or build_llm(settings)
    llm_with_tools = model.bind_tools(TOOLS)

    async def call_model(state: AgentState) -> dict[str, list[AnyMessage]]:
        # 系统提示词每轮现拼、不写入状态：它是常量，存进检查点只会让 Redis 里堆满重复内容
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)
