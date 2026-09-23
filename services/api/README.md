# Home Organizer — Backend API

FastAPI 后端。规划入口见仓库根 `AGENTS.md`，设计文档见 `docs/`。

## Stack

- Python 3.11+（Dockerfile 用 3.12）
- FastAPI + Uvicorn
- Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x (async) + Alembic
- PostgreSQL (asyncpg + psycopg)、SQLite（测试）
- Redis (redis-py asyncio) —— 目前只有健康检查用到
- MinIO / 本地文件系统（可切换存储后端，详见「存储后端」）
- structlog（JSON in prod, colored in dev）
- pytest + pytest-asyncio + ruff + mypy

## 端点一览

所有路由挂在 `/api/v1` 下（`/health` 在根）。详细请求/响应见 `docs/API.md`，
OpenAPI 在 `http://localhost:8000/docs`。

| 路由器 | 内容 |
| --- | --- |
| `auth.py` | `POST /auth/signup`、`/auth/login`、`/auth/refresh`、`GET /auth/me` |
| `homes.py` | `GET /homes`、`POST /homes`（P0.9）、`/homes/{id}`、`/homes/{id}/rooms`、`/homes/{id}/space-tree`、`/homes/{id}/slots`、`PATCH /homes/{id}`、`/rooms/{id}/storage-units`，以及成员管理 4 个路由（`GET/POST/PATCH/DELETE /homes/{id}/members[/{user_id}]`，P0.8） |
| `items.py` | 物品 CRUD（`POST/GET/PATCH /items[/{id}]`、`/items/{id}/placements`、`/items/{id}/candidates`、`/items/{id}/vision`、`/items/{id}/infer`、`/items/recognize`） |
| `recommendations.py` | 推荐：`POST /recommendations/items/{id}/recommend`、`GET /recommendations/{id}`、`POST /recommendations/{id}/accept`、`/reject`（带 note）、`/revoke`（撤销排除，P0.6）、`PATCH /recommendations/{id}` |
| `placements.py` | 直接摆放（不经 LLM）：`POST /placements`、`PATCH /placements/{id}`（改备注 / 换位置，P0.7）、`DELETE /placements/{id}`（软关闭） |
| `structure.py` | 收纳结构 CRUD + 「结构提议」AI：`POST /structures/propose` + 7 个 CRUD 路由 |
| `search.py` | 收纳助手：`POST /search`（带 `conversation_id` 多轮记忆） |
| `assets.py` / `uploads.py` / `files.py` | 上传（presign / 直传 / 直读） |

## Directory Layout

```
services/api/
├── app/
│   ├── main.py
│   ├── core/            # config / logging / exceptions / request_id / health
│   ├── api/
│   │   ├── deps.py      # get_actor：JWT + X-Home-Id（home 选择器每次都重验）
│   │   └── v1/          # 10 个路由器，见「端点一览」
│   ├── schemas/         # API 请求/响应模型
│   ├── models/          # SQLAlchemy ORM（asset / conversation / home / item /
│   │                    #   placement / preference / recommendation / room /
│   │                    #   rule / storage / trace / user）
│   ├── services/        # 业务逻辑（auth / asset / conversation / membership /
│   │                    #   recommendation / search / security / structure /
│   │                    #   vision / item_inference / image_payload）
│   ├── agents/          # 推荐 pipeline（pipeline / ranking / prompt_render /
│   │                    #   placement_service / reason）+ NL 搜索编排
│   │                    #   （agent / intent / answer / context / location /
│   │                    #   structure / history）
│   ├── verification/    # Verifier 检查（context / rule_engine / checks / verifier）
│   ├── tools/           # Agent 工具：ToolRegistry 13 个 + 不进 registry 的原语
│   │                    #   （write_tools._create_placement、recommendation_tools）
│   ├── ai/              # AIProvider Protocol + 三个实现
│   ├── storage/         # StorageBackend（minio | local）+ 错误
│   ├── cache/           # Redis 客户端
│   ├── db/              # base / session / enums / types / seed
│   ├── evaluation/      # 离线评测 harness（python -m app.evaluation）
│   ├── agent/prompts/   # ⚠️ 单数 agent —— prompt 模板都在这里
│   ├── domain/          # 空包（占位）
│   └── repositories/    # 空包（占位）
├── alembic/versions/    # 0001_initial_schema / 0002_assets /
│                        #   0003_update_recommendation_status /
│                        #   0004_recommendation_revoked
├── evaluation/dataset/  # 评测用例 + reports/
├── tests/{unit,api,db}/
├── var/storage/         # STORAGE_BACKEND=local 时的图片落点（git 忽略）
├── pyproject.toml
├── alembic.ini
├── Dockerfile
└── README.md
```

> 模块实现全部在 `services/api/`（不是 `apps/api/`）；`docs/ARCHITECTURE.md` §3.2 与本表一致。

## Setup

```bash
# 1. venv + 依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. 配置（Settings 读的就是这个文件）
cp .env.example .env
# 至少设置 JWT_SECRET、AI_API_KEY；没有 MinIO 时加 STORAGE_BACKEND=local

# 3. 迁移 + 起服务
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. （可选）seed —— 4 房间 / 4 柜 / 20 格 / 11 物品
python -m app.db.seed

# 5. 验证
curl http://localhost:8000/health
# OpenAPI: http://localhost:8000/docs
```

### 环境变量

完整列表见 `.env.example`，分组如下：

- **应用**：`APP_ENV`、`LOG_LEVEL`、`DEBUG`、`HOST`、`PORT`、`CORS_ORIGINS`
- **数据库**：`DATABASE_URL`（async，asyncpg）、`DATABASE_URL_SYNC`（Alembic 用，psycopg）、
  `DATABASE_POOL_SIZE`、`DATABASE_MAX_OVERFLOW`
- **Redis**：`REDIS_URL`、`REDIS_MAX_CONNECTIONS`
- **JWT**：`JWT_SECRET`（**生产必须改**，默认占位会在启动时 warning）、
  `JWT_ALGORITHM`、`JWT_ACCESS_TTL`、`JWT_REFRESH_TTL`
- **AI**：`AI_PROVIDER`（`mock` / `openai_compatible` / `anthropic`）、`AI_API_KEY`、
  `AI_BASE_URL`、`AI_MODEL_VISION`、`AI_MODEL_RECOMMEND`、`AI_TIMEOUT_S`
- **存储**：`STORAGE_BACKEND`（`minio` 或 `local`）、`STORAGE_LOCAL_DIR`、
  `API_PUBLIC_BASE_URL`，以及 MinIO 的 `MINIO_*`

### 认证

受保护路由用两段 header：

```
Authorization: Bearer <access_token>   # 你是谁（JWT，HS256）
X-Home-Id: <home_uuid>                # 你在哪个家操作（每次都重验 membership）
```

`get_actor`（`app/api/deps.py`）先校验 JWT，再校验 `X-Home-Id` 是否属于这个 token 的用户——
**非该 home 成员 → 404 not 403**（不泄漏其它 home 的存在性，仓库惯例）。
登录拿 token：

```bash
curl -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"..."}'
```

### 存储后端

`STORAGE_BACKEND` 控制图片落点：

- **`minio`**（默认）：上传走 `POST /uploads/presign` → 浏览器 PUT 到 MinIO。
- **`local`**：写到 `STORAGE_LOCAL_DIR`（默认 `./var/storage`），通过 `GET /files/{key}`
  直读（带 HMAC 签名，`STORAGE_LOCAL_SECRET` 不写则 fallback 到 `JWT_SECRET`）。
  `POST /uploads/presign` 在 local 模式下回 **409**（无对象存储可签），
  客户端改走 `POST /api/v1/assets/upload` 直传。

## Lint / Type-check / Test

```bash
python -m ruff check app/ tests/             # 基线 32
python -m mypy app/                          # 基线 20
python -m pytest tests/ --no-header -q       # 基线 752 passed / 1 skipped
```

跑特定子集：

```bash
pytest tests/unit/                           # 单元（不经过 HTTP）
pytest tests/api/test_home_members_api.py    # 单文件
pytest -k placement                          # 按名字筛
```

真模型 smoke（默认跳过）：

```bash
RUN_REAL_AI_TESTS=1 pytest tests/api/test_recognition_real_api.py
```

离线评测：

```bash
python -m app.evaluation                       # Mock AI（基线 61/61 passed）
EVAL_USE_REAL_AI=1 python -m app.evaluation --use-real-ai
# 报告 → evaluation/reports/{report.json,report.csv,report.md}
```

## Docker

```bash
docker build -t home-organizer-api .
docker run --rm -p 8000:8000 --env-file .env home-organizer-api
```

完整后端（postgres / redis / minio / api）用仓库根的 `docker compose up -d`；
Web 与 Nginx 未容器化，见 `docs/DEPLOYMENT.md`。

## 已知注意事项

- `.env` 会被 `Settings` 读取，因此**会漏进测试套件**；`tests/api/conftest.py` 与
  `tests/unit/conftest.py` 都把存储后端 pin 回 `minio`，新增开关需同样处理。
- 端口 8000 上的旧 uvicorn 子进程不会随父 bash 一起被杀掉，重启前先确认端口已释放。
- AI 输出的 Pydantic schema 一律 `extra="forbid"`、**不要 `strict=True`**（会拒掉 JSON 里的
  UUID 字符串，表现为误导性的 "missing required fields"）。
- 路由新增后需要重启 uvicorn —— 子进程不热加载新 router（实例化在 import 时）。
- 成员管理（`/homes/{id}/members`）是本项目里**唯一允许 403** 在 home 内部出现的地方
  （非 owner 调管理类）；其余成员错误一律 404 not 403。
