# DEVELOPMENT_PLAN — 阶段化开发计划

> 把开发拆成可以逐个验证的 Phase。每个 Phase 必须有明确产物和验收标准。

---

## 总览

| Phase | 目标 | 关键产物 | 验收 |
| --- | --- | --- | --- |
| 0 | 规划与脚手架 | docs/*、AGENTS.md、目录骨架 | 文档评审通过；`docker compose config` 合法 |
| 1 | 基础设施 | docker-compose 启动；alembic 空库 | `docker compose up` 跑通；`alembic upgrade head` 成功 |
| 2 | 用户与认证 | signup / login / me；JWT | 端到端跑通；测试覆盖 |
| 3 | 家庭空间 CRUD | Home/Room/Unit/Section/Slot | 树形 API 完整；前端可建空间 |
| 4 | MinIO 预签名上传 | `/uploads/presign` + 直传 | 浏览器能上传图片到 MinIO |
| 5 | AIProvider 抽象 + Vision | Provider Protocol + OpenAI 兼容实现 | Vision 在 golden case 上 ≥ 75% 准确率 |
| 6 | Candidate Generation（确定性） | `generate_candidates` + 冷启动默认映射 | 单测覆盖所有权重分支与冷启动 |
| 7 | Constraint Filtering + Verifier | `apply_hard_rules` + 复用为 Verifier | 单测覆盖所有 hard rule |
| 8 | Ranking + LLM Decision | 确定性 ranking + `rank_candidates` + Retry | golden 集 Top-1 ≥ 55% |
| 9 | 推荐端到端 | Orchestrator 串联 9 步 + 落库 | 端到端 5 个 case 全过 |
| 10 | 物品搜索 | `/items` 查询 + 当前 placement + `/candidates` 调试 | P95 ≤ 300ms |
| 11 | 前端最小可用 | 录入新物品全流程 | 浏览器录 1 件物品到推荐到接受 |
| 12 | 评估与 golden | eval 脚本 + golden set | 评估脚本可跑、报告生成 |
| 13 | 第二版候选 | pgvector / 多轮对话 / 小程序 | 视业务决定 |

---

## Phase 0 — 规划与脚手架

**目标**：完成所有规划文档；建立目录骨架。

**任务**：

- 写完 `docs/` 下 10 个文档
- 写 `AGENTS.md`
- 删除早期 Vite/React 脚手架（`client/`、`package.json` 根、`CLAUDE.md`）
- 决定 monorepo 形式（apps/* 还是单仓两个 root）
- 创建 `.env.example`、`.gitignore`、`docker-compose.yml` 骨架（先不写 service 内容）

**产物**：

- 完整 docs/
- 目录结构与 `docs/ARCHITECTURE.md` §6 一致
- `docker compose config` 解析通过

**验收**：

- 团队评审 docs，无重大遗留问题
- `git status` 干净；目录与架构图一致

---

## Phase 1 — 基础设施

**目标**：`docker compose up` 跑通所有服务，能连数据库。

**任务**：

- 写 `apps/api/Dockerfile`、`apps/web/Dockerfile`
- 写 `infra/nginx/default.conf`
- 写 `docker-compose.yml`（仅 postgres / redis / minio / minio-init）
- 写 `apps/api/alembic.ini` 与初始空 migration
- 写 `apps/api/app/main.py`（空 FastAPI，仅 `/health`）
- 写 `apps/web/app/page.tsx`（占位）
- 接入 `pgcrypto` 扩展

**产物**：

- 6 个服务启动并 healthy
- `curl http://localhost:8000/health` → 200
- `curl http://localhost:3000` → 200

**验收**：

- `docker compose up -d` → 全 healthy
- 关闭再启动不丢数据（pgdata、miniodata 持久）
- `alembic upgrade head` 在空库上成功

---

## Phase 2 — 用户与认证

**目标**：注册、登录、当前用户可用。

**任务**：

- 写 `users`、`home_memberships` 表 + migration
- 实现 `/api/v1/auth/signup` `/login` `/refresh` `/me`
- 实现密码 bcrypt、JWT 签发 / 校验
- 写 FastAPI 依赖 `get_current_user`

**产物**：

- 4 个接口 + 测试

**验收**：

- signup → 201；login → 拿到 access/refresh
- 错误密码 → 401
- `me` 在无 token → 401；有 token → 200
- 单元 + 集成测试通过

---

## Phase 3 — 家庭空间 CRUD

**目标**：能建家、建房间、建柜子、层、格。

**任务**：

- 写 `homes`、`rooms`、`storage_units`、`storage_sections`、`storage_slots` 表 + migration
- 实现对应 CRUD API（按 `docs/API.md` §3-§5）
- 实现 `/api/v1/homes/{id}/space-tree`
- 权限校验：仅 home 成员能读写
- 约束：删除有子级的 unit/room/section 需 409

**产物**：

- 完整空间 API + 测试
- 树形接口单测覆盖层级

**验收**：

- 端到端：signup → 创建 home → 创建 room → 创建 unit → 创建 section → 创建 slot
- 删除带 active placement 的 slot → 409
- 非成员调接口 → 403

---

## Phase 4 — MinIO 预签名上传

**目标**：浏览器能拿到预签名 URL 并直传 MinIO。

**任务**：

- 写 `infra/minio/init` 初始化 bucket
- 写 `app/storage/minio_client.py`
- 写 `/api/v1/uploads/presign`（生成 PUT 预签名 URL）
- 写 `/api/v1/uploads/url`（GET 预签名 URL，用于展示）

**产物**：

- 预签名 API + 测试

**验收**：

- 浏览器拿到 URL → PUT 上传成功
- 错误 content_type / 错误 method → 4xx
- 过期 URL → 403

---

## Phase 5 — AIProvider 抽象 + Vision

**目标**：拿到图片即可输出结构化 VisionOutput（仅 Vision，不含推荐）。

**任务**：

- 写 `app/ai/provider.py`（Protocol，含 `vision_recognize` 与 `rank_candidates`）
- 写 `app/ai/schemas.py`（`VisionOutput` / `RankingOutput` / `CandidateSlot`，`extra="forbid"` + `strict=True`）
- 写 `app/ai/providers/openai_compatible.py`
- 写 `app/ai/providers/anthropic.py`（如必要）
- 写 `app/ai/factory.py`（按 `AI_PROVIDER` 选择实现）
- 写 `app/agent/prompts/vision.v1.md`
- 写 `/api/v1/items/{id}/vision`
- 写 `app/observability/pii_scrubber.py`（基础正则清理）

**产物**：

- 完整 AI 层（仅 Vision）+ 调试用 rank 入口（暂未接 pipeline）
- 20 张 golden vision set

**验收**：

- golden set Top-1 准确率 ≥ 75%
- Provider 不可用时返回 503 + trace
- 解析失败 → 自动 Retry → 失败后写 trace
- 输出含候选列表外的 slot_id 时抛 `AIOutputConstraintError`（即便调用方未传 candidates 也要对 schema 严格）

---

## Phase 6 — Candidate Generation（确定性）

**目标**：从全部 Slot 中用确定性代码筛出 ≤ 20 个候选。

**任务**：

- 写 `app/agent/candidate_gen.py`：`generate_candidates(item, slots, history, preferences)`
- 写 `app/agent/scoring.py`：实现 §4 权重公式
- 写 `app/agent/default_room_map.py`：内置 category → room_type 映射（可被 HomeRule 覆盖）
- 写 `app/agent/contexts.py`：`SlotContext` / `ItemContext` / `ScoredSlot` 等 Pydantic 模型
- 单测：每个权重分支、冷启动、无历史、容量不匹配

**产物**：

- `generate_candidates` 函数 + 完整单测

**验收**：

- 给定固定输入，输出完全确定
- 冷启动（无历史）能给出 top 20
- 性能：1000 slot 输入下 < 100ms

---

## Phase 7 — Constraint Filtering + Verifier

**目标**：硬规则约束过滤（pipeline Step 5） + LLM 输出安全网（Step 8），复用同一规则引擎。

**任务**：

- 写 `app/agent/rules.py`：`apply_hard_rules(candidates, rules, item)` —— **同一函数被 Step 5 和 Step 8 复用**
- 写 `app/agent/verifier.py`：`verify(ranking_output, candidates_whitelist, item, rules)` —— 包含白名单校验 + 存在性 + 硬规则 + 去重 + 数量
- 单测：每条 hard rule 单独测、组合测、白名单违反测

**产物**：

- 规则引擎 + Verifier + 完整单测

**验收**：

- 单测覆盖所有 hard rule 类型与组合
- LLM 输出含候选外 slot_id → Verifier 拦截

---

## Phase 8 — Ranking + LLM Decision

**目标**：确定性 Ranking（Step 6） + LLM Decision（Step 7） + Retry。

**任务**：

- 写 `app/agent/ranking.py`：`rank_candidates(candidates)` 增量信号 + 排序
- 写 `app/agent/prompts/rank.v1.md`
- 写 `app/agent/retry.py`：Retry 策略（≤ 2 次，注入 last_failure）
- 写 `app/ai/providers/<vendor>.py` 中的 `rank_candidates` 实现
- 写 30 条 golden recommend set
- 单测：Retry 0 / 1 / 2 次 + Provider 错误

**产物**：

- Ranking + LLM Rank + Retry + golden

**验收**：

- golden set Top-1 ≥ 55%，Top-3 ≥ 80%
- Verifier 失败后 ≤ 2 次 Retry 通过率 ≥ 90%
- LLM 越界选 slot → Verifier 拦截 → Retry → 成功

---

## Phase 9 — 推荐端到端

**目标**：Orchestrator 串联 9 步 pipeline + 落库。

**任务**：

- 写 `app/agent/orchestrator.py`：9 步状态机
- 写 `recommendations`、`item_placements`、`agent_traces` 表 + migration（含 `pre_filter_count` / `post_filter_count`）
- 写 `/api/v1/items/{id}/recommend` `/accept` `/adjust` `/reject`
- 写 `/api/v1/items/{id}/candidates`（调试端点）
- 写 `app/services/placement_service.py` / `item_service.py` / `rule_service.py` / `preference_service.py`
- 5 个端到端 case 测试

**产物**：

- 完整推荐 pipeline + 落库 + 调试端点
- 端到端集成测试

**验收**：

- 5 个 case 全过：成功 / Retry 后成功 / 候选空 / 全过滤 / 调整 / 否决
- `agent_traces` 写入 9 步 steps
- `recommendations.pre_filter_count` / `post_filter_count` 正确
- `placements` 在 accept 后落库

---

## Phase 10 — 物品搜索

**目标**：能用关键字找物品及其当前位置。

**任务**：

- 写 `/api/v1/items` 查询（分页、过滤）
- 写 `GET /api/v1/items/{id}/placements`（历史时间线）
- 加索引 `(home_id, category)`、`(home_id, name)`

**产物**：

- 搜索 API + 测试

**验收**：

- P95 ≤ 300ms（1000 items / home 规模下）
- 支持 `q`、`category`、`in_slot` 过滤
- 时间线按 `placed_at` 倒序

---

## Phase 9 — 前端最小可用

**目标**：浏览器能完整跑"建家 → 录物品 → 看推荐 → 接受"。

**任务**：

- 写 Next.js 路由（按 `docs/API.md` §3）
- 写空间树组件
- 写物品上传组件（用 presign URL）
- 写推荐结果展示
- 写历史 / 搜索

**产物**：

- 完整 Web 端到端流程
- 截图 / 录屏

**验收**：

- 全流程在浏览器跑通
- 主要页面 Lighthouse Performance ≥ 80
- 关键交互有 loading / error UI

---

## Phase 10 — 物品搜索

**目标**：能用关键字找物品及其当前位置。

**任务**：

- 写 `/api/v1/items` 查询（分页、过滤）
- 写 `/api/v1/items/{id}/candidates` 调试端点
- 写 `GET /api/v1/items/{id}/placements`（历史时间线）
- 加索引 `(home_id, category)`、`(home_id, name)`

**产物**：

- 搜索 API + 测试

**验收**：

- P95 ≤ 300ms（1000 items / home 规模下）
- 支持 `q`、`category`、`in_slot` 过滤
- 时间线按 `placed_at` 倒序

---

## Phase 11 — 前端最小可用

**目标**：浏览器能完整跑"建家 → 录物品 → 看推荐 → 接受"。

**任务**：

- 写 Next.js 路由（按 `docs/API.md` §3）
- 写空间树组件
- 写物品上传组件（用 presign URL）
- 写推荐结果展示（含 9 步 pipeline 状态——若 LLM 慢，前端展示"正在筛选候选"等中间态）
- 写历史 / 搜索

**产物**：

- 完整 Web 端到端流程
- 截图 / 录屏

**验收**：

- 全流程在浏览器跑通
- 主要页面 Lighthouse Performance ≥ 80
- 关键交互有 loading / error UI

---

## Phase 12 — 评估与 golden

**目标**：建立持续评估机制。

**任务**：

- 写 `apps/api/scripts/eval/eval_vision.py`
- 写 `apps/api/scripts/eval/eval_candidate_gen.py`（新增：预过滤候选 Top-1 命中率）
- 写 `apps/api/scripts/eval/eval_recommend.py`
- 写 `apps/api/scripts/eval/eval_verifier.py`
- 扩充 golden set 到 ≥ 50 个 case
- 把 eval 接入 CI

**产物**：

- 评估脚本 + 报告
- CI 状态

**验收**：

- 每次 PR 自动跑 eval
- 报告对比基线，回归可见
- 包含 `pre_filter_top1_in_final_rate`（预过滤 top1 是否进 final ≥ 95%）

---

## Phase 13+ — 后续候选

- pgvector + 图像相似度搜索
- 多轮对话（Conversation / Message）
- 微信小程序
- 多人协作权限细分
- 计费 / 套餐 / 多租户
- 移动端原生

每个 Phase 都视业务反馈再决定是否启动；不在第一版计划内。

---

## 风险登记与应对

| 风险 | 触发条件 | 应对 |
| --- | --- | --- |
| Provider 行为漂移导致 parse 失败 | parse_error 上升 | 把 parse 失败的样本纳入 golden set；调 prompt / Pydantic schema |
| 推荐接受率低 | 低于 60% | 调 prompt；增加 explanation；做用户访谈 |
| Verifier 误拦截高 | 拦截后用户接受占比 > 5% | 软化 hard 规则；增加 evidence 字段 |
| 上传 / 数据库性能问题 | API P95 上升 | 加缓存；加索引；分离读写 |
| Token 成本失控 | 月成本超预算 | 加限流；切更便宜模型；缓存空间快照 |

---

## 节奏

- 一个 Phase 至少 1 次完整 demo（视频 / 录屏 / 截图）。
- 每个 Phase 结束做一次复盘，更新 `docs/EVALUATION.md` 与本文件。
- 任何对核心原则（AGENTS.md §3）的偏离必须在 PR 描述里显式说明。
