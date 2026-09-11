"""请求/响应的数据契约。

显式定义 schema 的价值：OpenAPI 文档自动生成、非法入参在进门就被拦下、响应字段有类型保证。
"""

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="用户这一轮说的话")
    thread_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description=(
            "会话 ID。不传则服务端新建一个并在响应里返回，客户端应保存下来继续使用。"
            "只允许字母、数字、下划线与短横线。"
        ),
    )


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    tool_calls: list[str] = Field(default_factory=list, description="本轮实际调用的工具名")


class ThreadStateResponse(BaseModel):
    thread_id: str
    messages: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    redis: bool
    tracing: bool
    model: str
