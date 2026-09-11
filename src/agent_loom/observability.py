"""LangSmith 接入口。

职责只有两件事：
1. 把配置翻译成 SDK 认识的环境变量（tracer 在创建客户端时读取它们）；
2. 构造带元数据的运行配置，让 LangSmith 里的每条 trace 都能按会话/用户筛出来。
"""

from __future__ import annotations

import os
from typing import Any

from .settings import Settings


def setup_observability(settings: Settings) -> bool:
    """启用或显式关闭追踪，返回是否真正开启。

    必须在第一次模型调用之前执行：LangSmith 的 tracer 在构造客户端时读取环境变量，
    事后修改不生效。没有 API Key 时一律关闭，避免产生注定失败的上报请求。
    """
    if not (settings.langsmith_tracing and settings.langsmith_api_key):
        os.environ["LANGSMITH_TRACING"] = "false"
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    return True


def build_run_config(thread_id: str, user_id: str | None = None, **extra: Any) -> dict[str, Any]:
    """构造一次运行的 RunnableConfig。

    `configurable.thread_id` 是 LangGraph 的会话主键：检查点、状态、消息历史全挂在它下面。
    同一个 thread_id 就是同一段对话记忆，换一个就是全新会话。

    run_name / tags / metadata 只影响 LangSmith 的展示与检索，不参与图逻辑，
    所以可以放心塞业务字段（用户、渠道、实验分组），线上排查问题时非常好用。
    """
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": "agent-loom.chat",
        "tags": ["agent-loom", "single-agent"],
        "metadata": {"thread_id": thread_id, "user_id": user_id, **extra},
    }
