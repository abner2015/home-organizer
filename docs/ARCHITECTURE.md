# ARCHITECTURE — 系统架构

> 描述目标系统的整体架构、组件职责、数据流、部署视图。

---

## 1. 设计目标

1. **业务代码与 AI Provider 解耦**：业务层不依赖任何具体模型供应商。
2. **可观测**：所有 AI 调用都有 trace；所有 API 都有结构化日志。
3. **可移植**：单条 `docker compose up` 启动完整系统。
4. **可演进**：第二版新增多轮对话、pgvector 检索时不需要重写核心流程。
5. **不引入不必要的复杂基础设施**：第一版只用 PostgreSQL + Redis + MinIO，不引入 Milvus / Elasticsearch / Neo4j。

---

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                         Client (Web)                          │
│                  Next.js + TypeScript + Tailwind              │
└──────────────────────────────────────────────────────────────┘
                              │ HTTPS
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                       Nginx (Reverse Proxy)                  │
│   /api/*  → api:8000     /  → web:3000     /minio/*  → minio │
└──────────────────────────────────────────────────────────────┘
        │                    │                       │
        ▼                    ▼                       ▼
┌──────────────┐    ┌──────────────────┐    ┌──────────────┐
│ apps/api     │    │   apps/web       │    │   MinIO      │
│ FastAPI      │    │   Next.js        │    │   Object     │
│ (Python)     │    │                  │    │   Storage    │
└──────────────┘    └──────────────────┘    └──────────────┘
        │
        ├──────────► PostgreSQL (state)
        ├──────────► Redis      (cache, rate limit, short-lived jobs)
        └──────────► AIProvider ──► external multimodal LLM API
```

---

## 3. 模块与职责

### 3.1 apps/web（Next.js）

- 用户界面：注册、登录、家庭空间树形管理、物品上传、推荐展示、物品搜索。
- 调用后端 API 完成所有业务逻辑；**不直接调用 AI Provider**。
- 上传图片：先向后端请求预签名 URL，再直传 MinIO（见 §6.1）。
- 路由：
  - `/` 首页 / 家庭空间概览
  - `/login`, `/signup`
  - `/homes/:homeId/space` 空间树
  - `/homes/:homeId/items` 物品列表
  - `/homes/:homeId/items/new` 录入新物品（核心流程）
  - `/homes/:homeId/items/:id` 物品详情 / 历史
  - `/homes/:homeId/rules` 规则管理
  - `/homes/:homeId/search` 搜索

### 3.2 apps/api（FastAPI）

按"分层 + 模块化"组织：

```
apps/api/
├── app/
│   ├── main.py                       # FastAPI 入口
│   ├── config.py                     # pydantic-settings
│   ├── deps.py                       # 公共依赖（DB、auth、当前用户）
│   ├── api/                          # 路由层
│   │   ├── auth.py
│   │   ├── homes.py
│   │   ├── rooms.py
│   │   ├── storage.py                # Unit / Section / Slot
│   │   ├── items.py
│   │   ├── placements.py
│   │   ├── recommendations.py
│   │   ├── preferences.py
│   │   ├── rules.py
│   │   └── uploads.py                # 预签名 URL
│   ├── schemas/                      # Pydantic 模型（请求/响应）
│   │   └── ...
│   ├── models/                       # SQLAlchemy ORM 模型
│   │   └── ...
│   ├── services/                     # 业务逻辑层
│   │   ├── home_service.py
│   │   ├── storage_service.py
│   │   ├── item_service.py
│   │   ├── placement_service.py
│   │   ├── recommendation_service.py # 协调 Agent
│   │   ├── preference_service.py
│   │   ├── rule_service.py
│   │   └── upload_service.py
│   ├── agent/                        # AI Agent
│   │   ├── orchestrator.py           # Think → Act → Observe → Verify → Retry → Answer
│   │   ├── verifier.py
│   │   ├── tools.py                  # 工具：查 Slot、查历史、查规则
│   │   └── prompts/                  # 模板（可热加载）
│   ├── ai/                           # AI Provider 抽象与实现
│   │   ├── provider.py               # AIProvider Protocol
│   │   ├── providers/
│   │   │   ├── openai_compatible.py
│   │   │   ├── anthropic.py
│   │   │   └── ...
│   │   └── schemas.py                # VisionOutput / RecommendationOutput
│   ├── storage/                      # MinIO 客户端
│   │   └── minio_client.py
│   ├── cache/                        # Redis 封装
│   │   └── redis_client.py
│   ├── db/                           # SQLAlchemy session、迁移
│   │   ├── base.py
│   │   ├── session.py
│   │   └── migrations/               # Alembic
│   ├── observability/                # 日志、trace、metric
│   │   ├── logging.py
│   │   └── tracing.py
│   └── errors.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── agent/
├── pyproject.toml                    # uv / poetry
├── alembic.ini
└── Dockerfile
```

### 3.3 packages/ai（独立 Python 包，可选）

如果 `apps/api` 内部对 AI 层封装不足以满足复用，第二版可抽出独立包。第一版先放在 `apps/api/app/ai/`。

---

## 4. 关键数据流

### 4.1 录入新物品 → 推荐 Slot

```
[Web] 上传图片
   │
   │ ① 请求 /api/uploads/presign  (POST, file_name, content_type)
   ▼
[API] 校验用户/家庭 → 生成 MinIO 预签名 PUT URL → 返回
   │
   │ ② 浏览器直传 MinIO
   ▼
[MinIO] 接收 object
   │
   │ ③ 提交 /api/recommendations  (POST, item_meta, object_key, description?)
   ▼
[API RecommendationService.create]
   │
   │  3.1 加载家庭空间（Home → ... → Slot）→ 缓存到 Redis
   │  3.2 加载 active HomeRule
   │  3.3 加载 UserPreference
   │  3.4 加载历史 ItemPlacement（按类别）
   │  3.5 启动 Agent Orchestrator
   ▼
[Agent Orchestrator — 9 步 pipeline]
   │
   │  Step 1  VISION              (LLM #1)         → VisionOutput
   │  Step 2  STRUCTURED_ITEM     parse Vision
   │  Step 3  STORAGE_RETRIEVAL   DB query
   │  Step 4  CANDIDATE_GENERATION 确定性代码        → ≤ 20 ScoredSlot
   │  Step 5  CONSTRAINT_FILTERING 确定性代码 (hard) → valid candidates
   │  Step 6  RANKING             确定性代码         → top 20 排序
   │  Step 7  LLM_DECISION        (LLM #2)         → Top 1~3 + reason
   │  Step 8  VERIFY              确定性代码 (安全网) → 校验白名单/硬规则/存在性
   │  Step 9  PERSIST             DB write
   │          └ Verify fail → Retry Step 7 (≤ 2)
   ▼
[API] 落 Recommendation（含 agent_trace_id），返回 1~3 个候选
   │
   │ ④ 浏览器展示候选（含 reason、置信度、matched_rules）
   ▼
[Web] 用户选择/调整
   │
   │ ⑤ 提交 /api/placements  (POST, item_id, slot_id, recommendation_id?)
   ▼
[API] 创建 ItemPlacement；若带 recommendation_id 则回填 chosen_slot_id、status
```

### 4.2 搜索物品

```
[Web] → GET /api/items?q=...
[API] SELECT FROM items WHERE name ILIKE %q% OR category ILIKE %q%
     LEFT JOIN item_placements active
[API] 返回 [{item, current_slot}]
```

第一版不做图像相似度搜索（pgvector 第二版再考虑）。

---

## 5. AI 层架构

### 5.1 AIProvider Protocol

```python
class AIProvider(Protocol):
    name: str

    async def vision_recognize(
        self,
        image_url: str,
        *,
        hint: str | None = None,
    ) -> VisionOutput: ...

    async def rank_candidates(
        self,
        *,
        item: ItemContext,
        candidates: list[ScoredSlot],   # ≤ 20，已过 Candidate Generation + Constraint Filtering
        rules: list[RuleContext],       # hard 规则仅供 prompt 提示
        preferences: list[PreferenceContext],
        history: list[PlacementContext],
        last_failure: str | None = None,
    ) -> RankingOutput: ...
```

业务代码只通过 `AIProvider` 协议编程。具体供应商实现：

- `providers/openai_compatible.py`（OpenAI / DeepSeek / 智谱 / 通义 等兼容 OpenAI 协议的）
- `providers/anthropic.py`

选哪个由 `AI_PROVIDER` 环境变量在启动时注入。LLM 在整个推荐 pipeline 中**只调用 ≤ 2 次**（Vision + Rank），候选生成、约束过滤、Verifier 全部是确定性代码（详见 `docs/AGENT.md` §1-§9）。

### 5.2 输出强制结构化

- Vision 输出 `VisionOutput` Pydantic schema：`name`, `category`, `subcategory`, `brand`, `estimated_size`, `is_sensitive`, `needs_lock`, `attributes`, `raw_description`。
- Recommendation 输出 `RecommendationOutput`：`candidates: list[CandidateSlot]`（见 `docs/DOMAIN.md` §5）。
- LLM 通过 JSON mode / function calling / tool use 强制 JSON；解析失败 → 重试。

### 5.3 Prompt 版本化

- Prompt 模板放在 `app/agent/prompts/`，每个模板一个 `.md` 文件，文件名带版本（如 `vision.v3.md`）。
- 任何 prompt 修改在 PR 中附 golden case 评估。

---

## 6. 存储与上传

### 6.1 MinIO 预签名上传

- Web 端上传前先调 `POST /api/uploads/presign`，API 生成 MinIO 预签名 URL。
- 浏览器拿到 URL 后 PUT 到 MinIO（不经过 API 服务，节省带宽）。
- 完成后 Web 端将 `object_key` 提交给 `POST /api/recommendations`。

### 6.2 Bucket 规划

- `home-organizer-items`：物品图片
- `home-organizer-uploads`：上传中临时对象（生命周期 1 天自动删除）

### 6.3 Redis 用途

- 家庭空间快照缓存（TTL 5 min，写操作时 invalidate）
- 限流（IP / 用户）
- 短任务队列（第二版；第一版同步调用即可）

---

## 7. 认证与权限

- 第一版：邮箱 + 密码，JWT (access + refresh)。
- API 解析 JWT → 注入 `current_user` 到请求。
- 每个需要授权的接口校验"用户对该 home 有 HomeMembership"。
- API Key 永不出现在前端；`AI_API_KEY` 仅在 API 进程环境变量中。

---

## 8. 可观测性

### 8.1 日志

- 结构化 JSON 日志（`loguru` 或 `structlog`）。
- 每条日志含 `request_id`、`user_id`、`home_id`（如有）。

### 8.2 Trace

- 每次推荐写入 `AgentTrace`（见 `docs/DOMAIN.md` §2.16）。
- LLM 调用的 token / cost / 耗时记录在 trace 中。
- 错误 trace 含 `error` 字段。

### 8.3 指标（第二版）

- Prometheus + Grafana。
- 关键指标：见 `docs/EVALUATION.md`。

---

## 9. 错误处理约定

- API 层：所有未捕获异常 → 统一 500 + 错误码；4xx 错误返回结构化 `ErrorResponse`。
- AI 层：LLM 返回非法 JSON → 抛 `AIOutputParseError`，由 Agent 触发 Retry；超 2 次 → 标记 `verifier_failed`，向用户返回友好错误。
- 存储层：MinIO 上传失败 → 上传服务返回明确错误码，Web 端提示重试。
- DB 层：唯一约束冲突 → 409 + 字段名。

---

## 10. 安全

- 密码 bcrypt（cost ≥ 12）。
- JWT 短期 access + 长期 refresh。
- 所有 API 走 HTTPS（Nginx 终结 TLS）。
- 图片访问走 MinIO 预签名 URL，TTL ≤ 15 min。
- CORS 白名单。
- CSRF：第一版 Web 用 bearer token，无需 CSRF。
- 输入校验：所有 Pydantic schema 开启 `extra="forbid"`、`strict`。
- SQL 注入：100% SQLAlchemy ORM / 参数化查询，不拼接原始 SQL。
- 速率限制：登录、推荐、注册等敏感接口 Redis 限流。

---

## 11. 部署视图

详见 `docs/DEPLOYMENT.md`。本节仅列高层服务：

| 服务 | 镜像 | 端口 | 备注 |
| --- | --- | --- | --- |
| web | 自建（Next.js standalone） | 3000 | |
| api | 自建（FastAPI + uvicorn） | 8000 | |
| postgres | postgres:16 | 5432 | |
| redis | redis:7 | 6379 | |
| minio | minio/minio | 9000, 9001 | 9001 控制台 |
| nginx | nginx:alpine | 80, 443 | 反向代理 |

---

## 12. 演进路线

| 阶段 | 关键变化 |
| --- | --- |
| Phase 1 | Web + API + DB + AI 基础 + 推荐端到端 |
| Phase 2 | 多轮对话（Conversation / Message / AgentTrace 增强） |
| Phase 3 | 图像相似度（pgvector） |
| Phase 4 | 微信小程序（API 复用，UI 重写） |
| Phase 5 | 多人协作 / 权限细分 |
| Phase 6 | 计费 / 套餐 / 多租户 |
