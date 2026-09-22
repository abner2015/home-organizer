# ARCHITECTURE — 系统架构

> 描述系统的组件划分、职责、关键数据流与部署视图。
>
> 最后更新：2026-09-22 —— 新增 §4.3「反向录入 —— 手动落位（全程零 LLM）」，§12 演进路线对齐。
> （2026-09-21：本版按**真实代码布局**重写。原稿的 `apps/api/`、`packages/ai/`、
> `app/agent/orchestrator.py`、以及「pipeline 的 Step 1 是 Vision」都与实际不符，已全部订正。）
>
> 状态图例：✅ 已交付 · ⏳ 已排期未实现（见 `docs/DEVELOPMENT_PLAN.md` 下篇）· 📋 设计稿
>
> **代码是唯一事实来源。** 本文档描述「现在长什么样」，与代码冲突时以 `services/api/app/` 为准。

---

## 1. 设计目标

1. **业务代码与 AI Provider 解耦**：业务层只依赖 `AIProvider` Protocol，不认识任何具体供应商。✅
2. **可观测**：每次 AI 调用落一条 `AgentTrace`；所有 API 有结构化日志 + `request_id`。✅（部分，见 §8）
3. **可移植**：单条 `docker compose up` 起后端全套。✅（Web 未容器化，见 §11）
4. **可演进**：新增多轮对话、pgvector 检索时不需要重写核心流程。✅
5. **不引入不必要的复杂基础设施**：只用 PostgreSQL + Redis + MinIO。✅

---

## 2. 架构总览

### 2.1 本地开发（当前实际形态）

```
┌─────────────────────────────────────────┐
│  Web — apps/web                          │  Next.js 14 App Router
│  http://127.0.0.1:18080                  │  Server Components + 少量客户端岛
└─────────────────────────────────────────┘
                    │  fetch('/api/v1/...')   ← 浏览器同源请求
                    ▼
┌─────────────────────────────────────────┐
│  Next.js rewrite                         │  next.config.mjs: /api/:path* → API
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│  API — services/api                      │  FastAPI + uvicorn  :8000
└─────────────────────────────────────────┘
        │                │            │
        ▼                ▼            ▼
   PostgreSQL         Redis      存储后端 (local | minio)      AIProvider ──► 远端多模态 LLM
   （唯一状态）   （仅健康检查）  （物品图片）
```

### 2.2 生产目标形态（📋 部分未接进 compose）

```
[Client] ──HTTPS──► [Nginx] ──/api/*──► [api:8000]
                       │──/──────► [web:3000]
                       └──/minio/*► [minio:9000]
                                        │
                                        ├─► postgres:5432
                                        └─► redis:6379
```

`infra/nginx/default.conf` 已写好，但 **nginx 与 web 都不在 `docker-compose.yml` 里**
（compose 只有 `postgres` / `redis` / `minio` / `minio-init` / `api`），`apps/web/` 也没有 Dockerfile。

---

## 3. 模块与职责

### 3.1 `apps/web`（Next.js）

- 用户界面：注册登录、空间树浏览、物品录入、推荐展示与决策、AI 助手对话。
- 调用后端 API 完成所有业务逻辑；**不直接调用 AI Provider**，API Key 不进浏览器。
- 所有请求经 `next.config.mjs` 的 `/api/:path*` rewrite 打到 API —— 前端只有 `src/lib/api.ts`
  一处硬编码 API 路径。
- 会话：`Authorization: Bearer` 存 **httpOnly cookie**（`src/lib/cookies.ts`），
  家选择存 `X-Home-Id`（`src/lib/session.ts` 浏览器端 / `src/lib/session.server.ts` 服务端）。
  `middleware.ts` 做未登录跳转。

真实路由（`src/app/`）：

| 路由 | 作用 |
| --- | --- |
| `/` | 首页 / 概览 |
| `/login`, `/signup` | 认证 |
| `/home` | 家庭空间概览 |
| `/home/rooms` | 房间列表 |
| `/home/storage` | 收纳空间树 |
| `/items` | 物品列表 / 搜索 |
| `/items/new` | 录入新物品（核心流程） |
| `/items/[id]` | 物品详情 / 历史 |
| `/recommendations/[id]` | 推荐决策页 |
| `/assistant` | AI 收纳助手（多轮） |

> ⚠️ 原稿写的是 `/homes/:homeId/items` 之类的路径 —— **没有 homeId 段**，home 是通过
> `X-Home-Id` 请求头传递的（见 §7）。原稿的 `/homes/:homeId/rules`、`/search` 页也不存在
> （规则管理还没有 UI，搜索长在 `/items` 上）。

### 3.2 `services/api`（FastAPI）

```
services/api/
├── app/
│   ├── main.py                    # FastAPI 装配：路由注册、异常处理器、CORS、中间件
│   ├── core/                      # 横切关注点
│   │   ├── config.py              # pydantic-settings（读 .env）
│   │   ├── exceptions.py          # 领域异常 + 全局异常处理器
│   │   ├── logging.py             # 结构化 JSON 日志
│   │   ├── request_id.py          # request_id 中间件
│   │   └── health.py              # /health（DB + Redis 探活，不探存储）
│   ├── api/
│   │   ├── deps.py                # get_actor：JWT 解析 + X-Home-Id 成员校验
│   │   └── v1/                    # 路由层
│   │       ├── auth.py            # signup / login / refresh / me
│   │       ├── homes.py           # home / rooms / space-tree / slots（只读）
│   │       ├── items.py           # CRUD + recognize + vision + infer
│   │       ├── recommendations.py # recommend / get / accept / reject / patch
│   │       ├── search.py          # NL 搜索（有状态）
│   │       ├── assets.py          # 上传 / 取回 / 删除
│   │       ├── uploads.py         # presign（MinIO 直传）
│   │       └── files.py           # 本地存储后端的签名 URL 读取
│   ├── schemas/                   # Pydantic 请求 / 响应模型
│   ├── models/                    # SQLAlchemy ORM（16 张表）
│   ├── services/                  # 业务逻辑层（见下）
│   ├── agents/                    # 推荐 pipeline 与 NL 搜索编排
│   │   ├── pipeline.py            # 9 步状态机（INTAKE → … → VERIFY → RETRY）
│   │   ├── state.py               # RecommendationState / AgentStepResult / AgentRunResult
│   │   ├── candidate_gen.py       # 确定性候选生成 + 硬过滤
│   │   ├── ranking.py             # 确定性加权打分排序
│   │   ├── context.py             # 家庭上下文装配
│   │   ├── prompt_render.py       # prompt 渲染（str.format_map）
│   │   ├── placement_service.py   # accept / reject / patch 业务逻辑
│   │   └── search/                # NL 搜索：agent / intent / answer / context / history / location / structure
│   ├── verification/              # Verifier：context / rule_engine / checks / verifier
│   ├── tools/                     # 13 个 ToolFn（home / item / context / write）
│   ├── ai/                        # AI Provider 抽象与实现
│   │   ├── provider.py            # AIProvider Protocol + VisionOutput / RankingOutput
│   │   ├── factory.py             # get_provider()，按 AI_PROVIDER 选实现
│   │   ├── errors.py              # AIProviderError 家族
│   │   ├── observability.py       # hash_prompt / scrub_image_url / redact_api_keys
│   │   └── providers/
│   │       ├── mock.py
│   │       ├── openai_compatible.py
│   │       └── anthropic.py
│   ├── storage/                   # 可切换存储后端（见 §6.1）
│   │   ├── backend.py             # StorageBackend Protocol + MinioBackend / LocalBackend
│   │   ├── minio_client.py        # boto3 薄封装
│   │   └── errors.py              # StorageError
│   ├── cache/redis_client.py      # Redis 客户端（目前仅健康检查用到）
│   ├── db/                        # base / session / enums / types / seed
│   └── evaluation/                # 离线评测 harness（python -m app.evaluation）
│   └── agent/prompts/             # ⚠️ 注意是单数 agent —— 模板全在这里
├── alembic/versions/              # 0001_initial_schema / 0002_assets / 0003_update_recommendation_status
├── evaluation/dataset/            # 评测用例 JSON（61 条）
├── tests/{unit,api,db}/           # ⚠️ 没有 integration/ 与 agent/ 目录
├── pyproject.toml
└── Dockerfile
```

> ⚠️ **两个容易踩的路径陷阱：**
>
> 1. **prompt 模板在 `app/agent/prompts/`（单数），agent 代码在 `app/agents/`（复数）。**
>    原稿把两者都写成 `app/agent/`，会找不到 `pipeline.py`。
> 2. **`app/repositories/` 与 `app/domain/` 目前是空包**（只有 `__init__.py`），
>    是分层预留位，还没有代码。原稿把 `orchestrator.py` / `verifier.py` / `tools.py`
>    全画在 `app/agent/` 下，实际它们分别在 `app/agents/pipeline.py`、
>    `app/verification/`、`app/tools/`。

服务层（`app/services/`）：`auth_service` / `security` / `asset_service` / `image_payload` /
`vision_service` / `recognition_service` / `item_inference_service` / `recommendation_service` /
`search_service` / `conversation_service`。

> 原稿列的 `home_service` / `storage_service` / `item_service` / `preference_service` /
> `rule_service` / `upload_service` **都不存在** —— 空间树与物品的读路径直接走
> `app/tools/*`，没有再加一层 service。

### 3.3 `packages/ai` —— 不存在

原稿设想的「独立 AI Python 包」从未创建，**仓库里没有 `packages/` 目录**。
AI 层就放在 `services/api/app/ai/`，只有一个消费方，拆包是纯开销。

---

## 4. 关键数据流

### 4.1 录入物品 → 推荐 Slot

```
[Web] 录入物品（两条入口）
   │
   ├─ A 有照片： POST /api/v1/assets/upload   (multipart，不落库)
   │             → {asset_id, object_key}      local 后端写磁盘 / MinIO 后端走 presign（§6.1）
   │
   └─ B 无照片： POST /api/v1/items/infer {name, description?}
                 → 模型把分类/子分类/尺寸/描述/敏感/需上锁全填好（只推理，不写库）
   ▼
[API] POST /api/v1/items                    → items 行（此时还没有 placement）
   │  可选 POST /api/v1/items/{id}/vision   → 图片 → VisionOutput → 回写 item 字段 + 落 AgentTrace
   ▼
[API] POST /api/v1/recommendations/items/{itemId}/recommend
   │
   │  1. 装配上下文：空间树（Home→Room→Unit→Section→Slot）、active HomeRule、
   │     UserPreference、历史 ItemPlacement
   │  2. 跑 pipeline
   ▼
[Agent pipeline — app/agents/pipeline.py]
   │
   │  1 INTAKE             确定性：读 Item 行
   │  2 UNDERSTAND         确定性
   │  3 RETRIEVE           DB：批量加载候选空间
   │  4 CANDIDATE_GENERATION  确定性 → ≤ 20（= pre_filter_count）
   │  5 FILTER             确定性：rule_engine 硬规则 → post_filter_count
   │  6 RANK               确定性：加权打分排序（category/room/path/capacity/…）
   │  7 DECIDE             LLM ──► RankingOutput，选中 1 个 slot        ← pipeline 里唯一的 LLM
   │  8 VERIFY             确定性：9 个 check_*（存在性/白名单/硬规则/容量/安全/去重/数量）
   │  9 RETRY              VERIFY 失败 → 回 Step 7（≤ 2 次，每次带上 last_failure）
   │     ANSWER / FAILED   终态，**不产生 step**
   ▼
[API] 落 AgentTrace + Recommendation，返回 1~3 个候选 + trace_id
   │
   │  ⚠️ 展示给用户的候选来自 Step 6 的排序结果，不是 LLM 的选择
   ▼
[Web] 用户决策（可先 PATCH 改 chosen_slot_id，此时 status 仍是 pending）
   │
   │  POST /api/v1/recommendations/{recId}/accept   （body 为空）
   ▼
[API] 建 ItemPlacement（source = ai_recommendation | user_manual）+ 回填 chosen_slot_id / status
```

**四个必须记住的事实**（原稿全部说反了）：

1. **Vision 不在 pipeline 里。** 它发生在物品录入期（`POST /items/{id}/vision`），结果存进
   `Item` 行；pipeline 的 Step 1 INTAKE 只是把它读出来。所以 pipeline 里 LLM 只出现**一次**
   （Step 7 DECIDE），不是原稿说的「Vision + Rank 两次」。
2. **DECIDE 只选一个 slot**（`max(candidates, key=confidence)`），VERIFY 校验的也是这一个。
   给用户看的 1~3 个候选来自确定性 RANK 的输出。
3. **RETRY 与 ANSWER / FAILED 不产生 `AgentStepResult`**，`AgentTrace.steps` 里没有它们。
4. **VERIFY 耗尽后的 FAILED 仍带着非空候选列表**（RANK 的结果，`chosen_slot_id = None`）；
   只有「FILTER 之后候选为空」才会 `candidates=[]` + `error="无符合硬规则的位置"`。

### 4.2 NL 搜索

```
[Web] POST /api/v1/search  {query, conversation_id?}
   │
   ▼
[SearchAgent — app/agents/search/agent.py]
   │  1. provider.structured_output(search.v4.md) → ExtractedSearchIntent（8 种 intent）
   │  2. 按 intent 分派到**只读**工具（search_items / get_rooms / build_home_blueprint / …）
   │  3. compose_answer：模型按 answer.v1.md 把确定性草稿润色成中文回复
   │     （模型不可达则回落到草稿，不报错）
   ▼
[API] 写 Conversation / Message 行（会话有状态，回放最近 8 轮）
      写 AgentTrace（**不写 Recommendation** —— 搜索是只读的）
      返回 {state, intent, matches, answer_text, conversation_id, trace_id}
```

> 原稿写的是 `GET /api/items?q=...` 的 ILIKE 查询。那是「按名字搜物品」的**实现细节**，
> 现在它的入口是 `GET /api/v1/items` 的 query 参数；而自然语言问答走的是上面这条链路。

### 4.3 反向录入 —— 手动落位（全程零 LLM）

旅程 B：物品已经在手上、去向也已经知道，就不该走 §4.1 那条链路。

```
[Web] 物品列表的逐件「放到这里」 / 详情页「放到别处」 / /items/place 批量归位
   │
   │  POST /api/v1/placements  {item_id, slot_id, note?}
   ▼
[API] app/api/v1/placements.py
   │
   ▼
[Service] placement_service.place_item → tools/write_tools._create_placement
   │  1. 校验 item / slot ∈ 本 home（否则 404）
   │  2. close_active_placements(item_id)      ← 同一物品恒只有一条 active
   │  3. INSERT ItemPlacement(source='user_manual', recommendation_id=NULL)
   ▼
[DB] item_placements 一行；**AgentTrace 不增、Recommendation 不碰**

移出：DELETE /api/v1/placements/{id} → 只置 removed_at（软关闭，行永不物理删除）
```

> **这条路径与 §4.1 的 accept 共用同一个原语**（`_create_placement`），
> 这正是 P0.3 顺手修掉的那个 bug：accept 过去不关旧行，只靠 PG 的部分唯一索引
> `uq_item_placements_one_active_per_item` 兜底 —— PG 上 `IntegrityError`（500），
> SQLite 上静默留下两条 active。
>
> 原语放在 `app/tools/` 而不是 `app/agents/`：反过来会让低层工具依赖 agents 包（倒置依赖）。

### 4.4 图片存储

见 §6.1。要点：**图片永远不由 Web 直传 API 再转存**（MinIO 模式下浏览器直传，本地模式下
走 multipart 由 API 落盘），且远端模型拿到的图片是**内联 data URI**，不是 URL
（模型解析不了 `localhost:9000`）。

---

## 5. AI 层架构

### 5.1 `AIProvider` Protocol（`app/ai/provider.py`）

```python
class AIProvider(Protocol):
    name: str

    async def vision(
        self,
        image_url: str,               # 可以是 https URL，也可以是 data: URI
        *,
        hint: str | None = None,
        context: str = "",            # 家庭真实数据，用于接地（把 category 限制在真实词表）
        timeout_s: float = 60.0,
    ) -> VisionOutput: ...

    async def chat(self, messages: list[Message], *, timeout_s: float = 60.0) -> str: ...

    async def structured_output(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        timeout_s: float = 60.0,
    ) -> BaseModel: ...

    async def rank_candidates(
        self,
        *,
        item: ..., candidates: list[...], rules: list[...], preferences: list[...],
        history: list[...], last_failure: str | None = None, timeout_s: float = 60.0,
    ) -> RankingOutput: ...
```

> 原稿的方法名是 `vision_recognize`，且**缺 `chat` / `structured_output`**，也没有
> `context` / `timeout_s` 参数 —— 与代码不符。
>
> 另外 `rank_candidates` **没有 `prompt` 参数**，所以每个 provider 自己 deferred import
> `app.agents.prompt_render.build_rank_prompt` 渲染 prompt，再委托给自己的 `structured_output`。

实现（`AI_PROVIDER` 环境变量选择，`app/ai/factory.py:get_provider` 是 `lru_cache` 单例）：

- `providers/mock.py` —— 确定性假模型，供单测与离线评测。
- `providers/openai_compatible.py` —— OpenAI / DeepSeek / 智谱 / 通义 等。
- `providers/anthropic.py`。

### 5.2 输出强制结构化

- `VisionOutput`：`usage_scene` / `usage_frequency` / `size_class` / `fragility` / `category` /
  `subcategory` / `notes`（`category` 允许为空串）。**没有 `confidence`、没有 `attributes`。**
- `RankingOutput`：`candidates: list[CandidateSlot]`，`min_length=1, max_length=3`（见 `docs/DOMAIN.md` §5）。
- 校验失败 → `AIOutputParseError` → 重试。

> ⚠️ **不要给这些 schema 加 `strict=True`。** 它们校验的是 LLM 吐出的 JSON 文本，
> `slot_id` / `evidence_item_ids` 在 JSON 里是字符串，strict 模式要求真正的 `UUID` 实例、
> 会拒掉它们，表现为一句极具误导性的「missing required fields」。这条坑真实地把
> Rank 步骤整段打死过（Agent 永远 `state=failed`）。规则是 **`extra="forbid"` 即可，不要 strict**。
> 原稿 §10 把 `strict` 写成规范，是错的，已删。

### 5.3 Prompt 版本化

- 模板在 **`app/agent/prompts/`（单数）**，一模板一 `.md`，文件名带版本号。
- 走 `str.format_map`，**不是 Jinja2** —— 字面花括号要写 `{{` `}}`。
- 当前在线版本：`vision.v2.md` / `infer.v1.md` / `search.v4.md` / `recommend.v2.md` / `answer.v1.md`。
  旧版本保留不删（append-only），便于对照。
- 任何 prompt 修改在 PR 中附 golden case 评估（`python -m app.evaluation`）。

---

## 6. 存储与上传

### 6.1 可切换存储后端（`STORAGE_BACKEND=local|minio`）✅

沙箱 / 本地 demo 没有 MinIO，所以存储被抽成 `app/storage/backend.py` 的 `StorageBackend`
Protocol，两个实现：

| | `MinioBackend` | `LocalBackend` |
| --- | --- | --- |
| 落点 | MinIO（bucket `home-organizer-uploads`） | `STORAGE_LOCAL_DIR`（默认 `./var/storage`） |
| 浏览器上传 | `POST /uploads/presign` → **直传** PUT | `POST /assets/upload` → multipart 经 API |
| 读取 URL | MinIO 预签名 GET，TTL 1h | `/api/v1/files/{key}?exp=…&sig=…`（HMAC 签名，无需 Bearer） |
| presign 接口 | 正常 | **409**（本地后端没有可直传的 URL） |

- 默认是 `minio`，所以不配 `.env` 时环境行为不变。
- `GET /api/v1/files/*` 在 MinIO 模式下**返回 404**（避免被当作跳板）。
- `_resolve()` 做路径穿越防御（拒绝绝对路径与 `..`）。
- ⚠️ 测试注意：`Settings` 读 `services/api/.env`，`.env` 一改成 `local` 就会污染整个测试套件，
  所以两个 conftest 都显式 pin 回 `minio`。

### 6.2 Bucket 规划

- `home-organizer-items`：物品图片
- `home-organizer-uploads`：上传对象（预签名流程用这个）

### 6.3 Redis 用途

- 📋 空间快照缓存（TTL 5 min，写时 invalidate）—— **未实现**
- 📋 限流（IP / 用户）—— **未实现**
- 📋 短任务队列 —— **未实现**

目前 Redis 只在 `/health` 探活里被连过一下，业务路径完全没用它。原稿把这三条写成现状，
会让人以为推荐已经走了缓存 —— 实际上**连点两次上传会实打实调两次 LLM**。

---

## 7. 认证与权限

- **Token = 你是谁**：`Authorization: Bearer <jwt>`，HS256，`JWT_SECRET` 签名，access TTL 3600s。
- **`X-Home-Id` = 你在哪个家操作**：这是**选择器，不是凭证**，每个请求都重新对着
  `HomeMembership` 校验一次。非成员 → **404（不是 403）**，避免泄漏「这个 home 存在」。
- 请求级依赖是 `app/api/deps.py:get_actor` → 返回 `Actor(user_id, home_id)`；
  约 41 个路由 `Depends(get_actor)`，**没有 header 后门**。
- 注册（`signup`）会自动 provision 一个 `我的家` + OWNER 成员关系（否则新账号无处可去）。
- API Key 永不出现在前端；`AI_API_KEY` 只在 API 进程环境变量里。
- ⏳ **已知尾巴**：web 从不调 `/auth/refresh`，所以 access 过期即静默登出（1 小时）。

---

## 8. 可观测性

### 8.1 日志 ✅

- 结构化 JSON 日志（`app/core/logging.py`），每条带 `request_id` / `user_id` / `home_id`（如有）。
- PII 与密钥通过 `app/ai/observability.py` 的 `redact_api_keys` / `scrub_pii` /
  `scrub_image_url` / `safe_log_payload` 过滤。

### 8.2 `AgentTrace` ✅

- 每次推荐、每次 NL 搜索、每次 vision 调用都写一行 `AgentTrace`（见 `docs/DOMAIN.md` §2.16）。
- `steps` 是 `AgentStepResult.to_json()` 的数组，形状 `{state, started_at, ended_at, payload, error}`。
- ⏳ **但 payload 目前只记数量与标识**（候选数、选中的 slot_id 等），**没有**落
  `score_breakdown` / `prompt_hash` / token 数。`llm_tokens_in` / `llm_tokens_out` /
  `llm_cost_usd` 三列存在但基本为 NULL。所以 `docs/AI.md` §11 描述的那套可观测性是**设计**，
  不是现状。

### 8.3 指标 📋

- Prometheus + Grafana，指标口径见 `docs/EVALUATION.md`。**未实现。**

---

## 9. 错误处理约定

- **API 层**：领域异常 → `app/core/exceptions.py` 的处理器 → 结构化 `ErrorResponse`
  （`{error, message, details?}`）。未捕获异常 → 统一 500。校验失败是 **422**（FastAPI 默认），
  不是 400。
- **AI 层**：`AIProviderError` 家族（`app/ai/errors.py`）。区分两类 ——
  模型**返回垃圾**（`AIOutputParseError`，可重试）vs 模型**不可达**（transport / auth / quota /
  timeout，不该当成「再问一次就好了」）。解析失败重试 ≤ 2 次；耗尽后按调用点不同落
  `verifier_failed` 或 `error`。
- **存储层**：统一 `StorageError`；上传失败返回明确错误码，Web 提示重试。
- **DB 层**：唯一约束冲突 → 409 + 字段名。

---

## 10. 安全

- 密码 bcrypt；JWT 短期 access + refresh。
- 所有 API 走 HTTPS（生产由 Nginx 终结 TLS；本地 dev 是 http）。
- 图片访问走签名 URL（MinIO 预签名 / 本地后端 HMAC），TTL 1h。
- CORS 白名单。
- CSRF：Web 用 bearer token（cookie 存 token），无 CSRF token。
- **输入校验：所有外部 schema 开 `extra="forbid"`；`strict` 一律不开**（理由见 §5.2）。
- SQL 注入：100% SQLAlchemy ORM / 参数化查询，不拼接原始 SQL。
- 对象 key 访问控制：key 一律以 `home/<home_uuid>/` 开头，`POST /items` 校验前缀，否则 400
  （因为 `_item_views` 会为任何 key 签 GET URL）。
- 📋 速率限制（登录 / 推荐 / 注册）—— **未实现**，依赖 Redis，见 §6.3。

---

## 11. 部署视图

详见 `docs/DEPLOYMENT.md`。高层服务清单（对照 `docker-compose.yml` 的实际内容）：

| 服务 | 镜像 | 端口 | compose 里？ | 备注 |
| --- | --- | --- | --- | --- |
| api | 自建（FastAPI + uvicorn） | 8000 | ✅ | `./services/api` 被 bind mount 到 `/app`（dev 热重载） |
| postgres | postgres:16 | 5432 | ✅ | |
| redis | redis:7 | 6379 | ✅ | 业务未使用 |
| minio | minio/minio | 9000, 9001 | ✅ | 9001 控制台 |
| minio-init | minio/mc | — | ✅ | 建 bucket 的一次性 job |
| web | 自建（Next.js standalone） | 3000 | ❌ | **没有 Dockerfile**，本地直接 `next dev` |
| nginx | nginx:alpine | 80, 443 | ❌ | 配置在 `infra/nginx/default.conf`，未接进 compose |

---

## 12. 演进路线

原稿这里的「Phase 1…Phase 6」用的是**规划期编号**，与项目历史里的 Phase 1~13
（`AGENTS.md` §5）不是一个体系，两边都叫 "Phase 2 / 3" 却指完全不同的东西 —— 这是重复
Phase 编号的根源。**统一口径见 `docs/DEVELOPMENT_PLAN.md` §0**，后续一律用项目历史编号。

下一批工作不是推倒重来，而是补 P0 断点（详见 `docs/DEVELOPMENT_PLAN.md` 下篇）：

| | 内容 | 状态 |
| --- | --- | --- |
| P0.0 | 设计文档与代码对齐 | ✅ 已交付（2026-09-21） |
| P0.1 | 真实账号：JWT + Home 选择器 + Web 登录 | ✅ 已交付 |
| P0.2 | 拍照即建模（空间结构由 LLM 提议 + 用户确认） | ✅ 已交付（2026-09-22） |
| P0.3 | 反向录入（已有物品直接落位，不经 LLM） | ✅ 已交付（2026-09-22） |
| P0.4 | 闭环 + 讲理由（反馈回灌偏好；推荐给出人话理由） | ⏳ |

更远的（多轮对话深化、pgvector 图像相似度、微信小程序、多人协作权限细分、计费多租户）
仍然是演进方向，但**没有排期**。
