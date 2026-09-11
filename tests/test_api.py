"""端到端测试：把图挂到 FastAPI 上，验证 HTTP 层的真实行为。

用 CHECKPOINTER=memory 绕过 Redis，用假模型绕过外部 API，
所以整条链路（路由 -> 图 -> 状态 -> 序列化）都被真实执行，只是没有外部依赖。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent_loom.graph import build_agent as real_build_agent
from agent_loom.main import create_app
from agent_loom.settings import Settings


@pytest.fixture
def app_env(monkeypatch):
    """给应用注入一份完全隔离的测试配置。

    关键点：不能让应用去读开发机的 .env。否则测试结果依赖本机环境，
    而且真的会按 .env 里的 LangSmith Key 往云端上传 trace。
    """
    isolated = Settings(
        _env_file=None,
        checkpointer="memory",
        llm_api_key="sk-test",
        langsmith_tracing=False,
        langsmith_api_key="",
    )
    monkeypatch.setattr("agent_loom.main.get_settings", lambda: isolated)
    return isolated


@pytest.fixture
def use_model(monkeypatch):
    """替换 main.build_agent，让 lifespan 建图时用上假模型。"""

    def _apply(model) -> None:
        def build(settings, checkpointer=None):
            return real_build_agent(settings, checkpointer=checkpointer, llm=model)

        monkeypatch.setattr("agent_loom.main.build_agent", build)

    return _apply


def test_healthz_reflects_redis_availability(app_env) -> None:
    """探活要如实反映依赖状态：Redis 可达就 ok，不可达必须 degraded，绝不假装健康。"""
    app = create_app()
    with TestClient(app) as client:
        body = client.get("/healthz").json()
    assert body["status"] == ("ok" if body["redis"] else "degraded")


def test_chat_roundtrip_and_memory(app_env, use_model, make_scripted_model) -> None:
    use_model(
        make_scripted_model([AIMessage(content="第一次回答"), AIMessage(content="第二次回答")])
    )
    app = create_app()

    with TestClient(app) as client:
        first = client.post("/chat", json={"message": "你好"}).json()
        assert first["answer"] == "第一次回答"
        thread_id = first["thread_id"]

        second = client.post("/chat", json={"message": "继续", "thread_id": thread_id}).json()
        assert second["thread_id"] == thread_id
        assert second["answer"] == "第二次回答"

        state = client.get(f"/threads/{thread_id}").json()
        assert [m["content"] for m in state["messages"]] == [
            "你好",
            "第一次回答",
            "继续",
            "第二次回答",
        ]


def test_chat_validates_empty_message(app_env) -> None:
    app = create_app()
    with TestClient(app) as client:
        assert client.post("/chat", json={"message": ""}).status_code == 422


def test_stream_emits_sse_events(app_env, use_model, make_scripted_model) -> None:
    use_model(make_scripted_model([AIMessage(content="流式回答")]))
    app = create_app()

    with TestClient(app) as client:
        with client.stream("POST", "/chat/stream", json={"message": "你好"}) as response:
            body = "".join(response.iter_text())

    assert "event: start" in body
    assert "event: done" in body
    assert "流式回答" in body
