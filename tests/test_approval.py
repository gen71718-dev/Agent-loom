"""人工审批（human-in-the-loop）的行为测试：全部离线运行，不需要 Redis 与外网。

这一版新增的核心能力是"副作用工具执行前必须有人点头"。测试要盯住四个事实：
1. 挂起期间工具绝不执行（审批的价值就在这里，执行了就等于没拦）；
2. 批准后工具真的执行；
3. 拒绝后不执行，且**每个** tool_call 都要补上 ToolMessage（协议要求，见 graph.py 注释）；
4. 租户隔离在审批链路上依旧成立——审批入口同样不能越权。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_loom.graph import build_agent
from agent_loom.main import create_app
from agent_loom.settings import Settings


def _settings() -> Settings:
    """默认 approval_required_tools="notify"，正好用来验证审批分支。"""
    return Settings(_env_file=None, llm_api_key="sk-test")


def _notify_call(call_id: str = "call_notify_1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "notify",
                "args": {"channel": "email", "message": "部署完成"},
                "id": call_id,
            }
        ],
    )


def _calculator_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "calculator", "args": {"expression": "2+2"}, "id": "call_calc_1"}
        ],
    )


def _tool_messages(result: dict) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


async def test_sensitive_tool_suspends_without_executing(make_scripted_model) -> None:
    """要审批的工具被请求时，图必须停在 review 节点，而不是顺手执行掉。"""
    graph = build_agent(
        _settings(),
        checkpointer=InMemorySaver(),
        llm=make_scripted_model([_notify_call(), AIMessage(content="已通知")]),
    )
    config = {"configurable": {"thread_id": "t-approval"}}
    result = await graph.ainvoke({"messages": [HumanMessage("通知一下")]}, config)

    interrupts = result["__interrupt__"]
    assert interrupts[0].value["type"] == "tool_approval"
    assert interrupts[0].value["calls"][0]["name"] == "notify"
    assert _tool_messages(result) == [], "挂起期间工具被执行了，审批形同虚设"


async def test_regular_tool_never_suspends(make_scripted_model) -> None:
    """只读工具（calculator）不在审批名单里，应当直接执行，不能被闸门误伤。"""
    graph = build_agent(
        _settings(),
        checkpointer=InMemorySaver(),
        llm=make_scripted_model([_calculator_call(), AIMessage(content="等于 4")]),
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage("2+2")]}, {"configurable": {"thread_id": "t-plain"}}
    )

    assert "__interrupt__" not in result
    assert [m.content for m in _tool_messages(result)] == ["4"]


async def test_resume_approved_executes_tool(make_scripted_model) -> None:
    """批准后，Command(resume=...) 把决定送回 interrupt 调用点，图接着往下跑。"""
    graph = build_agent(
        _settings(),
        checkpointer=InMemorySaver(),
        llm=make_scripted_model([_notify_call(), AIMessage(content="已通知")]),
    )
    config = {"configurable": {"thread_id": "t-approve"}}
    await graph.ainvoke({"messages": [HumanMessage("通知一下")]}, config)
    result = await graph.ainvoke(Command(resume={"approved": True}), config)

    executed = _tool_messages(result)
    assert len(executed) == 1
    assert executed[0].name == "notify"
    assert executed[0].content.startswith("已提交到 email 渠道")
    assert result["messages"][-1].content == "已通知"


async def test_resume_rejected_skips_tool_and_fills_every_call(make_scripted_model) -> None:
    """拒绝时不执行，且每个 tool_call 都要有 ToolMessage——少一条模型会直接报协议错。"""
    two_calls = AIMessage(
        content="",
        tool_calls=[
            {"name": "notify", "args": {"channel": "email", "message": "a"}, "id": "call_a"},
            {"name": "notify", "args": {"channel": "sms", "message": "b"}, "id": "call_b"},
        ],
    )
    graph = build_agent(
        _settings(),
        checkpointer=InMemorySaver(),
        llm=make_scripted_model([two_calls, AIMessage(content="好的，已取消")]),
    )
    config = {"configurable": {"thread_id": "t-reject"}}
    await graph.ainvoke({"messages": [HumanMessage("两个都通知")]}, config)
    result = await graph.ainvoke(
        Command(resume={"approved": False, "comment": "先别发"}), config
    )

    executed = _tool_messages(result)
    assert {m.tool_call_id for m in executed} == {"call_a", "call_b"}
    assert all("先别发" in m.content for m in executed)
    assert all("已提交" not in m.content for m in executed)
    assert result["messages"][-1].content == "好的，已取消"


def test_chat_returns_pending_then_resume_completes(
    app_env, use_model, make_scripted_model, alice
) -> None:
    """HTTP 层：一次 /chat 拿到挂起，一次 /chat/resume 拿到最终答案。"""
    use_model(make_scripted_model([_notify_call(), AIMessage(content="已通知")]))
    app = create_app()

    with TestClient(app) as client:
        first = client.post("/chat", json={"message": "通知一下"}, headers=alice).json()
        assert first["status"] == "pending_approval"
        assert first["answer"] == ""
        assert first["pending"]["calls"][0]["name"] == "notify"
        assert first["pending"]["calls"][0]["args"]["channel"] == "email"

        resumed = client.post(
            "/chat/resume",
            json={"thread_id": first["thread_id"], "approved": True},
            headers=alice,
        ).json()
        assert resumed["status"] == "completed"
        assert resumed["answer"] == "已通知"
        assert resumed["pending"] is None


def test_resume_without_pending_returns_409(
    app_env, use_model, make_scripted_model, alice
) -> None:
    """没有挂起却调 resume 说明客户端状态错了，用 409 明确告知，而不是静默重跑一轮。"""
    use_model(make_scripted_model([AIMessage(content="普通回答")]))
    app = create_app()

    with TestClient(app) as client:
        created = client.post("/chat", json={"message": "你好"}, headers=alice).json()
        response = client.post(
            "/chat/resume",
            json={"thread_id": created["thread_id"], "approved": True},
            headers=alice,
        )
    assert response.status_code == 409


def test_cannot_resume_another_tenants_thread(
    app_env, use_model, make_scripted_model, alice, bob
) -> None:
    """审批入口同样受租户隔离约束：别人的内部键构造不出来，只能看到"没有待审批"。"""
    use_model(make_scripted_model([_notify_call(), AIMessage(content="已通知")]))
    app = create_app()

    with TestClient(app) as client:
        pending = client.post("/chat", json={"message": "通知一下"}, headers=alice).json()
        thread_id = pending["thread_id"]
        assert pending["status"] == "pending_approval"

        by_bob = client.post(
            "/chat/resume",
            json={"thread_id": thread_id, "approved": True},
            headers=bob,
        )
        assert by_bob.status_code == 409

        # 越权尝试不能影响原会话：Alice 依然可以正常完成审批
        by_alice = client.post(
            "/chat/resume",
            json={"thread_id": thread_id, "approved": True},
            headers=alice,
        ).json()
    assert by_alice["status"] == "completed"
    assert by_alice["answer"] == "已通知"


def test_stream_emits_interrupt_event(app_env, use_model, make_scripted_model, alice) -> None:
    """流式接口要把中断翻译成 interrupt 事件，前端据此弹出确认框。"""
    use_model(make_scripted_model([_notify_call(), AIMessage(content="已通知")]))
    app = create_app()

    with TestClient(app) as client:
        with client.stream(
            "POST", "/chat/stream", json={"message": "通知一下"}, headers=alice
        ) as response:
            body = "".join(response.iter_text())

    assert "event: interrupt" in body
    assert "tool_approval" in body
    assert '"status": "pending_approval"' in body
