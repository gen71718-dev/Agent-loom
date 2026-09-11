# AgentLoom · 织机

> 基于 **LangGraph + Redis + LangSmith + FastAPI** 的单智能体服务。
> 一张手工搭建的 ReAct 图、一份存在 Redis 里的真实记忆、一条可回放的执行链路。

![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![tests](https://img.shields.io/badge/tests-21%20passed-brightgreen)

单 agent 是底座，不是终点：把 `tools` 换成子图、把 `agent` 拆成 supervisor + worker，
这张图就长成了多 agent 团队，而记忆层、可观测层、服务层一行都不用改。

**想搞懂每个组件为什么这样配合**，看 [docs/architecture.md](docs/architecture.md)（含时序图、耦合方式、踩坑记录）。
**只想跑起来**，往下看。

## 能做什么

**已实现**

- **多轮对话与工具调用**：一张 ReAct 图，模型自主决定何时调用工具。内置 `current_time`（任意 IANA 时区）与 `calculator`（四则运算）。
- **会话记忆**：按 `thread_id` 隔离，状态落在 Redis。服务重启、多副本部署都不丢；可配 TTL 自动过期。
- **历史回读与删除**：`GET /threads/{id}` 读回完整会话（可用来验证记忆确实落库），`DELETE /threads/{id}` 满足"用户行使删除权"。
- **流式输出**：SSE 逐 token 下发，另带节点级事件，前端可以提示"正在调工具"。
- **全链路追踪**：每次运行的每一步自动上报 LangSmith，可按 `thread_id` / `user_id` 筛选。
- **探活与降级可见**：`/healthz` 真实探测 Redis，连不上如实返回 `degraded`，不假装健康。
- **零依赖测试**：21 个用例；单测不需要 Redis、网络、API Key，Redis 集成测试在无 Redis 时自动跳过。

**有意不做（留给下一步）**

- 人工审批中断恢复（`interrupt`）、多 agent 协作（supervisor + worker 子图）
- 跨会话长期记忆（`AsyncRedisStore`：记住"用户是谁"，而不只是"这次聊到哪"）
- 鉴权与多租户：当前 `thread_id` 由客户端传入，**生产环境必须由服务端签发或严格校验**
- 评测集与回归（LangSmith dataset + evaluator）

内置工具只有两个是刻意的，为了让示例保持可读。加一个工具只需三步：
在 `src/agent_loom/tools.py` 写一个带 docstring 的 `@tool` 函数（docstring 就是模型看到的工具说明），
加进 `TOOLS` 列表，完事——图的代码一行都不用动。

## 架构

```
                   ┌──────────────────────── FastAPI ─────────────────────────┐
   浏览器 / 客户端  │  routes.py   会话编排、参数校验、SSE 序列化                 │
        │          │  main.py     lifespan：按顺序装配并持有下游依赖             │
        │  HTTP    └───────┬────────────────────────────┬─────────────────────┘
        └──────────────────┤                            │
                           │ 编译一次，进程内复用          │ 读配置
                           ▼                            ▼
                   ┌───────────────┐            ┌───────────────┐
                   │   LangGraph   │            │  settings.py  │◀── .env
                   │  StateGraph   │            └───────────────┘
                   │  agent ⇄ tools│───────────▶ LangChain：ChatOpenAI + @tool
                   └───┬───────┬───┘
            状态快照    │       │  每一步执行
                       ▼       ▼
              ┌────────────┐  ┌──────────────┐
              │   Redis    │  │  LangSmith   │
              │ checkpointer│ │  trace / eval│
              └────────────┘  └──────────────┘
```

## 快速开始

```bash
# 1. 起 Redis（必须是 Redis Stack：检查点依赖 RediSearch 建索引）
docker compose up -d redis

# 2. 装依赖（uv 会自动准备 Python 3.12）
uv sync

# 没装 Docker？把 .env 里的 CHECKPOINTER 改成 memory，就能用进程内存跑通全流程

# 3. 配置（Windows 上用 copy .env.example .env）
cp .env.example .env      # 填入 LLM_API_KEY；要开追踪再填 LANGSMITH_API_KEY

# 4. 启动
uv run uvicorn agent_loom.main:app --reload --port 8000
```

看到这行日志才算真的起来了：

```
INFO:agent_loom:AgentLoom ready | model=deepseek-chat | redis=redis://localhost:6379/0 | tracing=True
```

打开 http://127.0.0.1:8000/docs 可以直接在浏览器里试接口。

### 用第三方模型

任何 OpenAI 兼容端点都能直接用，改 `.env` 两行即可：

```ini
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
```

## 试一试

```bash
# 第一轮
curl -s -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我算一下 (128+72)*3/4"}'
# {"thread_id":"7f3c...","answer":"(128+72)*3/4 = 150","tool_calls":["calculator"]}

# 第二轮：带上同一个 thread_id，模型记得上一轮说过什么
curl -s -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"把刚才那个结果再乘以 2","thread_id":"7f3c..."}'
# {"thread_id":"7f3c...","answer":"150 × 2 = 300","tool_calls":["calculator"]}

# 流式：逐字返回
curl -N -X POST http://127.0.0.1:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"现在几点了？","thread_id":"7f3c..."}'

# 验证记忆真的落库了（不是存在进程内存里）
curl -s http://127.0.0.1:8000/threads/7f3c...

# 重启服务，再问一次"我叫什么" —— 上下文依然在，这就是 checkpointer 的价值
```

浏览器端消费 SSE 的样子（前端直接照抄）：

```javascript
const res = await fetch("http://127.0.0.1:8000/chat/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "现在几点了？", thread_id: threadId }),
});
const reader = res.body.getReader();
const decoder = new TextDecoder();
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  process.stdout.write(decoder.decode(value)); // 解析 "event:" / "data:" 两行即可
}
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/healthz` | 探活，会真的 ping 一次 Redis，连不上返回 `degraded` |
| `POST` | `/chat` | 一次性问答，返回最终答案与本轮调用的工具名 |
| `POST` | `/chat/stream` | SSE 流式，事件：`start` / `token` / `node` / `done` / `error` |
| `GET` | `/threads/{thread_id}` | 读回会话的完整状态（可用来验证记忆确实落库） |
| `DELETE` | `/threads/{thread_id}` | 删除会话，隐私合规的"用户行使删除权"落点 |

`thread_id` 是会话主键：不传则服务端新建并在响应里返回，客户端要保存下来继续用。
**生产环境务必由服务端生成或严格校验**，否则用户传别人的 ID 就能读到别人的对话。

## Docker

```bash
docker compose up -d redis             # 只起 Redis（本地开发推荐）
docker compose --profile full up -d    # 连应用一起容器化
```

## 目录结构

```
src/agent_loom/
├── settings.py        # 配置：环境变量 → 强类型对象，启动即校验
├── observability.py   # LangSmith：环境变量注入 + 运行配置（元数据/标签）
├── memory.py          # 检查点：Redis / 内存两种实现，按配置切换
├── tools.py           # 工具：agent 的手脚（含 AST 白名单求值）
├── prompts.py         # 系统提示词
├── graph.py           # 核心：ReAct 图
├── api/
│   ├── schemas.py     # 请求/响应契约，自动生成 OpenAPI
│   └── routes.py      # HTTP 边界层（保持薄）
└── main.py            # 入口：显式装配 + lifespan 生命周期
```

## 每一层在解决什么问题

| 层 | 组件 | 解决的问题 |
| --- | --- | --- |
| 编排 | LangGraph `StateGraph` | agent 不该是"一次模型调用"：多轮工具循环、条件跳转、可中断恢复都需要显式控制流 |
| 状态 | `AgentState` + `add_messages` reducer | 共享状态如何合并。用 reducer 追加消息，而不是每轮覆盖 |
| 记忆 | Redis checkpointer | 进程重启/多副本/用户回头继续聊，上下文都不能丢 |
| 工具 | `@tool` + AST 白名单 | 让模型能作用于真实世界，同时**绝不能**把 `eval()` 交给模型 |
| 观测 | LangSmith | 线上出问题时要看到"第 3 步模型为什么调了这个工具"，而不是靠猜 |
| 服务 | FastAPI + SSE | 把图变成可被前端消费的产品能力，同步与流式两种形态 |

## 关键原理速查

- **为什么系统提示词每轮现拼、不写进状态**：它是常量。写进状态会被 checkpointer 反复持久化，
  Redis 里堆满重复内容，还会污染 LangSmith 里的消息列表。
- **为什么图只编译一次**：编译产物内部持有连接池。每请求新建会迅速耗尽 Redis 连接。
- **为什么 `thread_id` 是整个系统的会话主键**：检查点、状态、历史全挂在它下面。换 ID 就是新会话。
- **为什么用 `bind_tools` 而不是自己拼工具描述**：模型厂商的原生 function calling 格式由
  `bind_tools` 统一适配，手拼容易漏掉各家的格式差异。
- **为什么 `stream_mode` 传列表**：`messages` 给前端打字机效果，`updates` 用来提示"正在调工具"，
  两种事件天然不同频率，需要分开处理。
- **`CHECKPOINTER=memory` 和 `redis` 的区别**：checkpointer 是接口，两种实现可互换。
  `memory` 免 Docker、但进程一停就没了，且无法在多副本间共享，只适合本地开发。

## 常见故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 浏览器"积极拒绝"，但进程列表里有 uvicorn | 配置校验失败导致子进程崩溃，`--reload` 父进程还活着 | 看终端最上面的报错；用 `Get-NetTCPConnection -LocalPort 8000 -State Listen` 确认端口没人监听 |
| 启动报 `Input should be a valid boolean` | `.env` 里布尔值拼写错误（如 `ture`） | 改成 `true` / `false` |
| 改了 `.env` 但配置没生效 | `uvicorn --reload` 只监听 `.py` 文件 | Ctrl+C 重启 |
| 启动报 RediSearch / `FT.CREATE` 相关错误 | Redis 不是 Stack 版本，缺 RediSearch 模块 | 换 `redis/redis-stack-server` 镜像 |
| 工具返回"未知时区" | Windows 上没有系统时区库 | 已通过 `tzdata` 依赖解决，勿删该依赖 |
| `/healthz` 返回 `degraded` | Redis 连不上 | 检查 `REDIS_URL` 与容器状态 |
| 模型报 400 / model not found | `LLM_MODEL` 与 `LLM_BASE_URL` 不匹配 | 第三方端点要用它自己的模型名，如 `deepseek-chat` |

## 测试

```bash
uv run pytest -q          # 单元测试：不需要 Redis、网络、API Key
uv run ruff check .
```

本地有 Redis 时，集成测试会自动启用（验证检查点真的落库、记忆能跨图实例读回）；
没有 Redis 则自动跳过，所以 CI 上无需额外服务。

## 下一步

1. 加 `interrupt`：让高风险工具调用挂起，等人工审批后再 `Command(resume=...)` 续跑。
2. 把 `agent` 换成子图：一个 supervisor 节点负责分发，多个 worker 子图并行干活。
3. 加长期记忆：用 `AsyncRedisStore` 存跨会话的用户偏好，与按会话隔离的 checkpointer 分工。
4. 加评测：在 LangSmith 里建数据集 + evaluator，每次改提示词都跑一遍回归。

详见 [docs/architecture.md](docs/architecture.md#八演进到多-agent)。

## License

[MIT](LICENSE)
