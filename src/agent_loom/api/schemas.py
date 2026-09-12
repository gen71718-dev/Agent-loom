"""请求/响应的数据契约。

显式定义 schema 的价值：OpenAPI 文档自动生成、非法入参在进门就被拦下、响应字段有类型保证。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..security import THREAD_ID_PATTERN


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="用户这一轮说的话")
    thread_id: str | None = Field(
        default=None,
        pattern=THREAD_ID_PATTERN,
        description=(
            "会话 ID。不传则服务端新建一个并在响应里返回，客户端应保存下来继续使用。"
            "只允许字母、数字、下划线与短横线。"
        ),
    )


class ApprovalCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]


class PendingApproval(BaseModel):
    """挂起等待人工确认的内容。"""

    type: str = "tool_approval"
    prompt: str
    calls: list[ApprovalCall]


class ChatResponse(BaseModel):
    thread_id: str
    status: Literal["completed", "pending_approval"] = "completed"
    answer: str = ""
    tool_calls: list[str] = Field(default_factory=list, description="本轮涉及的工具名")
    pending: PendingApproval | None = Field(
        default=None, description="status 为 pending_approval 时，给出待审批的调用明细"
    )


class ResumeRequest(BaseModel):
    thread_id: str = Field(pattern=THREAD_ID_PATTERN, description="挂起时返回的会话 ID")
    approved: bool = Field(description="是否批准执行这批工具调用")
    comment: str | None = Field(default=None, description="拒绝原因，会作为工具结果回给模型")


class ThreadStateResponse(BaseModel):
    thread_id: str
    messages: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    redis: bool
    tracing: bool
    model: str
