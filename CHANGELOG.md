# 更新日志

遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-09-12

### 新增

- **人工审批（human-in-the-loop）**：图里新增 `review` 节点，命中 `APPROVAL_REQUIRED_TOOLS`
  的工具调用会 `interrupt()` 挂起整条流程，等人工裁决后再继续。
  - `POST /chat` 命中时返回 `status=pending_approval` 与 `pending` 明细（工具名 + 参数）。
  - `POST /chat/resume` 用 `Command(resume=...)` 送回裁决：批准则执行；拒绝则不执行，
    并把拒绝原因作为工具结果回给模型，让模型重新决策。
  - SSE 新增 `interrupt` 事件，前端据此渲染确认框。
  - 无待审批时 `/chat/resume` 返回 `409`（客户端状态不同步），不是静默重跑。
- 新增 `notify` 工具（向 email / sms / webhook 渠道发通知），作为"有副作用、需审批"的示例。
- 新增 `APPROVAL_REQUIRED_TOOLS` 配置（逗号分隔，默认 `notify`，留空则全部自动执行）。
- 审批测试 9 个：挂起期间不执行、批准后执行、拒绝后补齐全部 `ToolMessage`、
  无挂起返回 409、跨租户不能审批他人会话、SSE `interrupt` 事件。测试总数 29 → 38。

### 修复

- **流式 + 工具调用导致 `/chat/stream` 报错**：测试假模型在 `_astream` 里把已解析完成的
  `tool_calls` 塞进 `AIMessageChunk` 并手动加 `index`，触发
  `unexpected keyword argument 'index'`，被 SSE 包成一条 `error` 事件。
  改用增量协议 `tool_call_chunks`（`args` 为 JSON 字符串、`index` 是其正式字段）。
  该缺陷只在"流式 + 工具调用"同时出现时暴露，同步路径完全正常。

## [0.2.0] - 2026-09-11

### 新增

- **接口鉴权**：`Authorization: Bearer <API Key>`，配置在 `.env` 的 `API_KEYS`（`key:user_id` 形式）。
  除 `/healthz` 外所有接口都要求凭据；未配置凭据时 fail closed（全部拒绝，而不是默认放行）。
- **多租户隔离**：内部 `thread_id` 由 `{user_id}:{客户端 id}` 派生。两个用户即便使用同一个
  `thread_id`，也互不可见、互不可删。
- 启动时校验 `API_KEYS`：Key 少于 16 位、缺少 `user_id`、Key 重复，都会让服务直接拒绝启动。
- 鉴权与隔离测试 8 个用例，含"同 `thread_id` 跨租户越权"的反向验证。
- `docs/architecture.md` 新增「多租户与安全模型」章节。

### 破坏性变更

- `POST /chat` 请求体不再接受 `user_id` 字段——身份只能来自凭据。
- 存储键已加租户前缀，因此**升级后旧会话读不到**（键形如 `checkpoint:{thread_id}` 的历史数据
  不再被索引）。开发环境直接 `docker compose down -v` 重建即可；生产环境需要迁移或接受丢失。

### 安全

- 修复 v0.1 的越权缺陷：任何人伪造 `thread_id` 即可读取他人的完整对话历史。

## [0.1.0] - 2026-09-11

### 新增

- 基于 LangGraph `StateGraph` 的 ReAct 图（`agent` ⇄ `tools` 循环，条件边由 `tools_condition` 驱动）。
- 检查点抽象：`redis`（生产形态）与 `memory`（本地开发）两种实现，Redis 侧支持 TTL。
- LangSmith 追踪接入，运行元数据包含 `thread_id` 与 `user_id`，可按会话/用户筛选。
- FastAPI 接口：`/chat`、`/chat/stream`（SSE）、`/threads/{thread_id}`、`/healthz`。
- 工具：`current_time`（IANA 时区）、`calculator`（AST 白名单求值，不使用 `eval`）。
- 21 个测试、ruff、GitHub Actions CI、Dockerfile、docker-compose、架构文档。
