# DEPLOYMENT — 部署

> 本文档分两部分读：**§1–§8 是现状**（照 `docker-compose.yml` / `Dockerfile` / `infra/` 的真实内容写），
> **§9–§10 是生产目标设计**。
>
> 状态图例：✅ 已落地 · ⏳ 已排期未实现 · 📋 设计稿
>
> 最后更新：2026-09-21 —— 原稿把 `web` 与 `nginx` 写成 compose 的既有服务、路径写成 `apps/api/`，
> 都与仓库实际不符，已订正。

---

## 1. 现状总览

**`docker compose up` 起来的是后端全套，不含 Web 与 Nginx。**

| 服务 | 镜像来源 | 容器端口 | 宿主机 | compose 里？ |
| --- | --- | --- | --- | --- |
| postgres | postgres:16 | 5432 | 5432 | ✅ |
| redis | redis:7 | 6379 | 6379 | ✅ |
| minio | minio/minio:latest | 9000 / 9001 | 9000 / 9001 | ✅ |
| minio-init | minio/mc:latest | — | — | ✅（一次性建 bucket） |
| api | `./services/api` 的 Dockerfile | 8000 | 8000 | ✅ |
| web | 📋 计划自建（Next.js standalone） | 3000 | 3000 | ❌ **无 Dockerfile** |
| nginx | nginx:1.27-alpine | 80 / 443 | 80 / 443 | ❌ **配置已写，未接进 compose** |

> ⚠️ 沙箱 / 本仓库的开发环境里 **没有 docker**。日常开发走的是 §7 的「裸进程」路径，
> 不是 compose。compose 是给有 Docker 的机器准备的一键后端。

---

## 2. 目录布局

```
home-organizer/
├── docker-compose.yml            # postgres / redis / minio / minio-init / api
├── .env.example
├── .env                          # 本地真实配置，git 忽略
├── AGENTS.md                     # 规划入口
├── infra/
│   ├── nginx/default.conf        # ⚠️ 只有 API + MinIO，没有 web upstream
│   ├── minio/README.md           # bucket 初始化说明
│   └── postgres/initdb/          # 首次启动执行的 SQL
├── services/
│   └── api/                      # 后端（注意是 services/，不是 apps/）
│       ├── Dockerfile
│       ├── alembic/versions/     # 0001 / 0002 / 0003
│       ├── evaluation/dataset/   # 评测用例
│       ├── var/storage/          # STORAGE_BACKEND=local 时的图片落点（git 忽略）
│       └── app/
└── apps/
    └── web/                      # 📋 只有前端源码，没有 Dockerfile
```

---

## 3. docker-compose.yml（实际内容）

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./infra/postgres/initdb:/docker-entrypoint-initdb.d:ro
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7
    command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
    volumes: ["redisdata:/data"]
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes: ["miniodata:/data"]
    ports: ["9000:9000", "9001:9001"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 3s
      retries: 10

  minio-init:
    image: minio/mc:latest
    depends_on:
      minio: { condition: service_healthy }
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD};
      mc mb -p local/home-organizer-items || true;
      mc mb -p local/home-organizer-uploads || true;
      mc anonymous set none local/home-organizer-items;
      mc anonymous set none local/home-organizer-uploads;
      echo 'MinIO buckets initialised.';
      "

  api:
    build: { context: ./services/api }
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
      minio:    { condition: service_healthy }
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
    volumes: ["./services/api:/app"]     # dev 热重载：宿主机代码直接挂进容器
    ports: ["8000:8000"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 3s
      retries: 10

volumes:
  pgdata:
  redisdata:
  miniodata:
```

**与「设计稿」的差异**：没有 `web`、没有 `nginx`。要补齐，需要先写 `apps/web/Dockerfile`
（并在 `next.config.mjs` 打开 `output: "standalone"`）。

---

## 4. 环境变量

`.env.example` 的实际内容（**注意 `DATABASE_URL` 用的是 `asyncpg`，另有 `DATABASE_URL_SYNC` 给 Alembic**）：

```bash
# ----- App -----
APP_ENV=development
LOG_LEVEL=INFO
DEBUG=false

# ----- Auth -----
JWT_SECRET=change-me-in-production-min-32-chars
JWT_ALGORITHM=HS256
JWT_ACCESS_TTL=3600
JWT_REFRESH_TTL=2592000

# ----- Postgres -----
POSTGRES_USER=homeorg
POSTGRES_PASSWORD=homeorg
POSTGRES_DB=homeorg
DATABASE_URL=postgresql+asyncpg://homeorg:homeorg@postgres:5432/homeorg
DATABASE_URL_SYNC=postgresql+psycopg://homeorg:homeorg@postgres:5432/homeorg

# ----- Redis -----
REDIS_URL=redis://redis:6379/0

# ----- MinIO / S3 -----
# 容器内 API 访问 minio:9000；浏览器访问 localhost:9000，预签名 URL 会按
# MINIO_PUBLIC_ENDPOINT 重写主机名（留空则回落为 MINIO_ENDPOINT）。
MINIO_ROOT_USER=homeorg
MINIO_ROOT_PASSWORD=homeorg-minio
MINIO_ENDPOINT=minio:9000
MINIO_PUBLIC_ENDPOINT=localhost:9000
MINIO_BUCKET_ITEMS=home-organizer-items
MINIO_BUCKET_UPLOADS=home-organizer-uploads
MINIO_USE_SSL=false

# ----- AI Provider -----
AI_PROVIDER=openai_compatible
AI_API_KEY=sk-xxxxx-replace-me
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL_VISION=gpt-4o
AI_MODEL_RECOMMEND=gpt-4o-mini
AI_TIMEOUT_S=30

# ----- CORS -----
CORS_ORIGINS=http://localhost:3000,http://localhost:8000
```

### ⏳ 缺口：存储后端的 4 个键没进 `.env.example`

`STORAGE_BACKEND` / `STORAGE_LOCAL_DIR` / `STORAGE_LOCAL_SECRET` / `API_PUBLIC_BASE_URL`
（`app/core/config.py` 里都有，默认 `minio` / `./var/storage` / `None` / `""`）**没有**写进
`.env.example`。本地 demo（没有 MinIO）需要手工加：

```bash
STORAGE_BACKEND=local
STORAGE_LOCAL_DIR=./var/storage
```

**铁律**：

- `.env` 必须留在 `.gitignore` 里。
- `AI_API_KEY` 只出现在 `api` 进程的环境里，绝不进浏览器（前端从不直连 AI；所有 `/api/*` 请求
  都经 Next rewrite 或 Nginx 转发）。
- 生产环境所有 `change-me*` / 默认密码必须替换；`JWT_SECRET` 用占位值时 `Settings` 会告警。
- ⚠️ **`Settings` 会读 `services/api/.env`，所以 `.env` 的值会漏进测试。** 这一点在
  `docs/AGENTS.md` §8 有详述（两个 conftest 都会把存储后端 pin 回 `minio`）。

---

## 5. Nginx 配置（已写，未接进 compose）

`infra/nginx/default.conf` 的实际内容 —— **它没有 `web` upstream，也没有 `location /`**：

```nginx
upstream api_upstream { server api:8000; }

server {
  listen 80;
  client_max_body_size 20m;     # 单张图片上限

  # API
  location /api/ {
    proxy_pass http://api_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 90s;      # 推荐接口允许较长
  }

  # MinIO 开发代理（生产建议走 CDN / 独立域名 + 预签名）
  location /minio/ {
    rewrite ^/minio/(.*) /$1 break;
    proxy_pass http://minio:9000;
    proxy_set_header Host $host;
  }
}
```

要作为完整入口，还需补 `web` upstream + `location /`（含 WebSocket upgrade 头）。

---

## 6. 启动流程（有 Docker 的机器）

```bash
# 1. 环境变量
cp .env.example .env
# 编辑 .env：至少改 JWT_SECRET、AI_API_KEY、MINIO_ROOT_PASSWORD

# 2. 启动后端全套
docker compose up -d --build

# 3. 迁移
docker compose exec api alembic upgrade head

# 4. （可选）seed
docker compose exec api python -m app.db.seed

# 5. 访问
#   API 文档:  http://localhost:8000/docs
#   MinIO 控制台: http://localhost:9001
#   Web 需另起：cd apps/web && npm run dev
```

> ⚠️ seed 是**幂等且从不 UPDATE** 的（`_ensure_*` 命中即返回旧行）。改了 seed 数据后想让新值
> 生效，必须删库重建（本地 SQLite demo 是删 `/tmp/home-organizer-demo.db`）。

---

## 7. 本地开发（无 Docker，沙箱路径）

```bash
# --- API ---
cd services/api
source .venv/bin/activate
alembic upgrade head                             # 迁移
python -m pytest tests/ --no-header -q          # 基线 534 passed / 1 skipped
python -m ruff check app/ tests/                 # 基线 32
python -m mypy app/                              # 基线 20
uvicorn app.main:app --host 0.0.0.0 --port 8000

# --- Web ---
cd apps/web
npm run dev          # 默认 3000，本仓 demo 用 18080：next dev -p 18080
npx tsc --noEmit
npx next lint
```

本地 demo 的 `.env` 跑的是**真实 DeepSeek**（`AI_PROVIDER=openai_compatible` +
`AI_BASE_URL=https://api.deepseek.com/v1`），没有 MinIO 时用 `STORAGE_BACKEND=local`。

---

## 8. 镜像构建

### 8.1 API Dockerfile（`services/api/Dockerfile`，实际内容）

```dockerfile
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# psycopg 编译需要
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system -e ".[dev]"

COPY . /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> ⏳ 两个可以收口的地方（**尚未改**）：装了 `.[dev]` 把测试/lint 依赖也带进运行镜像；
> 没有用 `uv.lock` 锁定版本。

### 8.2 Web Dockerfile（📋 设计稿，文件不存在）

```dockerfile
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```

> Next.js 需要在 `next.config.mjs` 里开 `output: "standalone"`。**当前配置里没有开。**

---

## 9. 数据持久化与备份

### 9.1 持久卷

- `pgdata` — PostgreSQL（唯一的状态来源）
- `redisdata` — Redis（业务未使用，见 `docs/ARCHITECTURE.md` §6.3）
- `miniodata` — MinIO 对象
- 本地后端模式下图片在 `services/api/var/storage/`，**不在卷里，也不该进 git**

生产部署强烈建议用托管服务 / 云盘，不要用本地卷。

### 9.2 备份（📋 第二版）

- PostgreSQL：每日 `pg_dump`，归档到对象存储。
- MinIO：versioning + bucket replication。
- Redis：可容忍全丢，不单独备份。

### 9.3 恢复演练（📋）

- 每季度一次：从 `pg_dump` + bucket 恢复到新环境，验证关键流程跑通。

---

## 10. 生产部署建议（📋 第二版）

- 全部走托管：PostgreSQL (RDS)、Redis (ElastiCache)、S3 / 兼容 S3 的对象存储。
- API / Web 走 K8s 或 ECS，多副本。
- HTTPS：Nginx / 云 LB 终结 TLS。
- 域名：`app.example.com` → Web；`api.example.com` → API；`cdn.example.com` → 图片。
- 图片走 CDN，源站私有 bucket + 预签名。
- WAF / 速率限制在 Nginx / 云 LB 层（应用层限流**未实现**）。
- 日志：API stdout → Loki / CloudWatch。
- 监控：Prometheus + Grafana（**未实现**）。

### 10.1 CI / CD（📋 第二版）

- CI：lint + type check + 测试 + `alembic check` + 镜像构建。
- CD：合并 main → 构建镜像 → 推 registry → 滚动部署。
- DB 迁移：先 `alembic upgrade head`，再切流量；schema 变更独立窗口执行。

---

## 11. 故障排查速查

| 症状 | 排查 |
| --- | --- |
| 端口 8000 起不来 `[Errno 98]` | **上一次的 uvicorn 没死干净。** 杀 bash 包装进程不会杀掉 uvicorn 子进程 —— 查 `/proc/[0-9]*/cmdline` 找到 python 子进程显式 `kill`，再确认 `/health` 探活，否则请求会继续打在被删的旧进程上 |
| `/api/*` 502 | `docker compose logs api`；看迁移是否跑过、env 是否缺失 |
| 上传卡在上传步 | 存储后端不可达。没有 MinIO 时切 `STORAGE_BACKEND=local`；MinIO 模式下检查 bucket 是否 `mc anonymous set none` 与 `MINIO_*` 变量 |
| 视觉识别 503 / 400 | 图片 URL 必须是模型能访问的地址。远端模型解析不了 `localhost:9000` —— 现走**内联 data URI**（见 `docs/ARCHITECTURE.md` §4.3），若报错先查图片字节是否真的存在于存储里 |
| 推荐一直 `state=failed` / 「无符合硬规则的位置」 | `pre_filter_count == 0` ⇒ 候选生成就把位置筛光了。最常见原因是 `Item.category` 不在任何 slot 的 `allowed_categories` 里（模型编了不存在的类别）；查 `agent_traces.steps` |
| 推荐一直 `verifier_failed` | 查 `agent_traces.steps` 里 VERIFY 步的 `error`，定位是哪条 check 失败；看是否 HomeRule 太严 |
| 改了 `.env` 后测试全红 | `Settings` 读了 `services/api/.env`。conftest 会把存储后端 pin 回 `minio`；新增的开关也要同样处理 |
| Web 报「需要一个只在 server 用的模块」 | `next dev` 的增量编译模块图过时了（文件在 client/server 之间翻转后常见）。**重启 `next dev`，别去改代码** |
| Postgres 启动失败 | `docker compose logs postgres`；多为端口冲突或密码含特殊字符 |
