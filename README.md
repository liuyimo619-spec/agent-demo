# 旅行计划生成与管理 Agent

基于 **LangChain v1 / LangGraph v1** 的多轮旅行规划 Agent。用户用自然语言提出需求，Agent 通过高德地图 MCP、联网搜索和用户记忆收集信息，生成结构化行程，落库成可迭代的版本，并在入库前做一次独立校验。

前端是一个单文件流式对话页，后端是 FastAPI + PostgreSQL。

---

## 演示

96 秒录屏：[v1.0.0 演示视频](https://github.com/liuyimo619-spec/agent-demo/releases/tag/v1.0.0)（MP4，23.3 MB）

依次演示：登录 → 生成计划卡片并修改 → 切换账号验证记忆隔离 → 300 秒总时长熔断后继续对话 → 领域守卫拒绝无关输入 → 计划表评判员连续打回不合理计划（如博物馆排周一闭馆）→ 超步硬上限 → 五层限制架构图。

> 视频使用本机测试账号录制，数据库与服务配置不随仓库发布；在新环境按「快速开始」配置后即可复现。

---

## 功能

- **自然语言规划**：支持口语化（"下周六去成都玩两天，别太累"）和明确约束（"2 人、2 天、人文类、含午餐"）两类输入，输出按天、按时间段的行程。
- **流式输出 + 计划卡片**：模型只输出 4–5 句口语介绍，计划表本身以 JSON 卡片渲染，不混进对话正文；卡片上方固定显示"实际价格以现场为准"。
- **多轮修改与版本管理**：同一城市在工作台内递增版本号，可列举/读取/删除；用户确认后归档为正式计划，并清空该用户的内存对话。
- **用户长期偏好**：按 8 个固定维度（风格/饮食/住宿/活动/避免/必须体验/氛围/特殊需求）保存与检索。
- **冷区**：定稿后的城市与景点进入 3 年冷却期，默认不再推荐（用户强烈要求除外）。
- **领域守卫**：与旅行无关的输入在入口直接拒绝，不进入主 Agent。
- **计划表评判员**：`save_plan` 写库前调用一次小模型，用高德返回的开放时间核对"周一闭馆却排周一""编造无材料的景点"等问题，不合理则拒绝入库并返回原因。
- **认证与会话**：argon2 密码哈希；登录下发随机 token，数据库只存 SHA-256 摘要；令牌 7 天固定有效期。
- **运行时护栏**：工具选择、未注册工具拦截、逐工具调用上限、超步软/硬熔断、总时长与空闲双看门狗。

---

## 使用流程

1. **提需求**：口语（"下周六去成都玩两天，别太累"）或精确约束（"2 人、2 天、人文类、含午餐"）均可。
2. **生成与迭代**：`save_plan` 写入工作台，同城每保存一次版本号 +1，旧版本保留 7 天。
3. **定稿**：`confirm_plan_version` 把指定版本归档到 `travel_plans`，删除该城市的工作台行，并把城市与计划内景点写入冷区。
4. **下一轮**：`get_cold_zone_raw` 读出冷区，Agent 据此避开已定稿的城市与景点。

> 只生成、不定稿时，计划只存在于工作台，冷区不会有任何记录。

---

## 架构

```
浏览器（frontend/index.html，单文件 SPA）
  │  POST /agent/旅行agent/   [Authorization: Bearer <token>, user_id: <uid>]
  ▼
FastAPI（api.py）
  ├─ verifytoken 依赖 ──→ auth.py（刷新 last_active，返回 user_id）
  ├─ 领域守卫（domain_guard.py，15s 超时，非旅行返回 400）
  ├─ 注入当前时间前缀
  └─ chat_loop（agent.py）──→ LangGraph create_agent
        │
        ├─ 中间件链（middleware.py）
        │    每次模型调用前：修复孤立 tool_call → 历史裁剪(20 轮)
        │    → 加载未注册工具/逐工具限额 → 只暴露已选工具 + 关思考 + 超步降级
        │    → 步数计数/熔断 → 动态系统提示词
        │    Agent 结束后：定稿清内存 · 计划卡注入
        │
        └─ 工具层
             高德 MCP（限流 3 QPS + 返回瘦身，剔除导航类工具）
             web_search（Linkup）
             长期偏好（Memory.py）
             计划表与工作台（plan_manager.py；save_plan 写库前同步调用评判员）
             冷区（cold_zone.py）
             select_tools（内置工具选择器）
  │
  ▼
PostgreSQL（ORM_SQLAlchemy.py / sql语句.sql）
```

流式协议很简单：后端以 `text/plain` 持续输出正文分片；计划表用固定标记 `\n\n[[PLAN_CARD]]\n` 与正文分隔，标记之后是计划 JSON，前端据此切分并渲染卡片。

---

## 请求链路

```mermaid
flowchart LR
    U["用户输入<br/>POST /agent/旅行agent/"] --> T{"token 校验"}
    T -- 失败 --> T1["401"]
    T -- 通过 --> G{"领域守卫<br/>fail-closed · 15s"}
    G -- 三角形 --> G1["400 非旅行请求"]
    G -- 超时 --> G2["504"]
    G -- 系统错误 --> G3["500"]
    G -- 其他 --> A["主 Agent<br/>五层限制（见下）"]
    A --> DONE["结束<br/>4–5 句介绍 + 计划卡"]
    A --> F1["兜底：52 步硬熔断"]
    A --> F2["兜底：总时长 / 空闲双看门狗"]
    F1 --> SOFT["道歉话术<br/>提示重新询问"]
    F2 --> SOFT
```

### 主 Agent：五层限制

从外到内，请求每进一层就多一组约束；运行中触发阈值时，则是外层能力被一层层剥掉，而不是整单失败。

```mermaid
flowchart TB
    subgraph L1["① 会话层　每次模型调用前"]
        direction TB
        N1["修复孤立 tool_call · 历史裁剪（超过 20 轮）· 注入当前时间"]
        subgraph L2["② 可见性层　每次模型调用前"]
            direction TB
            N2["只暴露 select_tools + save_plan + 本轮已选工具<br/>save_plan 返回后关闭思考<br/>超过 45 步只留 save_plan"]
            subgraph L3["③ 执行层　每次工具调用"]
                direction TB
                N3["入参哨兵清洗 · 未注册工具拦截<br/>逐工具上限 3–8 次 · 52 步硬熔断"]
                subgraph L4["④ 业务层　核心"]
                    direction TB
                    N4["计划表评判员：不合理则拒绝入库、不发卡片<br/>合理则写工作台 · 定稿写穿三张表 · 冷区 3 年"]
                end
            end
        end
    end
    L4 --> OUT["输出层<br/>计划卡注入 + 标记切分 + 4–5 句介绍"]
    OUT --> U2["用户"]
    L4 -. 兜底 .-> CUT["总闸 / 空闲闸 / 硬熔断打断"]
```

剥洋葱的三段：**全部已选工具 →（45 步）仅剩 `save_plan` →（52 步或撞闸）强制结束并给出话术**。最内层永远是"把已有信息落成一张计划表"，所以卡住的单也会以一个明确的结局收尾，而不是无声中断。

---

## 技术栈

| 层 | 选择 |
| --- | --- |
| Agent 框架 | LangChain v1 `create_agent` + LangGraph v1（装饰器中间件、自定义 state schema） |
| 模型 | 通义千问（OpenAI 兼容协议）；主模型、小模型（守卫/评判员，关闭思考）、主模型关思考实例（`save_plan` 返回后使用） |
| 外部工具 | 高德地图 MCP（SSE）、Linkup 联网搜索 |
| Web | FastAPI + StreamingResponse（`text/plain`） |
| 数据库 | PostgreSQL（asyncpg + SQLAlchemy 2.0 异步 ORM） |
| 认证 | argon2-cffi（密码）+ secrets/SHA-256（令牌） |
| 可观测性 | LangSmith（可选，`LANGSMITH_TRACING`） |
| 包管理 | uv（`pyproject.toml` + `uv.lock`） |

---

## 核心设计

**1. 工具按需加载，而不是一次性挂满**
所有业务工具都注册进 Agent，但默认只有 `select_tools` 和 `save_plan` 可见。模型先调用 `select_tools` 选取本轮要用的工具，选中名单写入 `loaded_tools` 状态；`expose_selected_tools` 在每次模型调用前只把名单内的工具暴露给模型，`auto_load_tool_middleware` 在真正执行时兜底记录并拦截未加载的工具。既压缩了每次请求的 tool schema，也让"该不该用这个工具"变成一次显式决策。

**2. 三层运行护栏**
- 逐工具调用上限（`TOOL_LIMIT`）：防止同一工具换参数反复重试；
- 超步计数：46 步起给出警告（`step_count > 45`），超过 45 步只暴露 `save_plan`，52 步强制跳转结束；
- 双看门狗：总时长 300s 硬闸 + 单次等待 60s 空闲闸。

**3. 计划表流水线**
`save_plan` 接收强类型 `PlanData`（城市 / 天 / 活动三级嵌套，星期用枚举，价格字段标注"禁止填写"）→ 内部累加每天费用与总价 → 从当前线程倒序收集 `maps_search_detail` 的"景点名 + 开放时间"作为材料，与用户原话一起组装成消息列表交给计划表评判员 → 返回结构化判定：合理则写入工作台，并把计划 JSON 放进 `ToolMessage.artifact`；不合理则返回 `status=error` 的 ToolMessage，说明位置与原因，既不写库也不发卡片。`append_plan_card_middleware` 再把 artifact 转成带 `render_type=plan_card` 的 AIMessage，`chat_loop` 输出 `[[PLAN_CARD]]` 标记，前端据此渲染卡片。

**4. 记忆分四层**
| 层 | 存储 | 生命周期 |
| --- | --- | --- |
| 对话上下文 | `InMemorySaver` checkpointer（按 user_id 作 thread） | 确认计划后清空；7 天不活跃由后台循环清理 |
| 工作台（短期） | `travel_plan_workspace` | 7 天 TTL，按城市递增版本 |
| 正式计划 | `travel_plans` | 永久，确认版本时从工作台归档 |
| 偏好 / 冷区 | `user_preferences` / `cold_zone_*` | 偏好永久；冷区 3 年（数据库触发器） |

定稿（`confirm_plan_version`）是一次写穿：归档到 `travel_plans`、删除该城市的工作台行、把城市与计划内全部景点写入冷区（去重后 upsert，时间戳由触发器维护），并清空该用户的内存对话。

**5. 高德返回瘦身与限流**
MCP 客户端挂两个拦截器：共享令牌桶把调用间隔排到 3 QPS；结果按黑名单递归剔除导航、照片、坐标串等无用字段，并以紧凑 JSON 回填。返回体积约降一半，直接减少后续每轮的输入 token。

---

## 目录结构

```
.
├─ api.py                 FastAPI 应用、路由、CORS、静态前端挂载、生命周期
├─ agent.py               Agent 工厂、自定义 state、chat_loop（流式 + 重试 + 看门狗）
├─ middleware.py          8 个中间件：工具选择、限额、超步、计划卡、历史裁剪…
├─ tools.py               高德 MCP 接入（限流/瘦身拦截器）、web_search、select_tools
├─ models.py              主模型 / 小模型 / 关闭思考模型
├─ prompts.py             系统提示词（工具铁律、高德调用顺序、费用真实性、场景适配、输出约束、降级）
├─ auth.py                注册 / 登录 / 登出 / 令牌校验 / 过期记忆清理循环
├─ security.py            argon2 密码哈希、令牌生成与摘要
├─ errors.py              异常归因与可重试判定
├─ logger.py              按天滚动的业务日志（agent_YYYYMMDD.log）
├─ cleanup_expired.py     过期数据清理脚本（工作台 / 冷区 / 令牌）
├─ memory/
│  ├─ ORM_SQLAlchemy.py   表模型与异步 Session
│  ├─ checkpointer.py     InMemorySaver 实例
│  ├─ Memory.py           用户长期偏好工具
│  ├─ plan_manager.py     计划表 / 工作台 / 版本 / 冷区写入等 15 个业务工具
│  ├─ cold_zone.py        冷区查询工具
│  └─ domain_guard.py     领域守卫 + 计划表评判员
├─ frontend/index.html    流式对话页 + 登录 + 计划卡片渲染（无构建）
├─ tests/test_offline.py  离线单测（28 例：不联网 / 不连库 / 不调模型）
├─ sql语句.sql            数据库建表脚本（含触发器与索引，可作为 schema 真相源）
└─ pyproject.toml / uv.lock
```

---

## 测试

仓库内置 28 个离线用例，不联网、不连数据库、不调用模型：

```bash
uv run python -m unittest discover -s tests -v
```

覆盖工具名合并、历史裁剪（20 轮）、逐工具调用上限、超步熔断、`select_tools` 校验、高德返回瘦身、密码与令牌哈希、异常归因、计划表结构与 `save_plan` 入库流水线。导入模块时会读取 `.env`（模型与数据库连接串在模块级初始化），用例本身不发起网络请求，也不写库。

---

## 快速开始

**环境**：Python ≥ 3.14、PostgreSQL、uv

1. 安装依赖

```bash
uv sync
```

2. 配置环境变量（复制 `.env.example` 为 `.env` 后填写）

| 变量 | 用途 |
| --- | --- |
| `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` | 通义千问（OpenAI 兼容）接入 |
| `DASHSCOPE_MODEL_ID` | 主模型 |
| `DASHSCOPE_MODELx_ID` | 小模型（领域守卫、计划表评判员） |
| `AMAP_KEY` | 高德 MCP 密钥 |
| `LINKUP_API_KEY` | 联网搜索密钥 |
| `ASYNC_DATABASE_URL` | `postgresql+asyncpg://user:pass@host:port/db` |
| `CHAT_TOTAL_TIMEOUT` / `CHAT_IDLE_TIMEOUT` | 总闸 / 空闲闸秒数，默认 300 / 60 |
| `LANGSMITH_*` | 可选，链路追踪 |

3. 建库

```bash
createdb -U postgres travel_agent_db
psql -U postgres -d travel_agent_db -f "sql语句.sql"
```

4. 启动（前端由 FastAPI 直接托管在同源 `/`）

```bash
uv run uvicorn api:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`，先在页面顶部注册账号，再在输入框提问。

> 必须在项目根目录启动：`StaticFiles(directory="frontend")` 按当前工作目录解析，换个目录启动会直接报目录不存在。

> 可选：`uv run python cleanup_expired.py` 手动清理过期的工作台、冷区与令牌数据。

---

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/Register/` | 注册，返回 `[user_id, token]` |
| POST | `/Login/` | 登录，返回 `[user_id, token]` |
| POST | `/agent/Logout/` | 登出（需 Bearer + user_id） |
| POST | `/agent/旅行agent/` | 对话主接口，流式返回；需 Bearer + user_id |
| GET | `/` | 前端页面 |

对话请求体只有 `{"message": "..."}`（1–500 字符）。非旅行领域返回 400，领域守卫超时返回 504。

---

## 设计取舍与已知限制

- **单进程内存会话**：`InMemorySaver` 让上下文只存在于当前进程。多 worker 部署会串会话，上云需固定单 worker 或改用持久化 checkpointer。
- **同账号多设备并发（待修复）**：允许多个设备同时登录，彼此不会被挤下线，但 `thread_id = user_id`，因此同一进程内共用后端对话上下文，前端聊天记录却不会跨设备自动同步。前端 `busy` 仅防止当前页面重复发送，后端没有按账号串行处理；不同设备同时发消息时，请求会重叠执行，最终保存的对话历史可能缺失部分回复。
  - 验证范围：当前安装的 LangGraph + `InMemorySaver` 纯内存最小图中，同一线程并发输入 `A`、`B`，两个请求分别拿到 `reply-A`、`reply-B`，但最终读取的历史为 `A、B、reply-B`，缺少 `reply-A`。此结果不代表每次并发必现，也不是完整双设备业务测试；未调用模型或操作数据库。
  - 临时使用限制：同账号等待本轮回复结束后再发送下一条消息。待增加按 `user_id` 的单请求保护，覆盖从开始处理到流式响应结束或取消的完整生命周期；同账号已有任务时拒绝新请求并给出提示，不影响不同账号并发。该保护尚未实现。
- **同一会话连续创建不同计划（质量与稳定性限制）**：同一账号在对话内存未清理前多次创建不同旅行计划，各次规划的用户需求、工具结果和计划内容会累积在同一条对话历史中，而不会按每份计划独立隔离。即使请求串行执行，首份计划之后的其他计划也可能受到前序需求和材料的干扰，无法保证质量与输出稳定性。
  - 清理规则：服务启动时先检查一次，之后每 6 小时检查一次；按会话表中该账号最近的 `last_active` 判断，只清理超过 7 天未活跃账号的对话内存，并非每 6 小时全部清空。`confirm_plan_version` 定稿成功后，也会在本轮结束时清空该账号的对话内存。
  - 历史裁剪：超过 20 条用户消息时保留最近 20 轮对话，这是滑动窗口裁剪，不是清空或按计划隔离。持续活跃且未定稿时，不同计划仍可能共用保留下来的上下文。
  - 范围说明：这里的“合并”指共用对话上下文，不是数据库中的计划记录合并。
- **登出后对话内存可能漏清（待验证）**：`logout_user` 删除当前令牌记录，但不删除后端对话线程。若登出后该账号在 `user_sessions` 中已无任何记录，`count_memory_within_time` 仅扫描该表的分组查询便无法选中该账号，进程中残留的对话内存可能无法通过“7 天未活跃”清理机制回收。此风险由代码推导，尚未复现；登出不等于清空对话内存。
- **同账号同城并发保存可能撞版本号（待验证）**：`_generate_plan_key` 先查询 `MAX(version_no)` 再加一，查询和计划写入分属不同事务，且没有同账号请求互斥。两个请求可能读到相同的最大版本号，随后写入相同的下一版本，触发 `(user_id, city, version_no)` 主键冲突，使其中一次保存失败，而非自动覆盖已有版本。此风险由代码推导，尚未进行并发复现。
- **领域守卫是 fail-closed**：拒绝成本低、污染成本高，所以守卫异常时不放行。
- **计划表不含住宿**：总价只加门票、餐饮、交通；卡片上明确标注"不含住宿"。
- **门票价可能为 0**：高德详情接口的 `cost` 字段常为空，此时按 0 计，计划表不代表实时票价。
- **闭馆校验依赖 `day_of_week`**：计划表里只存星期而不存具体出行日期，评判员能核对"周一闭馆 vs 周一安排"，但无法核对"用户给的日期区间"。
- **评判员是模型判定，不是硬约束**：规则覆盖闭馆冲突、编造景点、重复景点与体力安排；材料取自整条线程的 `maps_search_detail`，未按城市或轮次过滤，同一线程切换城市时历史材料会一并送入。
- **旁白与总价可能不一致**：卡片总价由代码累加，介绍文案由模型生成，提示词只能做软约束；卡片上的价格提示与"不含住宿"标注即为此兜底。
- **过程旁白靠提示词约束**：系统提示词要求查询阶段最多一句进度说明，实际输出仍可能超出，属于已知的软约束。
- **日志为按天文件**：`agent_YYYYMMDD.log` 写在仓库根目录，已被 `.gitignore` 忽略。

---

## 说明

项目为个人练习作品，用于学习 Agent 工程化：工具编排、状态与中间件、结构化输出、记忆分层、外部 API 治理与失败降级。不提供真实的预订、支付或导航能力。

---

## License

[MIT](LICENSE) © 2026 乱七八糟炒饭 (mmfr)
