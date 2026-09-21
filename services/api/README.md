# Home Organizer — Backend API

FastAPI 后端。规划入口见仓库根 `AGENTS.md`，设计文档见 `docs/`。

## Stack

- Python 3.11+（Dockerfile 用 3.12）
- FastAPI + Uvicorn
- Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x (async) + Alembic
- PostgreSQL (asyncpg + psycopg)、SQLite（测试）
- Redis (redis-py asyncio) —— 目前只有健康检查用到
- MinIO / 本地文件系统（可切换存储后端）
- structlog（JSON in prod, colored in dev）
- pytest + pytest-asyncio + ruff + mypy

## Directory Layout

```
services/api/
├── app/
│   ├── main.py
│   ├── core/            # config, logging, exceptions, request_id, health
│   ├── api/
│   │   ├── deps.py      # get_actor：JWT + X-Home-Id
│   │   └── v1/          # auth / homes / items / recommendations / search /
│   │                    #   assets / uploads / files
│   ├── schemas/         # API 请求/响应模型
│   ├── models/          # SQLAlchemy ORM
│   ├── services/        # 业务逻辑
│   ├── agents/          # 推荐 pipeline + NL 搜索编排
│   ├── verification/    # Verifier 检查
│   ├── tools/           # Agent 工具（13 个）
│   ├── ai/              # AIProvider Protocol + 三个实现
│   ├── storage/         # StorageBackend（minio | local）
│   ├── cache/           # Redis 客户端
│   ├── db/              # base, session, enums, types, seed
│   ├── evaluation/      # 离线评测 harness（python -m app.evaluation）
│   ├── agent/prompts/   # ⚠️ 单数 agent —— prompt 模板都在这里
│   ├── domain/          # 空包（占位）
│   └── repositories/    # 空包（占位）
├── alembic/versions/    # 0001 / 0002 / 0003
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
cd services/api && cp .env.example .env
# 至少设置 JWT_SECRET、AI_API_KEY；没有 MinIO 时加 STORAGE_BACKEND=local

# 3. 迁移 + 起服务
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. （可选）seed
python -m app.db.seed

# 5. 验证
curl http://localhost:8000/health
# OpenAPI: http://localhost:8000/docs
```

## Lint / Type-check / Test

```bash
python -m ruff check app/ tests/     # 基线 32
python -m mypy app/                  # 基线 20
python -m pytest tests/ --no-header -q   # 基线 534 passed / 1 skipped
```

离线评测：

```bash
python -m app.evaluation                       # Mock AI
python -m app.evaluation --use-real-ai         # 需同时设 EVAL_USE_REAL_AI=1
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
