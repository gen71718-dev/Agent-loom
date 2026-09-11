# 贡献指南

## 开发环境

```bash
uv sync                      # 按 uv.lock 安装，含开发依赖
uv run pytest -q             # 单元测试：不需要 Redis / API Key
uv run ruff check .          # 静态检查
```

需要跑集成测试时先起 Redis：

```bash
docker compose up -d redis
uv run pytest -q             # 集成测试会自动检测 Redis，不可用则跳过
```

## 代码约定

- 新增功能必须带测试。纯逻辑用单测；涉及 Redis 的用 `tests/test_redis_integration.py` 里的跳过式写法。
- 不引入 `eval()` / `exec()`：工具层的入参来自模型，等同不可信输入。
- 配置只走 `settings.py`，不要在业务代码里直接读 `os.environ`。
- 提交前跑 `ruff check .` 与 `pytest`。

## 提交与 PR

- 提交信息用祈使句，说明"做了什么"，例如 `新增 human-in-the-loop 中断恢复`。
- PR 描述里写清：动机、行为变化、如何验证。
- 涉及行为变更的，同步更新 `README.md` 或 `docs/architecture.md`。
