# 更新日志

遵循[语义化版本](https://semver.org/lang/zh-CN/)。

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
