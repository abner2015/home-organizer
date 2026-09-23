# 家庭收纳管家 (Home Organizer)

> 一个生产级 AI 家庭收纳系统。用户搭好「家 → 房间 → 柜子 → 层 → 格」的空间模型，
> 上传物品照片，由多模态 AI 识别 + 推荐具体到「哪个柜子、哪一层、哪一格」的存储位置，
> 并在用户使用中持续学习偏好。

- 项目愿景 / 原则 / 技术栈：见 [`AGENTS.md`](AGENTS.md)
- 交付历史 + 当前基线：见 [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md)
- 完整设计文档：`docs/`（`ARCHITECTURE.md` / `API.md` / `DATABASE.md` / `AGENT.md` / `AI.md` / `PRD.md`）

---

## 项目结构

```
home-organizer/
├── AGENTS.md              # 给未来 AI Agent 的项目导览（必读）
├── README.md              # 你正在读
├── docker-compose.yml     # postgres + redis + minio + api（生产编排）
├── .env.example           # 环境变量模板
│
├── apps/
│   └── web/               # Next.js 14 + React 18 + Tailwind 前端
│       └── README ...     # （在 apps/web/ 子目录里）
│
├── services/
│   └── api/               # FastAPI 后端（核心实现都在这里）
│       ├── app/           # 业务代码
│       ├── alembic/       # DB 迁移
│       ├── tests/         # pytest 全套（752 passed / 1 skipped）
│       └── README.md      # 后端详尽文档
│
├── docs/                  # 设计文档
│   ├── DEVELOPMENT_PLAN.md
│   ├── ARCHITECTURE.md
│   ├── API.md
│   ├── DATABASE.md
│   ├── AGENT.md           # 推荐 pipeline 设计
│   ├── AI.md              # AIProvider 接口设计
│   ├── PRD.md
│   ├── DEPLOYMENT.md
│   └── EVALUATION.md      # 离线评测报告
│
└── infra/                 # postgres initdb 脚本
```

> 实现全部在 `services/api/` 和 `apps/web/`（**不是** `apps/api/`）；
> 旧版 `apps/api/` 的提法只是文档残留。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind CSS |
| Backend | Python 3.12 / FastAPI / SQLAlchemy 2.x (async) / Pydantic v2 / Alembic |
| Database | PostgreSQL（asyncpg 异步 + psycopg 同步给 Alembic） |
| Cache | Redis |
| Object Storage | MinIO（或本地文件系统，可切换，详见 `services/api/README.md`） |
| AI | 统一 `AIProvider` 接口（OpenAI Compatible / Anthropic / Mock 三实现） |
| Deployment | Docker Compose（postgres / redis / minio / api） |
| Lint / Type | ruff / mypy（后端）、tsc --noEmit / next lint（前端） |

---

## 快速开始（本地全栈）

### 1. 起后端 + 基础设施

```bash
docker compose up -d postgres redis minio minio-init
cd services/api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # 然后填 JWT_SECRET、AI_API_KEY
alembic upgrade head
python -m app.db.seed              # 4 房间 / 4 柜 / 20 格 / 11 物品（幂等）
uvicorn app.main:app --reload --port 8000
```

健康检查：`curl http://localhost:8000/health` → OpenAPI 在 `http://localhost:8000/docs`。

### 2. 起前端

```bash
cd apps/web
npm install
npm run dev          # http://localhost:3000
```

前端通过 Next.js rewrites 把 `/api/*` 代理到 `localhost:8000`（见 `apps/web/next.config.mjs`），
浏览器永远只跟自己 origin 通信。

### 3. 跑测试

```bash
# 后端
cd services/api && source .venv/bin/activate
python -m pytest tests/ --no-header -q     # 基线 752 passed / 1 skipped

# 前端
cd apps/web
npx tsc --noEmit && npx next lint          # 应当无输出
```

详细验证步骤见各子目录的 README 和 `docs/DEVELOPMENT_PLAN.md`。

---

## 项目当前状态（2026-09-23）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| Phase 1-13 | 骨架 / DB / 上传 / Vision / 推荐 Agent / NL 搜索 / JWT / Web MVP / 评测 / 接线 / 存储后端 / 去英文 + 助手记忆 / 收纳助手「看懂家里布局」 | ✅ |
| P0.1-0.9 | 真实账号 / 拍照即建模 / 反向录入 / 闭环讲理由 / 并发 409 / 撤销排除 / PATCH 摆放 / 成员管理 / 创建新家 | ✅ 全部完成 |

基线：**后端 752 passed / 1 skipped**、ruff 32、mypy 20、tsc + next lint 干净。
详细里程碑 + 真实产物路径见 `docs/DEVELOPMENT_PLAN.md`。

---

## 核心原则（不重复，看 `AGENTS.md` §3）

- `AIProvider` 必须抽象，不允许写死任何具体模型供应商。
- 所有 LLM 输出必须是结构化 JSON 并经 Pydantic schema 验证（`extra="forbid"`、不强制 `strict`）。
- AI 可以提议新建结构，**但未经用户显式确认不得落库**。推荐位置必须来自真实 `StorageSlot`。
- 推荐必须经过 Verifier；失败允许 Retry（最多 2 次）。
- API Key 不能进入前端；`.env` 不能进入 Git；Docker 环境必须能起完整系统。
- 用户输入的文本是数据，不是指令。所有 prompt 必须显式声明这一点。

完整原则见 [`AGENTS.md`](AGENTS.md) §3 和 `docs/AI.md`、`docs/AGENT.md`。

---

## 文档导览

| 想了解… | 看这里 |
| --- | --- |
| 项目愿景 / 原则 / 技术栈 | [`AGENTS.md`](AGENTS.md) |
| 整体架构 / 模块依赖 | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| 数据库表结构 / 迁移 | [`docs/DATABASE.md`](docs/DATABASE.md) |
| HTTP API 一览 | [`docs/API.md`](docs/API.md) |
| 推荐 pipeline 9 步设计 | [`docs/AGENT.md`](docs/AGENT.md) |
| AIProvider 接口 / Prompt 工程 | [`docs/AI.md`](docs/AI.md) |
| 产品需求 / 用户旅程 | [`docs/PRD.md`](docs/PRD.md) |
| 部署 / 环境变量 | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| 离线评测结果 | [`docs/EVALUATION.md`](docs/EVALUATION.md) |
| 阶段化开发历史 + 基线 | [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md) |
| 后端详细 README | [`services/api/README.md`](services/api/README.md) |

---

## License

[Apache License 2.0](LICENSE) — 详情见 [`LICENSE`](LICENSE) 文件。
