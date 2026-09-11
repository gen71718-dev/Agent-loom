"""鉴权与租户隔离。

两条原则，都是"从结构上杜绝"而不是"靠记得写检查"：

1. **身份只能来自凭据**。请求体里的任何"我是谁"字段都不可信——
   客户端说自己是谁就信谁，等于没有鉴权。本项目上一版就是这么写的（ChatRequest.user_id），
   这一版把它彻底删掉了。

2. **隔离靠派生存储键，而不是先查后判**。内部 thread_id 由 `{user_id}:{客户端 id}` 拼成，
   别人的键你根本构造不出来，也就不存在"某个分支忘了校验"的可能。
   对比一下有风险的做法：写一个 owner 注册表，每次请求先查归属再放行——
   查询有竞态、记录有 TTL 不一致、新增接口容易漏掉检查。
"""

from __future__ import annotations

import re
import secrets
import uuid
from typing import NamedTuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .settings import Settings

# 客户端传入的 thread_id 只允许安全字符：既是防御性校验，也避免奇怪的字符混进 Redis 键名
THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_bearer = HTTPBearer(
    auto_error=False,
    description="Bearer API Key，例如 `Bearer local-dev-key-0001`",
)


class ThreadRef(NamedTuple):
    """对外 id 与内部存储键的配对。"""

    public: str
    internal: str


def authenticate(settings: Settings, presented: str | None) -> str | None:
    """校验 API Key，返回对应用户标识；失败返回 None。

    用 secrets.compare_digest 做常数时间比较：普通的 == 会在第一个不同的字节处就返回，
    攻击者能通过响应时间逐字节把 Key 试出来。
    """
    if not presented:
        return None
    for key, user_id in settings.api_key_map.items():
        if secrets.compare_digest(key, presented):
            return user_id
    return None


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """FastAPI 依赖：从 Authorization 头解析出当前用户。

    配置缺失时一律拒绝（fail closed）——默认放行的"开发模式"是最经典的安全事故来源。
    """
    settings: Settings = request.app.state.settings
    user_id = authenticate(settings, credentials.credentials if credentials else None)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或无效的 API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


def resolve_thread(user_id: str, client_thread_id: str | None) -> ThreadRef:
    """把客户端给的会话 id 映射成租户隔离的内部键。

    内部键是确定性的函数映射，不需要任何额外的存储或查询，
    所以也不存在"注册表被写坏"或"两个租户撞键"的可能。
    """
    if client_thread_id is None:
        public = uuid.uuid4().hex
    else:
        if not THREAD_ID_PATTERN.match(client_thread_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="thread_id 只允许字母、数字、下划线与短横线，长度 1-64",
            )
        public = client_thread_id
    return ThreadRef(public=public, internal=f"{user_id}:{public}")
