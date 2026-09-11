"""测试公共设施：一个按脚本回答的假模型。

为什么要假模型：单测不该依赖外部 API（慢、要钱、结果不稳定），
但图的路由、状态合并、记忆持久化这些逻辑依然值得被真实验证。
"""

from __future__ import annotations

import os
import uuid

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


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
        # 图开启 messages 流式时会走这里：把整条脚本消息作为单个分片吐出
        message = self._next()
        tool_calls = [
            {**call, "index": index} for index, call in enumerate(message.tool_calls or [])
        ]
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=message.content, tool_calls=tool_calls, id=message.id)
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
