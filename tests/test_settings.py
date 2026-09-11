"""配置层测试：环境变量到强类型对象的映射。"""

from agent_loom.settings import Settings


def test_defaults_are_sane() -> None:
    settings = Settings(_env_file=None)
    assert settings.redis_url.startswith("redis://")
    assert settings.langsmith_tracing is False


def test_env_overrides_are_applied(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("REDIS_CHECKPOINT_TTL_MINUTES", "60")
    settings = Settings(_env_file=None)
    assert settings.llm_model == "deepseek-chat"
    assert settings.redis_checkpoint_ttl_minutes == 60


def test_blank_base_url_becomes_none(monkeypatch) -> None:
    """留空必须变成 None；空字符串会被 SDK 当成一个真实的空地址。"""
    monkeypatch.setenv("LLM_BASE_URL", "")
    assert Settings(_env_file=None).llm_base_url is None


def test_openai_api_key_alias(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-alias")
    assert Settings(_env_file=None).llm_api_key == "sk-alias"


def test_cors_origins_are_split() -> None:
    settings = Settings(_env_file=None, cors_origins="https://a.com, https://b.com")
    assert settings.cors_origin_list == ["https://a.com", "https://b.com"]
