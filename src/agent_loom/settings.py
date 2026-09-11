"""集中式配置。

所有可变参数都来自环境变量：代码可以开源，密钥和连接串永远不进仓库。
pydantic-settings 负责把字符串转成正确的类型，配错在启动瞬间就报错，而不是请求进来才炸。
"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。每个字段都能被同名的大写环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "AgentLoom"
    app_env: str = "dev"
    log_level: str = "INFO"

    llm_model: str = "gpt-4o-mini"
    # 兼容两种写法：本项目自己的 LLM_API_KEY，或 SDK 习惯的 OPENAI_API_KEY
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"),
    )
    llm_base_url: str | None = None
    llm_temperature: float = 0.0

    redis_url: str = "redis://localhost:6379/0"
    redis_checkpoint_ttl_minutes: int = 1440
    checkpointer: Literal["redis", "memory"] = "redis"

    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "agent-loom"

    cors_origins: str = "*"

    # 鉴权：格式 `key:user_id,key:user_id`。留空则拒绝所有请求（fail closed）
    api_keys: str = ""

    @field_validator("llm_base_url", mode="after")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """把 .env 里留空的 `LLM_BASE_URL=` 归一成 None。

        空字符串会被 SDK 当成"一个真实的空地址"而报错，null 才是"用默认地址"。
        """
        return value or None

    @field_validator("api_keys", mode="after")
    @classmethod
    def _validate_api_keys(cls, value: str) -> str:
        """启动时就校验格式，别等第一个请求进来才发现配错。

        过短的 Key 是最常见的凭证事故来源，这里直接拒绝。
        """
        seen: set[str] = set()
        for item in _iter_api_key_entries(value):
            key, _, user_id = item.partition(":")
            if not user_id:
                raise ValueError(f"API_KEYS 条目缺少 user_id，应为 `key:user_id`：{item!r}")
            if len(key) < 16:
                raise ValueError(f"API_KEYS 里的 Key 太短（至少 16 位）：{key[:4]}***")
            if key in seen:
                raise ValueError("API_KEYS 里存在重复的 Key")
            seen.add(key)
        return value

    @property
    def api_key_map(self) -> dict[str, str]:
        """API Key → 用户标识。"""
        result: dict[str, str] = {}
        for item in _iter_api_key_entries(self.api_keys):
            key, _, user_id = item.partition(":")
            result[key] = user_id
        return result

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


def _iter_api_key_entries(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """进程内单例：环境变量只解析一次，避免每个请求重复读盘。"""
    return Settings()
