"""测试公共设施：假模型、环境隔离与应用夹具。

为什么要假模型：单测不该依赖外部 API（慢、要钱、结果不稳定），
但图的路由、状态合并、记忆持久化这些逻辑依然值得被真实验证。
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from agent_loom.graph import build_agent as real_build_agent
from agent_loom.settings import Settings

ALICE_KEY = "test-key-alice-00000001"
BOB_KEY = "test-key-bob-0000000002"
API_KEYS = f"{ALICE_KEY}:alice,{BOB_KEY}:bob"


class ScriptedChatModel(BaseChatModel):
    """按预设脚本逐条返回消息；脚本用完后重复最后一条。"""

    responses: list[AIMessage]
    cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: object, **kwargs: object) -> ScriptedChatModel:
        # 脚本本身就规定了哪一步该调工具，这里直接忽略绑定
        return self

    def _next(self) -> AIMessage:
        template = self.responses[min(self.cursor, len(self.responses) - 1)]
        self.cursor += 1
        # 必须换一个新 id：add_messages 按消息 id 去重，重复 id 会被"替换"而不是"追加"
        return AIMessage(
            content=template.content,
            tool_calls=template.tool_calls,
            id=str(uuid.uuid4()),
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        # 图开启 messages 流式时会走这里：把整条脚本消息作为单个分片吐出。
        #
        # 分片必须用 tool_call_chunks（带 index 的"增量协议"）而不是 tool_calls：
        # tool_calls 是"已解析完成"的形态，塞进分片会因缺少 index 字段校验失败，
        # 表现为流式接口里冒出一条 `event: error`。args 在增量协议里是 JSON 字符串。
        message = self._next()
        tool_call_chunks = [
            {
                "name": call["name"],
                "args": json.dumps(call["args"], ensure_ascii=False),
                "id": call["id"],
                "index": index,
                "type": "tool_call_chunk",
            }
            for index, call in enumerate(message.tool_calls or [])
        ]
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content, tool_call_chunks=tool_call_chunks, id=message.id
            )
        )


@pytest.fixture(autouse=True)
def restore_environ():
    """还原进程环境变量，避免测试之间互相污染。

    setup_observability() 会直接给 os.environ 赋值（LangSmith 就是靠环境变量生效的），
    这种直接赋值 monkeypatch 追踪不到，会泄漏给同一进程里后续的所有测试。
    """
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


@pytest.fixture
def make_scripted_model():
    def _factory(responses: list[AIMessage]) -> ScriptedChatModel:
        return ScriptedChatModel(responses=responses)

    return _factory


@pytest.fixture
def app_env(monkeypatch) -> Settings:
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
        api_keys=API_KEYS,
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


@pytest.fixture
def alice() -> dict[str, str]:
    return {"Authorization": f"Bearer {ALICE_KEY}"}


@pytest.fixture
def bob() -> dict[str, str]:
    return {"Authorization": f"Bearer {BOB_KEY}"}
