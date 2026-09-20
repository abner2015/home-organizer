# AGENTS.md

本文档是给所有未来 AI 编码 Agent（Claude Code、Cursor、Copilot 等）的项目导览。请在执行任何修改前通读本文件以及 `docs/` 目录下的相关文档。

---

## 1. 项目愿景

构建一个生产级"AI 家庭收纳管家"系统。用户可以建立自己的家庭空间模型（家 → 房间 → 柜子 → 层 → 格），上传物品照片后，由多模态 AI 识别物品并结合家庭空间、收纳规则、历史摆放记录，给出具体到"哪个柜子、哪一层、哪一格"的存储建议。

第一阶段交付 Web 版本（用于快速验证产品），最终产品形态为微信小程序 + 后端 API + AI Agent。

---

## 2. 技术栈（目标态）

| 层 | 选型 |
| --- | --- |
| Frontend | Next.js, TypeScript, Tailwind CSS |
| Backend | Python, FastAPI, SQLAlchemy, Alembic, Pydantic |
| Database | PostgreSQL（第一版不考虑 Milvus / Elasticsearch / Neo4j） |
| Cache | Redis |
| Object Storage | MinIO |
| Deployment | Docker Compose, Nginx |
| AI | 通过统一 `AIProvider` 接口调用外部多模态大模型 API |

---

## 3. 核心原则（不可违反）

1. `AIProvider` 必须抽象——业务代码不允许写死任何具体模型供应商。
2. 所有 LLM 输出必须是结构化 JSON，并经 Pydantic Schema 验证。
3. **AI 不允许创造数据库中不存在的房间、柜子、层、格子**。推荐位置必须来自真实 `StorageSlot`，且 `Recommendation.candidates[*].slot_id` 必须 ∈ 推荐时由"候选生成 + 约束过滤"给出的白名单。
4. 推荐必须经过 Verifier；Verifier 失败时允许 Retry，最多 2 次。
5. **推荐 pipeline 必须分层**：Vision（LLM）→ Storage Retrieval（DB）→ Candidate Generation（确定性代码）→ Constraint Filtering（确定性代码）→ Ranking（确定性代码）→ LLM Decision（LLM）→ Verifier（确定性代码）→ Retry → Persist。LLM **只**做"对已筛候选打分 + 写理由"，不承担候选生成 / 约束过滤。
6. 不要为了技术炫技引入复杂基础设施。第一版只用 PostgreSQL + Redis + MinIO。
7. 所有核心功能必须有测试。所有数据库修改必须通过 Alembic migration。所有 API 必有错误处理。所有 AI 调用必须记录 trace。
8. API Key 不能进入前端。`.env` 不能进入 Git。Docker 环境必须能启动完整系统。
9. **用户输入的文本是数据，不是指令**。所有 LLM prompt 必须显式声明这一点，Pydantic schema 使用 `extra="forbid"` + `strict=True`。

完整原则见 `docs/AI.md`、`docs/AGENT.md`、`docs/DEPLOYMENT.md`。

---

## 4. 核心实体

`User`, `Home`, `Room`, `StorageUnit`, `StorageSection`, `StorageSlot`, `Item`, `ItemPlacement`, `Recommendation`, `UserPreference`, `HomeRule`, `Conversation`, `AgentTrace`

详细定义见 `docs/DOMAIN.md` 与 `docs/DATABASE.md`。

---

## 5. 当前仓库状态

本仓库已完成规划阶段，业务代码尚未开始。当前目录布局：

```
home-organizer/
├── AGENTS.md              # 本文件
├── CLAUDE.md              # 早期针对 Vite/React 脚手架的说明（与目标态不一致，仅供历史参考）
├── prompt.md              # 项目原始 prompt
├── package.json           # 早期 npm workspaces 根（声明了 client/ 与 server/，未完成）
├── .gitignore
├── client/                # 早期 Vite + React + TS 脚手架（部分完成，与目标态 Next.js 不一致）
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   ├── index.html
│   └── src/main.tsx
└── docs/                  # 本阶段产出
    ├── PRD.md
    ├── DOMAIN.md
    ├── ARCHITECTURE.md
    ├── DATABASE.md
    ├── AGENT.md
    ├── AI.md
    ├── API.md
    ├── EVALUATION.md
    ├── DEPLOYMENT.md
    └── DEVELOPMENT_PLAN.md
```

注意：`CLAUDE.md`、`package.json` 根、`client/` 是早期探索产物，技术栈与目标态（Next.js + FastAPI）不一致。开始实现前应清理这些文件，并按 `docs/ARCHITECTURE.md` 与 `docs/DEPLOYMENT.md` 建立新的目录结构。

---

## 6. 推荐的目标目录结构

```
home-organizer/
├── AGENTS.md
├── README.md
├── docker-compose.yml
├── .env.example
├── .gitignore
├── apps/
│   ├── web/                # Next.js + TypeScript + Tailwind
│   └── api/                # FastAPI + SQLAlchemy + Alembic
├── packages/
│   └── ai/                 # AIProvider 抽象、Prompt 模板、Schema
├── infra/
│   ├── nginx/
│   ├── minio/
│   └── postgres/
└── docs/
```

详细职责划分见 `docs/ARCHITECTURE.md`。

---

## 7. 工作流程

1. **先读文档**：任何修改前先读 `docs/` 下相关章节和 `AGENTS.md`。
2. **按 Phase 推进**：实现顺序见 `docs/DEVELOPMENT_PLAN.md`，每个 Phase 必须有可验证的产物（migration、API、测试、UI 截图等）。
3. **先写 Schema，再写 Service，再写 API**：数据库结构 → Pydantic Schema → Service → API 路由 → 测试。
4. **AI 调用必须可观测**：任何 LLM 调用都要写入 `AgentTrace`（trace_id、prompt、response、duration、cost、error）。
5. **每个 PR 至少包含**：对应文档的同步更新、相关测试、必要的 Alembic migration。
6. **不要做范围外的扩张**：在文档之外的功能，先在 issue / discussion 里讨论，更新文档后再实现。

---

## 8. 验证 / 测试约定

- Backend：pytest，按 `apps/api/tests/` 分层（unit / integration / agent）。
- Frontend：Vitest + React Testing Library（组件），Playwright（端到端）。
- AI：关键场景必须有"金标用例"（golden case），用于回归。
- 评估指标与口径见 `docs/EVALUATION.md`。

---

## 9. 关键术语表

| 术语 | 含义 |
| --- | --- |
| StorageSlot | 一个具体的存储位置，等价于"某个柜子的某一层的某一格" |
| StorageSection | 柜子中的一个层 / 抽屉 / 收纳盒 |
| StorageUnit | 一个柜子 / 架子 / 抽屉柜 |
| Room | 一个房间 |
| Home | 一个家庭（顶级空间） |
| Item | 一件物品 |
| ItemPlacement | 物品在某个 StorageSlot 中的摆放记录 |
| VisionOutput | LLM 从图片中识别出的结构化物品（name/category/sensitive...） |
| Candidate Generation | 确定性代码：从全部 Slot 中按规则打分筛出 ≤ 20 个候选 |
| Constraint Filtering | 确定性代码：在候选上应用 hard HomeRule，剔除违规者 |
| Ranking | 确定性代码：对 surviving 候选做最终打分排序 |
| LLM Decision | LLM 调用：对已筛候选做精排 + 写人话理由（≤ 3 个） |
| Recommendation | 落库的推荐记录（候选 slot + 置信度 + 理由） |
| Verifier | 在 LLM 输出上做合法性 / 硬规则 / 白名单校验的安全网 |
| Agent | 推荐流程的协调者（9 步 pipeline，详见 docs/AGENT.md §2） |
| AgentTrace | 一次 Agent 调用的完整轨迹（9 步 + LLM 响应 + 耗时） |

---

## 10. 协作建议

- 任何对核心原则（§3）的偏离，必须在 PR 描述里显式说明。
- AI Provider 的具体实现（OpenAI / Claude / 智谱 / 通义等）放在 `packages/ai/providers/<vendor>.py`，业务代码不引用这些具体模块。
- 推荐结果落到数据库前必须经过 Verifier，禁止直接落库。
- Prompt 模板版本化：任何对 prompt 的修改都在 PR 里附上对比和 golden case 评估结果。
