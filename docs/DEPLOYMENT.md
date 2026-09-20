# DEPLOYMENT — 部署

> Docker Compose 一键启动完整系统：PostgreSQL + Redis + MinIO + API + Web + Nginx。

---

## 1. 服务总览

| 服务 | 镜像来源 | 容器内端口 | 宿主机端口（dev 默认） |
| --- | --- | --- | --- |
| postgres | postgres:16 | 5432 | 5432 |
| redis | redis:7 | 6379 | 6379 |
| minio | minio/minio:latest | 9000 (API), 9001 (console) | 9000, 9001 |
| api | apps/api Dockerfile | 8000 | 8000 |
| web | apps/web Dockerfile | 3000 | 3000 |
| nginx | nginx:1.27-alpine | 80, 443 | 80, 443 |

---

## 2. 目录布局

```
home-organizer/
├── docker-compose.yml
├── .env.example
├── .env                       # 本地真实配置，git 忽略
├── infra/
│   ├── nginx/
│   │   └── default.conf
│   ├── minio/
│   │   └── README.md          # bucket 初始化说明
│   └── postgres/
│       └── initdb/            # 首次启动时执行的 SQL
└── apps/
    ├── api/
    │   ├── Dockerfile
    │   └── ...
    └── web/
        ├── Dockerfile
        └── ...
```

---

## 3. docker-compose.yml（骨架）

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
    depends_on: { minio: { condition: service_healthy } }
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD};
      mc mb -p local/home-organizer-items || true;
      mc mb -p local/home-organizer-uploads || true;
      mc anonymous set none local/home-organizer-items;
      mc anonymous set none local/home-organizer-uploads;
      "

  api:
    build: { context: ./apps/api }
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
      minio:    { condition: service_healthy }
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
    volumes: ["./apps/api:/app"]
    ports: ["8000:8000"]

  web:
    build: { context: ./apps/web }
    env_file: .env
    depends_on: [api]
    ports: ["3000:3000"]

  nginx:
    image: nginx:1.27-alpine
    depends_on: [api, web]
    volumes:
      - ./infra/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
    ports: ["80:80"]

volumes:
  pgdata:
  redisdata:
  miniodata:
```

---

## 4. 环境变量

`.env.example`：

```bash
# 通用
APP_ENV=development
LOG_LEVEL=INFO
JWT_SECRET=change-me
JWT_ACCESS_TTL=3600
JWT_REFRESH_TTL=2592000

# Postgres
POSTGRES_USER=homeorg
POSTGRES_PASSWORD=homeorg
POSTGRES_DB=homeorg
DATABASE_URL=postgresql+psycopg://homeorg:homeorg@postgres:5432/homeorg

# Redis
REDIS_URL=redis://redis:6379/0

# MinIO
MINIO_ROOT_USER=homeorg
MINIO_ROOT_PASSWORD=homeorg-minio
MINIO_ENDPOINT=minio:9000
MINIO_PUBLIC_ENDPOINT=localhost:9000
MINIO_BUCKET_ITEMS=home-organizer-items
MINIO_BUCKET_UPLOADS=home-organizer-uploads
MINIO_USE_SSL=false

# AI
AI_PROVIDER=openai_compatible
AI_API_KEY=sk-xxxxx
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL_VISION=gpt-4o
AI_MODEL_RECOMMEND=gpt-4o-mini
AI_TIMEOUT_S=30

# Web
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

**铁律**：

- `.env` 必须加入 `.gitignore`。
- `AI_API_KEY` 只在 `api` 服务中出现，不出现在 `web` 服务环境变量里。
- 生产环境所有 `change-me` / 默认值必须替换。

---

## 5. Nginx 配置（骨架）

`infra/nginx/default.conf`：

```nginx
upstream api_upstream { server api:8000; }
upstream web_upstream { server web:3000; }

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

  # MinIO 代理（开发用；生产建议用 CDN / 独立域名 + 预签名）
  location /minio/ {
    rewrite ^/minio/(.*) /$1 break;
    proxy_pass http://minio:9000;
    proxy_set_header Host $host;
  }

  # Web
  location / {
    proxy_pass http://web_upstream;
    proxy_set_header Host $host;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
  }
}
```

---

## 6. 启动流程

```bash
# 1. 复制环境变量模板
cp .env.example .env
# 编辑 .env，至少修改 JWT_SECRET、AI_API_KEY、MINIO_ROOT_PASSWORD

# 2. 一键启动
docker compose up -d --build

# 3. 跑数据库迁移
docker compose exec api alembic upgrade head

# 4. （可选）注入 seed 数据
docker compose exec api python -m app.db.seed

# 5. 访问
# Web:  http://localhost
# API:  http://localhost/api/v1/docs
# MinIO 控制台: http://localhost:9001
```

---

## 7. 镜像构建

### 7.1 API Dockerfile

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# 系统依赖（psycopg / Pillow 编译用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# 依赖
COPY pyproject.toml uv.lock* ./
RUN pip install --no-cache-dir uv && uv sync --frozen

# 代码
COPY . /app

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 7.2 Web Dockerfile

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

> Next.js 需要在 `next.config.js` 中开启 `output: "standalone"`。

---

## 8. 数据持久化与备份

### 8.1 持久卷

- `pgdata` — PostgreSQL
- `redisdata` — Redis
- `miniodata` — MinIO

生产部署强烈建议使用托管服务 / 云盘，不要用本地卷。

### 8.2 备份（第二版）

- PostgreSQL：每日 `pg_dump`，归档到对象存储。
- MinIO：开启 versioning + bucket replication。
- Redis：开启 AOF（可容忍少量数据丢失）。

### 8.3 恢复演练

- 每季度抽一次回放：从 `pg_dump` + MinIO bucket 恢复新环境，验证关键流程跑通。

---

## 9. 生产部署建议（第二版）

- 全部走托管：PostgreSQL (RDS)、Redis (ElastiCache / 自托管)、MinIO / S3。
- API / Web 走 K8s 或 ECS，多副本；AI 调用走 HPA。
- HTTPS：Nginx 终结 TLS（certbot / 云厂商证书）。
- 域名：`app.example.com` 走 Web；`api.example.com` 走 API；`cdn.example.com` 走图片。
- 图片走 CDN，源站 MinIO 用私有 bucket + 预签名。
- WAF / 速率限制在 Nginx / 云 LB 层。
- 日志：API 输出到 stdout → 收集到 Loki / CloudWatch。
- 监控：Prometheus + Grafana（指标）+ Sentry（前端错误）。

---

## 10. CI / CD（第二版）

- CI：lint + type check + 测试 + alembic check + 镜像构建。
- CD：合并 main → 自动构建镜像 → 推送 registry → 滚动部署。
- DB 迁移：先 `alembic upgrade head`，再切流量。
- 任何 schema 变更必须 review，且 Alembic 升级在生产环境独立窗口执行。

---

## 11. 故障排查速查

| 症状 | 排查 |
| --- | --- |
| Web 打不开 | `docker compose ps` 看 nginx/web 状态；`docker compose logs web` |
| `/api/*` 502 | `docker compose logs api`；看是否 alembic 未跑、env 缺失 |
| 推荐一直 verifier_failed | 查 `agent_traces.steps` 看哪条规则失败；调整 HomeRule |
| MinIO 上传 403 | 检查 bucket 是否 `mc anonymous set none`；检查 `MINIO_*` 环境变量 |
| Postgres 启动失败 | `docker compose logs postgres`；可能端口冲突或密码含特殊字符 |
| 容器 OOM | 调高 docker compose memory limit；或查慢 SQL |
