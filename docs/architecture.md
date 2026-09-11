# 架构与原理

这份文档回答三个问题：每个组件在干什么、它们靠什么配合、搭建时踩过哪些坑。

## 一、五个组件各自的职责边界

| 组件 | 它解决的唯一问题 | 不负责什么 |
| --- | --- | --- |
| **FastAPI** | 把图变成可被 HTTP 消费的产品能力 | 不参与"模型怎么想、工具怎么调" |
| **LangGraph** | 控制流与状态管理：多轮循环、条件跳转、中断恢复 | 不负责与具体模型厂商对接 |
| **LangChain** | 统一抽象与生态：模型、工具、消息、输出格式 | 不负责编排（1.0 起编排交给 LangGraph） |
| **Redis** | 状态的外部持久化：会话记忆跨进程、跨副本存活 | 不参与推理，也不需要理解消息语义 |
| **LangSmith** | 可观测性：把执行过程变成可检索、可评测的数据 | 不在请求链路上，挂了不影响服务 |

关键判断：**这五个东西不是"叠加"，而是各自填一个正交的坑**。
少了任何一个，你都会在某个具体场景里卡住——记忆没法跨重启、出问题只能靠打印日志、
或者为了改一条流程要把整个服务重写。

## 二、静态关系图

```
                   ┌──────────────────────── FastAPI ─────────────────────────┐
   浏览器 / 客户端  │  routes.py   会话编排、参数校验、SSE 序列化                  │
        │          │  main.py     lifespan：按顺序装配并持有下游依赖             │
        │  HTTP    └───────┬────────────────────────────┬─────────────────────┘
        └──────────────────┤                            │
                           │ 1. 编译一次，进程内复用        │ 2. 读配置
                           ▼                            ▼
                   ┌───────────────┐            ┌───────────────┐
                   │   LangGraph   │            │  settings.py  │◀── .env / 环境变量
                   │  StateGraph   │            └───────────────┘
                   │               │
                   │  agent ⇄ tools│────────────▶ LangChain：ChatOpenAI + @tool
                   └───┬───────┬───┘               （统一模型与工具抽象）
                       │       │
         3. 状态快照    │       │ 4. 每一次节点执行
                       ▼       ▼
              ┌────────────┐  ┌──────────────┐
              │   Redis    │  │  LangSmith   │
              │ checkpointer│ │  trace / eval│
              └────────────┘  └──────────────┘
```

## 三、一次请求的完整时序

以 `POST /chat`「帮我算一下 (128+72)*3/4」为例：

```
客户端        FastAPI          LangGraph        DeepSeek        Redis        LangSmith
  │             │                  │                │             │              │
  ├─POST /chat─▶│                  │                │             │              │
  │             │ ① 校验入参、生成/复用 thread_id    │             │              │
  │             │ ② 组 RunnableConfig（含元数据）    │             │              │
  │             ├─────────────────▶│                │             │              │
  │             │                  │ ③ 按 thread_id 读检查点       │              │
  │             │                  ├─────────────────────────────▶│              │
  │             │                  │◀─────────────────────────────┤（首轮为空）    │
  │             │                  │ ④ agent 节点：SystemMessage + 历史消息         │
  │             │                  ├───────────────▶│ ⑤ 决定调用 calculator        │
  │             │                  │◀───────────────┤ tool_calls=calculator       │
  │             │                  │ ⑥ 条件边判定：有 tool_calls → 走 tools 节点    │
  │             │                  │ ⑦ ToolNode 真正执行 calculator("(128+72)*3/4")│
  │             │                  │ ⑧ 结果包成 ToolMessage 写回状态               │
  │             │                  ├─────────────────────────────▶│ ⑨ 存快照      │
  │             │                  │ ⑩ 边 tools → agent，回到模型  │              │
  │             │                  ├───────────────▶│ ⑪ 带着工具结果生成最终答案    │
  │             │                  │◀───────────────┤「= 150」     │              │
  │             │                  ├─────────────────────────────▶│ ⑫ 存快照      │
  │             │◀─────────────────┤ ⑬ 返回最终 state              │              │
  │◀─JSON 响应──┤ ⑭ 取最后一条 AIMessage 作为 answer               │              │
  │             │                  │                │             │              │
```

全程 LangSmith 通过回调在旁路并行收集每一步的输入输出，**不在关键路径上**。

第二轮请求带上同一个 `thread_id`，③ 读检查点时就能拿到上一轮的全部消息，
所以模型能理解"刚才那个结果"指的是什么——这就是记忆的全部魔法，没有额外机制。

## 四、它们靠什么配合起来

这是整个项目最值得理解的部分。四种耦合方式，按耦合强度从低到高：

### 1. 接口耦合（最松，也最重要）

LangGraph 只定义抽象，不关心实现：

```python
class BaseCheckpointSaver(ABC):
    async def aget_tuple(...) -> CheckpointTuple | None: ...
    async def aput(...) -> RunnableConfig: ...
```

`langgraph-checkpoint-redis` 提供 `AsyncRedisSaver`，库自带的 `InMemorySaver` 也实现同一套方法。
所以 `memory.py` 里能用十几行代码在两者之间切换，而 `graph.py` 一行都不用改。

同样的模式还出现在：`BaseChatModel`（OpenAI / DeepSeek / 通义都一样用）、
`BaseTool`（`@tool` 装饰器产出的都是同一种结构）。

**这是"可替换"的来源**：换 Redis 集群、换模型厂商、换向量库，都只动配置或一个工厂函数。

### 2. 约定耦合（隐式的，最容易踩坑）

`thread_id` 是全系统唯一的会话主键，但它是一个**字符串约定**，不是类型：

```python
config = {"configurable": {"thread_id": "..."}}
```

LangGraph 用它定位检查点，Redis 用它组织键名
（`checkpoint:{thread_id}:{namespace}:{checkpoint_id}`），LangSmith 用它做 trace 元数据。

三套系统靠同一个字符串串起来，谁都没显式声明这个契约。
**推论**：多租户场景下，`thread_id` 必须由服务端生成或严格校验，
否则用户传别人的 ID 就能读到别人的对话历史（越权漏洞）。

### 3. 环境变量耦合（无侵入）

LangSmith 的接入方式最特别：代码里没有一行"上报"逻辑。

```python
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "..."
```

LangChain 内部在创建模型客户端时读取这些变量，自动挂上 tracing 回调。
**代价是有时序要求**：必须在第一次模型调用之前设置，之后再改不生效——
这就是 `setup_observability()` 被放在 lifespan 最前面的原因。

### 4. 生命周期耦合（显式装配）

`main.py` 的 lifespan 用代码固定了启动顺序：

```
读配置 → 开追踪 → 建 Redis 连接 → 起检查点 → 编译图 → 对外服务
                                                        ↓
                                            关闭连接、释放资源
```

这条顺序不能随意调换：先编译图再开追踪，这次的 trace 就丢了；
先起检查点再读配置，就连不上正确的 Redis。

**为什么不用全局变量**：生命周期写在函数里是可读、可测的；全局变量会把这层依赖关系藏起来，
出问题时你只能靠猜启动顺序。

## 五、关键数据结构

### AgentState：图里流动的唯一状态

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

`Annotated[..., add_messages]` 里的 `add_messages` 是 **reducer**：
节点返回 `{"messages": [新消息]}` 时，LangGraph 调用它把新消息**合并**进已有列表，
而不是覆盖。用普通 `list` 会出现"每轮只剩最后一条消息"。

重要细节：`add_messages` **按消息 ID 去重**，同一个 ID 是"替换"而不是"追加"。
重放消息、复用消息对象时容易踩到。

### RunnableConfig：一次运行的身份与元数据

```python
{
    "configurable": {"thread_id": "..."},   # 影响逻辑
    "run_name": "agent-loom.chat",          # 只影响 LangSmith 展示
    "tags": ["agent-loom", "single-agent"],
    "metadata": {"thread_id": ..., "user_id": ...},
}
```

前半部分决定程序行为，后半部分只决定**可观测性**。分开的好处是：
可以往 metadata 里塞任意业务字段（渠道、实验分组、用户等级）而不担心影响执行。

## 六、设计取舍

| 决策 | 为什么 |
| --- | --- |
| 手写 StateGraph，不用 `create_agent` 封装 | 教学与排障优先：每一步跳转都在明面上，出问题能定位到节点 |
| 系统提示词每轮现拼，不写入状态 | 它是常量。写进状态会被 checkpointer 反复持久化，Redis 里堆满重复内容 |
| 图在 lifespan 里只编译一次 | 编译产物内部持有连接池；每请求新建会迅速耗尽 Redis 连接 |
| 工具用 AST 白名单求值，不用 `eval()` | 工具入参来自模型，等同不可信输入。`eval` 等于把 RCE 交给模型 |
| SSE 手写而不是引第三方库 | 帧格式就三行，少一个依赖就少一处升级负债 |
| checkpointer 提供 memory 实现 | 让测试与本地开发零外部依赖，CI 上不用起 Redis |
| `/healthz` 真的 ping Redis | 只证明"进程活着"的探活会在依赖挂掉时误导运维 |
| 租户隔离用"派生存储键"而不是 owner 注册表 | 注册表有查询竞态、TTL 不一致、新增接口容易漏检查；派生键不需要任何检查 |

## 七、多租户与安全模型

### 威胁模型与对应防护

| 攻击方式 | 防护手段 |
| --- | --- |
| 无凭据调用 | 除 `/healthz` 外全部要求 `Authorization: Bearer`；未配置凭据时 fail closed |
| 逐字节猜 Key | `secrets.compare_digest` 常数时间比较，堵住基于响应时间的爆破 |
| 用别人的 `thread_id` 读对话 | 内部键 = `{user_id}:{客户端 id}`，构造不出别人的键 |
| 用别人的 `thread_id` 删对话 | 同上，删除也只作用于自己的键 |
| 探测某个 `thread_id` 是否存在 | 不存在与无权访问都返回空列表，不泄露存在性 |
| 用"我是谁"字段冒充他人 | 请求体里根本没有身份字段 |
| 弱 Key / 配置格式错误 | 启动时校验长度与格式，不合格直接拒绝启动 |

### 为什么用"派生键"而不是"owner 注册表"

常见做法是维护一张 `thread_id → user_id` 的注册表，每次请求先查归属再放行。三个问题：

1. 查询与写入之间有竞态窗口；
2. 注册表自身的 TTL 必须与检查点严格一致，否则会出现"注册表过期但对话还在"的缝隙；
3. 新增一个按 `thread_id` 操作的接口时，很容易忘记加检查——漏一次就是一个漏洞。

派生键一次性消掉全部三个问题：**不需要存储、不需要查询、不需要记得检查**。
`internal = f"{user_id}:{public_id}"` 是纯函数，给不出正确输入就拿不到数据。

代价是拿不到"某用户有哪些会话"的列表——需要的话得另外建索引。这是用功能换安全，取舍是明确的。

### 当前边界

- 鉴权是**静态 API Key**（配置在 `.env`），没有注册登录，也没有密钥轮换与吊销。
- 没有速率限制与配额，单个租户可以打满模型额度。
- `user_id` 只是字符串标识，没有用户资料、权限分级、审计日志。
- 生产化还需要：密钥管理服务、按租户限流、审计日志、会话列表索引。

## 八、踩坑记录

都是搭建过程中真实撞到的。

### 1. Redis 必须是 Redis Stack，不是原生 Redis

`AsyncRedisSaver.asetup()` 会创建 RediSearch 索引（`FT.CREATE`）。
原生 Redis 没编译这个模块，启动直接失败。

**处理**：用 `redis/redis-stack-server` 镜像。
**验证**：`FT._LIST` 应返回 `['checkpoint', 'checkpoint_write']`。

### 2. Windows 上 zoneinfo 找不到时区

`ZoneInfo("UTC")` 在 Windows 抛 `ZoneInfoNotFoundError`，
而工具里的 `except` 把它转成了"未知时区：UTC"——**静默降级，比报错更难查**。

**处理**：加 `tzdata` 依赖。（Linux 用系统时区库，所以这个问题只在 Windows 出现。）

### 3. add_messages 按 ID 去重

测试里"历史消息少了一条"，排查后发现假模型两轮返回了同一个消息对象（同一个 ID），
于是第二轮**替换**了第一轮而不是追加。

**处理**：每次返回新 ID。
**教训**：生产里重放消息、缓存消息对象时同样会踩。

### 4. 配置拼写错误 + `--reload` 掩盖崩溃现场

`.env` 里写成 `LANGSMITH_TRACING=ture`：

```
pydantic_core.ValidationError: Input should be a valid boolean, input_value='ture'
```

配置校验在 `create_app()` 阶段就失败，应用 import 就崩。
但 `--reload` 的**父进程**还活着，于是 `docker ps`/任务管理器里能看到 uvicorn 进程，
8000 端口却从来没被绑定，浏览器报"积极拒绝"。

**排查命令**（先看端口，再看进程，别被"进程还在"骗了）：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen   # 空 = 真的没在服务
```

### 5. `--reload` 不监听 .env

uvicorn 默认只监听 `*.py`。改了 `.env` 不重启，配置不会生效。
**处理**：改配置后必须 Ctrl+C 重启（或起服务前先验证配置能解析）。

### 6. LangSmith 环境变量有时序要求

tracer 在创建客户端时读环境变量，之后再设置无效。
**处理**：`setup_observability()` 放在 lifespan 第一步，先于编译图。

### 7. SSE 需要关闭反向代理缓冲

响应头加了 `X-Accel-Buffering: no`。否则 Nginx 会把流式响应攒成一批再吐，
前端看到的打字机效果消失——本地开发完全正常，一上生产就"变慢"。

### 8. 身份来自请求体（v0.1 的真实设计缺陷）

最初的 `ChatRequest` 里有个 `user_id` 字段，由客户端传入，`thread_id` 也完全由客户端决定。
这不是鉴权：换个 `user_id` 就能以别人的身份写数据，构造一个别人的 `thread_id`
就能读到对方完整的对话历史——而且**日志里看不出任何异常**。

**修复（v0.2.0）**：删掉请求体里的身份字段，身份只能从 `Authorization: Bearer` 解析；
内部 `thread_id` 由 `{user_id}:{客户端 id}` 派生，越权在结构上不可能发生。

教训：身份字段只要出现在请求体里就是漏洞，**不管它当前有没有被用于鉴权**——
它迟早会被某个新接口顺手用上。

## 九、演进到多 agent

当前结构里，多 agent 的改造点只有 `graph.py`：

1. **加中断审批**：`builder.compile(checkpointer=..., interrupt_before=["tools"])`，
   恢复时用 `Command(resume=...)`。高风险工具执行前挂起，等人工确认。
2. **拆子图**：把 `agent` 换成一个 supervisor 节点，每个 worker 是一张编译好的子图，
   通过 `add_node("worker_a", subgraph)` 挂进来——父图和子图共享同一套检查点机制。
3. **加长期记忆**：`AsyncRedisStore` 存跨会话的用户偏好，与按会话隔离的 checkpointer 分工：
   一个存"这次对话说到哪了"，一个存"这个用户是谁"。
4. **加评测**：LangSmith 数据集 + evaluator，每次改提示词跑回归，把"感觉变好了"变成可量化的结论。

记忆层、可观测层、服务层在这四种改造里**一行都不用动**——
因为它们依赖的是接口和约定，不是具体的图结构。
