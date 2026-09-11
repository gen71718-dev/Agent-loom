"""HTTP 边界层。

这一层刻意保持"薄"：只做参数校验、会话 ID 编排、事件序列化。
业务逻辑（模型怎么想、工具怎么调）全在 graph 里，这样换传输协议（gRPC、CLI、消息队列）
都不用重写 agent。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from ..observability import build_run_config
from .schemas import ChatRequest, ChatResponse, HealthResponse, ThreadStateResponse

router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # 关掉 Nginx 等反向代理的缓冲，否则流式会变成"攒够一批才吐"
    "X-Accel-Buffering": "no",
}


def _text(content: Any) -> str:
    """把消息内容拍平成字符串。

    新模型的 content 可能是富内容块（list），不处理会在序列化时直接炸。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return "" if content is None else str(content)


def _sse(event: str, data: dict[str, Any]) -> str:
    """SSE 帧格式：event 行 + data 行 + 空行结束。ensure_ascii=False 保证中文可读。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/healthz", response_model=HealthResponse)
async def healthz(request: Request) -> HealthResponse:
    """探活接口：同时验证 Redis 真的连得上，而不是只证明进程还活着。"""
    settings = request.app.state.settings
    try:
        await request.app.state.redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return HealthResponse(
        status="ok" if redis_ok else "degraded",
        redis=redis_ok,
        tracing=request.app.state.tracing,
        model=settings.llm_model,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """一次性问答：等图跑完再返回。

    注意 ainvoke 内部可能已经循环了多轮"模型 -> 工具 -> 模型"，
    端到端只暴露一次请求，这对调用方是最省事的形式。
    """
    graph = request.app.state.graph
    thread_id = payload.thread_id or str(uuid.uuid4())
    config = build_run_config(thread_id, payload.user_id)
    result = await graph.ainvoke({"messages": [HumanMessage(payload.message)]}, config)
    messages = result["messages"]

    answer = ""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and _text(message.content):
            answer = _text(message.content)
            break

    tool_calls = [
        call["name"]
        for message in messages
        if isinstance(message, AIMessage)
        for call in (message.tool_calls or [])
    ]
    return ChatResponse(thread_id=thread_id, answer=answer, tool_calls=tool_calls)


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """流式问答：以 SSE 逐字下发，客户端能做打字机效果。

    stream_mode 传列表可以同时拿到多种事件：
    - "messages"：模型吐出的每一个 token（含元数据，可判断来自哪个节点）
    - "updates"：每个节点执行完的状态增量，用来告诉前端"正在调工具"
    """
    graph = request.app.state.graph
    thread_id = payload.thread_id or str(uuid.uuid4())
    config = build_run_config(thread_id, payload.user_id)

    async def event_stream() -> AsyncIterator[str]:
        yield _sse("start", {"thread_id": thread_id})
        try:
            async for mode, chunk in graph.astream(
                {"messages": [HumanMessage(payload.message)]},
                config,
                stream_mode=["messages", "updates"],
            ):
                if mode == "messages":
                    message_chunk, metadata = chunk
                    # 只转发模型说的话；工具返回值不直接给用户，由模型消化后再表述
                    if metadata.get("langgraph_node") != "agent":
                        continue
                    text = _text(message_chunk.content)
                    if text:
                        yield _sse("token", {"text": text})
                else:
                    for node in chunk:
                        yield _sse("node", {"node": node})
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
        yield _sse("done", {"thread_id": thread_id})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/threads/{thread_id}", response_model=ThreadStateResponse)
async def get_thread(thread_id: str, request: Request) -> ThreadStateResponse:
    """读回某个会话的完整状态，验证"记忆真的存在 Redis 里"。"""
    graph = request.app.state.graph
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = [
        {"role": message.type, "content": _text(message.content)}
        for message in snapshot.values.get("messages", [])
    ]
    return ThreadStateResponse(thread_id=thread_id, messages=messages)


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str, request: Request) -> dict[str, Any]:
    """删除会话：既清聊天记录，也清检查点。隐私合规里"用户行使删除权"的落点。"""
    checkpointer = request.app.state.checkpointer
    delete = getattr(checkpointer, "adelete_thread", None)
    if delete is None:
        raise HTTPException(status_code=501, detail="当前 checkpointer 版本不支持删除会话")
    await delete(thread_id)
    return {"deleted": thread_id}
