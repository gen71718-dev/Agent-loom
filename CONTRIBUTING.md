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

## 安全相关改动

- 身份字段**永远不能**出现在请求体或查询参数里，只能来自 `Authorization` 头。
- 任何按 `thread_id` 操作的新接口都必须走 `security.resolve_thread()` 派生内部键，
  不允许自己拼键，更不允许直接把客户端传入的 id 当存储键用。
- 涉及鉴权或租户隔离的改动，必须同时补"跨租户越权"的反向测试，
  参考 `tests/test_auth.py::test_same_thread_id_is_isolated_between_users`。

## 工具与人工审批

- 有副作用的工具（写外部系统、发消息、动数据）必须登记进 `APPROVAL_REQUIRED_TOOLS`，
  并补一条"挂起期间不执行、批准后才执行"的测试，参考 `tests/test_approval.py`。
- `interrupt()` 之前不能有任何副作用（写库、发请求、打点）：节点恢复时会从函数第一行重放，
  这段代码会被执行两次。
- 拒绝一批调用时，必须给本批**全部** `tool_call` 补上 `ToolMessage`——OpenAI 兼容协议要求
  二者严格一一对应，少一条下一轮模型调用就会报错。

## 提交与 PR

- 提交信息用祈使句，说明"做了什么"，例如 `新增 supervisor + worker 子图`。
- PR 描述里写清：动机、行为变化、如何验证。
- 涉及行为变更的，同步更新 `README.md` 或 `docs/architecture.md`。
