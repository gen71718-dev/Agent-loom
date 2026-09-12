"""Agent 的核心：一张手工搭建的 ReAct 图。

不直接调高层封装，是因为这张图把 agent 的全部行为摊开在明面上，
每一步跳转你都能看到，出问题也定位得到。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import interrupt

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

    结构就是一个 ReAct 循环，中间插了一道人工审批闸门：

        START -> agent ──有 tool_calls──> review ──放行──> tools -> agent
                    │                         └──拒绝──> agent（把拒绝原因作为工具结果回给模型）
                    └────没有 tool_calls────> END（直接给出最终答案）

    agent 节点：把系统提示词和全部历史消息交给模型，模型自己决定"回答"还是"调工具"。
    review 节点：只有命中 APPROVAL_REQUIRED_TOOLS 的调用才会挂起，等人工决定。
    tools 节点：由 ToolNode 执行模型请求的工具调用，把结果作为 ToolMessage 写回状态。
    tools_condition：官方预置的路由函数，读最后一条消息有没有 tool_calls 来决定走哪条边。

    `llm` 参数用于测试注入替身模型：图的路由与状态合并逻辑值得被真实验证，
    但测试不该真的去调外部 API。
    """
    model = llm or build_llm(settings)
    llm_with_tools = model.bind_tools(TOOLS)
    approval_required = settings.approval_required

    async def call_model(state: AgentState) -> dict[str, list[AnyMessage]]:
        # 系统提示词每轮现拼、不写入状态：它是常量，存进检查点只会让 Redis 里堆满重复内容
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    def review_sensitive_calls(state: AgentState) -> dict[str, list[AnyMessage]]:
        """人工审批闸门。

        关键机制：节点被 interrupt 挂起后，恢复时会**从函数开头重新执行**，
        interrupt() 在同一次调用点返回人工决定。所以 interrupt() 之前必须是纯计算——
        一旦有副作用（写库、发请求），就会被执行两次。
        """
        last = state["messages"][-1]
        calls = list(getattr(last, "tool_calls", None) or [])
        sensitive = [call for call in calls if call["name"] in approval_required]
        if not sensitive:
            return {}

        decision = interrupt(
            {
                "type": "tool_approval",
                "prompt": "以下工具调用有副作用，需要人工确认",
                "calls": [
                    {"id": call["id"], "name": call["name"], "args": call["args"]}
                    for call in sensitive
                ],
            }
        )
        if isinstance(decision, dict) and decision.get("approved"):
            return {}

        # 拒绝时给本批次**所有**调用都补一条 ToolMessage：
        # OpenAI 兼容协议要求每个 tool_call 都有对应结果，缺一条模型会直接报错。
        # 所以这里整批拒绝，让模型重新决策，而不是只挡掉其中一个。
        comment = decision.get("comment") if isinstance(decision, dict) else None
        reason = f"人工审批未通过，未执行。{comment}" if comment else "人工审批未通过，未执行。"
        return {
            "messages": [
                ToolMessage(content=reason, tool_call_id=call["id"], name=call["name"])
                for call in calls
            ]
        }

    def route_after_review(state: AgentState) -> str:
        """放行后最后一条仍是带 tool_calls 的 AIMessage；被拒后追加了 ToolMessage。"""
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else "agent"

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("review", review_sensitive_calls)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "review", END: END})
    builder.add_conditional_edges(
        "review", route_after_review, {"tools": "tools", "agent": "agent"}
    )
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)
