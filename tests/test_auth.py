"""鉴权与多租户隔离。

这是项目安全性的落点：身份只能来自凭据，租户之间靠"派生存储键"隔离。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agent_loom.main import create_app
from agent_loom.security import authenticate, resolve_thread
from agent_loom.settings import Settings

VALID_KEY = "a" * 16


def test_authenticate_accepts_valid_key_and_rejects_others() -> None:
    settings = Settings(_env_file=None, api_keys=f"{VALID_KEY}:alice")
    assert authenticate(settings, VALID_KEY) == "alice"
    assert authenticate(settings, "b" * 16) is None
    assert authenticate(settings, None) is None


def test_unconfigured_keys_reject_everything() -> None:
    """fail closed：没有配置就不是"放行"，而是全部拒绝。"""
    settings = Settings(_env_file=None, api_keys="")
    assert authenticate(settings, "anything") is None


def test_weak_or_malformed_api_keys_fail_at_startup() -> None:
    """配错在启动瞬间就炸，而不是等第一个请求进来。"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_keys="short-key:alice")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_keys=VALID_KEY)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_keys=f"{VALID_KEY}:a,{VALID_KEY}:b")


def test_internal_thread_key_is_namespaced_by_user() -> None:
    alice_thread = resolve_thread("alice", "shared")
    bob_thread = resolve_thread("bob", "shared")
    # 对外是同一个 id，落库的键不同 —— 隔离在结构上成立
    assert alice_thread.public == bob_thread.public == "shared"
    assert alice_thread.internal == "alice:shared"
    assert alice_thread.internal != bob_thread.internal


def test_missing_credentials_are_rejected(app_env) -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "hi"})
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"

        wrong = client.post(
            "/chat", json={"message": "hi"}, headers={"Authorization": "Bearer " + "x" * 16}
        )
        assert wrong.status_code == 401

        # 用错认证方案同样拒绝
        basic = client.post(
            "/chat", json={"message": "hi"}, headers={"Authorization": "Basic YWJj"}
        )
        assert basic.status_code == 401


def test_healthz_stays_public(app_env) -> None:
    """探活必须无凭据可访问，否则负载均衡与容器编排没法用。"""
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


def test_same_thread_id_is_isolated_between_users(
    app_env, use_model, make_scripted_model, alice, bob
) -> None:
    """两个用户用同一个 thread_id，也互相看不到对方的消息，删不掉对方的数据。"""
    use_model(
        make_scripted_model([AIMessage(content="alice 的回答"), AIMessage(content="bob 的回答")])
    )
    app = create_app()

    with TestClient(app) as client:
        first = client.post(
            "/chat", json={"message": "我是 alice", "thread_id": "shared"}, headers=alice
        ).json()
        second = client.post(
            "/chat", json={"message": "我是 bob", "thread_id": "shared"}, headers=bob
        ).json()
        assert first["thread_id"] == second["thread_id"] == "shared"

        alice_state = client.get("/threads/shared", headers=alice).json()
        bob_state = client.get("/threads/shared", headers=bob).json()

        # 越权删除同样无效：alice 删掉自己的，bob 的必须还在
        client.delete("/threads/shared", headers=alice)
        bob_after_delete = client.get("/threads/shared", headers=bob).json()
        alice_after_delete = client.get("/threads/shared", headers=alice).json()

    assert [m["content"] for m in alice_state["messages"]] == ["我是 alice", "alice 的回答"]
    assert [m["content"] for m in bob_state["messages"]] == ["我是 bob", "bob 的回答"]
    assert [m["content"] for m in bob_after_delete["messages"]] == ["我是 bob", "bob 的回答"]
    assert alice_after_delete["messages"] == []


def test_invalid_thread_id_is_rejected(app_env, alice) -> None:
    app = create_app()
    with TestClient(app) as client:
        # 请求体由 Pydantic 模式校验拦下
        assert (
            client.post(
                "/chat", json={"message": "hi", "thread_id": "bad id!!"}, headers=alice
            ).status_code
            == 422
        )
        # 路径参数由 resolve_thread 拦下
        assert client.get("/threads/bad%20id", headers=alice).status_code == 400
