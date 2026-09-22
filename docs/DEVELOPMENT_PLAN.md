# DEVELOPMENT_PLAN — 阶段化开发计划

> **本文件分两篇，读之前先看这一行：**
>
> - **上篇 · 已交付**（项目史 Phase 1–13）：计划早已执行完。这里记录**实际情况** ——
>   真实产物路径、真实验收结论、真实基线。已完成的部分不再有「任务」，只有事实。
> - **下篇 · P0 路线图**（P0.0–P0.4）：唯一还在推进的计划。每个 P0.x 都带交付物与验收标准。
>
> 最后更新：2026-09-22 —— P0.3 反向录入交付；基线 600 → 626。
>
> 背景：本文件原稿写于**开工前**。开工后实际走出来的顺序与原稿并不一致，于是原稿里出现了
> Phase 9、Phase 10 各两份（旧副本与新副本交织），路径也停留在 `apps/api/`。本次一并归位。

---

## 0. 关于「Phase 编号」

仓库里历史存在**两套互不相同的编号**，这是本文件此前自相矛盾的根源：

| 编号体系 | 出处 | 含义 |
| --- | --- | --- |
| 项目史 Phase 1–13 | `AGENTS.md` §5、本文上篇 | **实际交付顺序**，是这个项目真正的历史 |
| 计划稿 Phase 0–13 | 本文件原稿 | **开工前的设想**，与实际交付顺序不同 |

**归一结论：以项目史编号为准。** 上篇按项目史列已交付批次，并附「对应计划稿 Phase」做交叉引用；
原稿里唯一仍然重要的一条计划稿内容 —— **「家庭空间 CRUD」** —— 在 Phase 1–13 期间从未交付，
它不是历史，**它就是 P0.2 的缺口本身**；现已随 P0.2 补齐（见下篇）。

---

# 上篇 · 已交付（项目史 Phase 1–13）

## 总览

| Phase | 内容 | 对应计划稿 | 状态 |
| --- | --- | --- | --- |
| 1 | 骨架与基础设施 | Phase 0 + 1 | ✅ |
| 2 | 数据库：14 实体 / 16 表 + Alembic `0001` + seed | Phase 2(表) + 3(表) | ✅ |
| 3 | 图片上传（assets + presign） | Phase 4 | ✅ |
| 4 | AI Vision（`AIProvider` + 识别） | Phase 5 | ✅ |
| 5 | 存储推荐 Agent（9 步 pipeline + Verifier + Retry） | Phase 6+7+8+9 | ✅ |
| 6 | 自然语言搜索（含多轮记忆） | Phase 10(读侧) + 原稿没有的新增 | ✅ |
| 7 | JWT 认证接口 | Phase 2(接口) | ✅ |
| 8 | Web 前端 MVP | Phase 9/11(前端) | ✅ |
| 9 | 评估 harness + golden set | Phase 12 | ✅ |
| 10 | Web ↔ API 接线（读路径 + 写路径） | Phase 3(读接口) | ✅ |
| 11 | 可切换存储后端 + 图片内联识别 + 模型填表 | Phase 4 的扩展 | ✅ |
| 12 | 去英文 + 助手记忆 + 模型代笔 | — | ✅ |
| 13 | 收纳助手「看懂家里布局」 | — | ✅ |

**当前基线**（任何改动都不得使其上升/下降）：

- `services/api`：**626 passed / 1 skipped**
- `ruff check app/ tests/`：**32**（历史遗留，不得上升）
- `mypy app/`：**20**（历史遗留，不得上升）
- `apps/web`：`npx tsc --noEmit` 与 `npx next lint` **干净**

## Phase 明细

### Phase 1 — 骨架与基础设施 ✅

`services/api/app/{core,db,cache}`、`/health`、结构化日志、异常处理器；
`docker-compose.yml`（postgres / redis / minio / minio-init / api）、`infra/postgres/initdb/`（pgcrypto）。

### Phase 2 — 数据库 ✅

14 实体 / 16 表，Alembic `0001_initial_schema`（含全部 CHECK、部分唯一索引、可延迟 FK），
`python -m app.db.seed` 幂等种子（4 房间 / 4 柜 / 20 格 / 11 物品）。

> 表在原稿的 Phase 2 与 Phase 3 各写了一半，实际是一次性交付的。

### Phase 3 — 图片上传 ✅

`POST/GET/DELETE /api/v1/assets/*`：JPEG/PNG/WebP 白名单、20 MiB 上限、魔数嗅探 + 尺寸、
SHA-256 去重、预签名 URL。迁移 `0002_assets`。（本地存储后端在 Phase 11 追加。）

### Phase 4 — AI Vision ✅

`AIProvider` Protocol（`vision()` / `chat()` / `structured_output()` / `rank_candidates()`）；
三个实现 `MockAIProvider` / `OpenAICompatibleProvider` / `AnthropicProvider`；
`VisionOutput` schema；`recognize_image()` 重试编排 + `AgentTrace` 落库；
`app/ai/observability.py` 的日志脱敏。真实 API 测试由 `RUN_REAL_AI_TESTS=1` 开关控制。

> **订正（重要）**：原稿 Phase 5 要求 schema 用 `extra="forbid"` **+ `strict=True`**。
> 这个处方是错的，已于 2026-09-20 在本文件与代码中一并去掉。`strict=True` 会让 JSON 反序列化
> 出来的**字符串 UUID** 校验失败，导致 `RankingOutput` 每次调用都抛
> "Structured output missing required fields"，真实 LLM 的 Rank 步骤**整条链路静默失效**，
> Agent 只能返回 `state=failed`（详见 `docs/AI.md`）。
> **现行约定：AI 输出 schema 用 `extra="forbid"`，不用 `strict=True`。**

### Phase 5 — 存储推荐 Agent ✅

9 步 pipeline（INTAKE→UNDERSTAND→RETRIEVE→CANDIDATE_GENERATION→FILTER→RANK→DECIDE→VERIFY
→(RETRY)→ANSWER/FAILED），最多 2 次 Retry；13 个工具；9 个 Verifier 纯函数检查；
确定性 ranker（`docs/AGENT.md` §4 权重 + §4.4 打破平局）；
4 个接口（recommend / accept / reject / patch）。迁移 `0003_update_recommendation_status`。

> 原稿把它拆成 Phase 6–9 四个阶段，实际是一次成型的。模块路径也已归位：
> `app/agent/*` → **`app/agents/*`**；`app/agent/rules.py` + `verifier.py` → **`app/verification/`**；
> `app/agent/orchestrator.py` → **`app/agents/pipeline.py`**。

### Phase 6 — 自然语言搜索 ✅

`SearchAgent`（只读）识别意图后分派；支持物品 / 位置 / 存在性 / 列表 / 放置建议 / 存储结构描述
等意图；`POST /api/v1/search` 有状态（`conversations` + `messages`，回放最近 8 轮），
由 `search.v4.md` + `answer.v1.md` 驱动。

> 原稿完全没有「自然语言搜索」这一项 —— 它是开工后加进来的能力，且已成为助手的核心。

### Phase 7 — JWT 认证接口 ✅

`/api/v1/auth/{signup,login,refresh,me}`、`auth_service`、`security`（bcrypt + JWT）。

### Phase 8 — Web 前端 MVP ✅

Next.js 14 App Router + Tailwind。页面：`/`、`/login`、`/signup`、`/home`（+ rooms / storage）、
`/items`（+ 详情 / 新增）、`/recommendations/[id]`、`/assistant`。

> 原稿 Phase 9/11 的验收写「浏览器跑通『**建家** → 录物品 → 看推荐 → 接受』」。
> 「建家」这一步的实现方式变了：没有任何「创建 home」的接口，**注册时自动 provision 一个家**
> （见 P0.1），所以「建家」不再是一个用户动作。

### Phase 9 — 评估 harness ✅

`python -m app.evaluation`，61 个 golden case，产出 `report.json` / `report.csv` / `report.md`。
Mock 与真实 LLM 数字均记录在 `docs/EVALUATION.md`。

### Phase 10 — Web ↔ API 接线 ✅

**读路径**：`app/api/v1/homes.py`（6 条路由）、`GET` 物品牌、`GET /recommendations/{id}`。
**写路径**：`POST /uploads/presign`、`POST/PATCH /items`、`POST /items/{id}/vision`。

> 原稿 Phase 10「物品搜索」的两条验收（`/items/{id}/placements` 时间线、`/items/{id}/candidates`
> 调试端点）均已交付；`P95 ≤ 300ms` 未做压测，**未验证**，不当作已达成。

### Phase 11 — 可切换存储后端 ✅

`STORAGE_BACKEND=local|minio`：`app/storage/{errors,backend}.py` + `app/api/v1/files.py`
（签名 URL 读取，不带 `get_actor`，签名即凭证）。图片改为**内联 data URI** 送给多模态模型
（远端模型无法 fetch `localhost`）。`POST /items/infer` 让模型把表单一次填齐。

### Phase 12 — 去英文 + 助手记忆 ✅

`full_path` 改用中文 `label`；Verifier 的 10 条消息中文化；`recommend.v2.md` 禁止 code/英文；
`/assistant` 变成有状态的（`conversation_id` + `Message` 回放）。

### Phase 13 — 收纳助手「看懂家里布局」 ✅

新增第 8 个意图 `describe_storage` + 纯函数 `build_home_blueprint`，
让「我家有几个柜子 / 有几个房间 / 还有多少空位」这类**结构问题**不再被当成物品搜索。

---

# 下篇 · P0 路线图（唯一在推进的计划）

## P0 是什么

P0 是「**让一个真人第一次用起来，能走完一遍并觉得有用**」的最小闭环。

判定标准不是功能多少，而是：**一个不会收纳的人，注册进来之后，在不看说明书的情况下能不能
把一件东西放对地方，并且知道为什么放那儿。** 因此 P0 只做四个批次，任何一个批次单独上线
都不会把产品弄坏。

> 核心原则的修订见 `AGENTS.md` §3.3（AI 可以**提议**新结构，但必须用户显式确认才落库）。
> 产品定位、核心价值（放 / 理 / 找）、三段旅程、权限模型、使用指引见 `docs/PRD.md`。

## 依赖关系

```
P0.1 真实账号 ✅  ──┐
                    ├──► P0.2 拍照即建模 ✅ ──┬──► P0.3 反向录入 ✅
                    │                         └──► P0.4 闭环 + 讲理由
                    └──► （P0.1 尾巴：token 续期 ✅）
```

P0.2 是硬前提：**没有真实的 slot，推荐、反向录入、闭环全都无从谈起。**
现在这条前提已经成立 —— 全新账号可以在浏览器里从空树搭出第一个可用 slot。
P0.3 又把它反过来用：**放不需要 AI 也能发生**，AI 只负责「不知道放哪」的那一半。

---

## P0.0 — 定位与产品重规划（文档）⏳

**为什么**：定位、三段旅程、权限模型从未落进任何文档 —— 它们只存在于讨论里。

**交付物**

1. `docs/PRD.md` 重写：产品定位、核心价值 **放 · 理 · 找**、三段旅程
   （A 拍照即建模 / B 反向录入 / C 找）、权限模型、使用指引、MVP 边界。
2. 本文件的 P0 路线图（即本节）。

**验收**

- 三段旅程的每一段都能指到具体的 P0.x。
- 每个 P0.x 的验收标准都是**可勾选的事实**，不是形容词。
- 与 `AGENTS.md` §3 核心原则无冲突；有冲突的已显式记录修订理由。

---

## P0.1 — 真实账号 ✅ 已完成

**交付物**：`get_actor` 改为真 JWT（`Authorization: Bearer`）+ 每请求重新校验 `HomeMembership`；
`X-Home-Id` 只是「我在哪个家操作」的选择器，不是凭证；web 会话改为 cookie
（`requireSession()` 服务端 + 客户端组件从服务端父组件拿 session）；
`/login` `/signup`；注册时自动 provision「我的家」。

**验收（全过）**

- [x] 无 token → 401；伪造 `X-User-Id` / `X-Home-Id` → 401
- [x] 合法 token + 别人的 home → **404（不是 403）**
- [x] 注册 → 登录 → `GET /homes` 自动有「我的家」→ 刷新仍在线
- [x] 7 个页面带 cookie 渲染真实种子数据；无 cookie → 307 `/login`

**尾巴（待办）**：`jwt_access_ttl = 3600`，而 web 从不调 `/auth/refresh` ⇒ **一小时后静默掉线**。
修法是在 `session.ts` 的续期点接上 refresh；属于 P0.1，不单列批次。

**附带的两个坑（已修，供后续参考）**

- 种子账号 `demo@home.local` 永远登不进去 —— `EmailStr` 直接拒 special-use TLD（`.local`）。
  已改为 `demo@example.com`。
- `next dev` 在文件由 client 翻成 server 后会保留**腐坏的模块图**，报出指向正确文件的假错误。
  **重启 `next dev` 即可，不要去改代码。**

---

## P0.2 — 拍照即建模 ✅ 已交付（2026-09-22）

**为什么**：此前收纳结构**只能靠 `python -m app.db.seed` 建立** —— 没有任何创建
room / unit / section / slot 的接口。新注册的账号拿到的是一个空树，于是推荐永远
`pre_filter_count == 0`。这是产品当时最大的断点：**用户根本没法把自己的家告诉系统。**

**交付物**

1. ✅ **结构写接口**（`app/api/v1/structure.py` + `app/services/structure_service.py`）：
   rooms / storage-units / sections / slots 的创建、改名、删除；`PATCH /homes/{id}` 改名。
   - 删除有子级的节点 → **409**；删除 slot 时**只要有任何 placement 记录（含已 removed）**
     就 409 —— `item_placements.slot_id` 是 `ondelete="RESTRICT"`，只数 active 会让
     数据库抛 `IntegrityError`（500）。`details` 分开给 `active_count` / `historical_count`。
   - 非成员 → **404**（不是 403）；`PATCH /homes/{id}` 非 owner → 403（全库唯一一处 403）。
   - 枚举字段在边界校验（`room_type="厨房"` → 422，不是 DB CHECK 的 500）。
2. ✅ **AI 提议结构 + 用户确认**（`docs/AGENT.md` §14）：一次 LLM 调用（照片内联成 data URI
   走 vision model）→ 纯函数校验（`app/agents/structure/validate.py`）→ 用户勾选确认 →
   普通写接口逐节点落库。**提议本身绝不落库**，唯一副作用是一行 `AgentTrace`；
   `{}` 空请求走服务端模板，零成本、不调模型。
3. ✅ **Web**：`/home/setup`（`StructureBuilder` = 提议流 + 手动搭建），空状态 CTA 指向它。
   提议的每一步改写都通过 `warnings` 告诉用户（截断 / 丢重复 code / 清类别 / 重名 / 空词表）。

**验收**

- [x] 全新账号能在浏览器里从**空树**建出至少 1 个可用 slot，全程不碰命令行
- [x] 未确认的 AI 提议在数据库中**不存在**（`tests/api/test_structure_proposal_api.py`
      断言四张存储表行数不变、`agent_traces` 恰好 +1）
- [x] 新建出的 slot 能进入推荐候选（实测 `pre_filter_count = 13`，不再是 `state=failed`）
- [x] 基线：**600 passed / 1 skipped**，ruff 32，mypy 20 —— 相对上一批只增测试、不增告警

**落地位置**：后端 `app/{api/v1/structure.py, services/structure_service.py,
services/structure_proposal_service.py, agents/structure/*, schemas/structure.py}`；
prompt `app/agent/prompts/structure.v1.md`；前端 `apps/web/src/app/home/setup/*`。
`AIProvider.structured_output` 新增可选 `image_url`（`docs/AI.md` §2）—— 缺省 `None` 时行为
与改动前逐字节相同，既有调用点零改动。

**本批不做**（留 ⏳）：`POST /homes`、成员管理、`GET /storage-units/{id}` /
`GET /sections/{id}` 详情路由、`structure_proposals` 持久化、完整的结构编辑器。

---

## P0.3 — 反向录入 ✅ 已交付（2026-09-22）

**为什么**：物品**已经在某个地方**时，用户不该被迫走一遍「拍照 → 识别 → 推荐 → 接受」。
已经有明确去向的东西应当能直接落位。这是「无需繁琐录入」的另一半。

**交付物**

1. ✅ **直接落位接口**（`app/api/v1/placements.py` + `app/agents/placement_service.py`）：
   `POST /api/v1/placements` → 201 直写 `ItemPlacement`（`source=user_manual`），
   **不经过 LLM**；`DELETE /api/v1/placements/{id}` → 200 **软关闭**（置 `removed_at`，
   行永不物理删除，历史仍可读）。
2. ✅ **「关旧 + 插新」只有一份实现**：`app/tools/write_tools.py:_create_placement` /
   `close_active_placements`，`save_placement`（AI 路径）与 `place_item`（手动路径）共用。
   原语放在 `tools/` 而不是 `agents/` —— 反过来会让低层工具依赖 agents 包（倒置依赖）。
3. ✅ **Web**：物品列表 / 详情页逐件「放到这里」（放完**不导航**，所以「连续补录多件」成立）；
   独立的 `/items/place`「批量归位」页（先选一格，再勾选多件一次落位）。

**验收**

- [x] 用户能把一件物品直接「放」进某个 slot，`item_placements` 正确落库
      （`tests/api/test_placement_write_api.py`，17 条）
- [x] 物品详情显示当前位置，且**不触发任何 LLM 调用** —— 断言 `agent_traces` 行数不变
- [x] 跨 home 的 item / slot → 404；未知 slot → 404；无凭证 → 401
- [x] 同一物品落两次后**恰好一条 active**（显式数行数，不依赖部分唯一索引 —— SQLite 不强制它）
- [x] 空间树该格 `active_count`：0 → 落位后 1 → 移出后 0
- [x] 重复 `DELETE` → 409；`DELETE` 跨 home → 404
- [x] 基线：**626 passed / 1 skipped**，ruff 32，mypy 20；`tsc --noEmit` + `next lint` 干净

**顺带修的真实 bug**：`accept_recommendation` 建新 `ItemPlacement` 时**不关闭**该物品已有的
active placement，只靠 `uq_item_placements_one_active_per_item` 兜底 —— PG 上 `IntegrityError`
（500），SQLite 上静默产生两条 active 而 `_active_placement_by_item` 用 dict 收敛、后写的悄悄赢。
两条写路径现在共用 `_create_placement`，回归由
`tests/unit/test_placement_service.py::test_accept_closes_previous_active_placement` 守住。

**落地位置**：后端 `app/{api/v1/placements.py, agents/placement_service.py,
tools/write_tools.py, schemas/item.py}`；前端 `apps/web/src/components/placements/*`
（`SlotPicker` / `PlaceItemButton` / `RemovePlacementButton` / `BatchPlaceClient`）+
`apps/web/src/app/items/place/page.tsx`。

**本批不做**（留 ⏳）：后端批量端点（批量页逐条 POST 足够）、`PATCH /placements/{id}`、
手动落位的容量 / 安全校验（那是 AI 路径 verifier 的职责，手动是用户的明确选择）、
手动落位时 supersede `Recommendation`、并发落位的 409 兜底。

**依赖**：P0.2（得先有 slot 可放）✅。

---

## P0.4 — 闭环 + 讲理由 ⏳

**为什么**：推荐出来后，用户的接受 / 拒绝应当让**下一次更准**；并且用户要能看懂
「为什么是这个柜子这一格」。当前偏好没有被回灌，拒绝也没有被记住。

**交付物**

1. **反馈回灌**：接受 → 正偏好；拒绝 → 记录原因并排除该 slot，下次同物品的候选不再包含它。
2. **讲理由**：每条推荐给出可读的中文理由，并在推荐页 / 物品页展示「为什么放这里」。

**验收**

- [ ] 接受某个 slot 后，对同类物品再次推荐，该 slot 的排序**上升**
- [ ] 拒绝某个 slot 后，同物品的候选**不再包含**它
- [ ] 每条候选都有非空的中文理由，且**不含 code / 英文**（Phase 12 已确立的规则）

**依赖**：P0.2（有真实 slot 之后，闭环才有意义）。

---

## 风险登记与应对

| 风险 | 触发条件 | 应对 |
| --- | --- | --- |
| Provider 行为漂移导致 parse 失败 | parse_error 上升 | 把失败样本纳入 golden set；调 prompt / Pydantic schema |
| 推荐接受率低 | 低于 60% | 调 prompt；补强「讲理由」；做用户访谈 |
| Verifier 误拦截高 | 拦截后用户仍接受的占比 > 5% | 软化 hard 规则；增加 evidence 字段 |
| AI 提议结构「太啰嗦」 | 用户确认率低 | 减少单次提议数量；优先提议层 / 格而非整个柜子 |
| 并发落位产生两条 active | 同一物品几乎同时落位 | PG 部分唯一索引抛 `IntegrityError`（500）。单用户 UI 下几乎不可能；要封死就在路由捕 `IntegrityError` 返 409 —— **P0.3 只记录，未实现** |
| Token 成本失控 | 月成本超预算 | 限流；切更便宜模型；缓存空间快照 |

## 节奏

- 一个批次至少 1 次完整 demo（截图 / 录屏）。
- 每个批次结束更新 `docs/EVALUATION.md` 与本文件。
- 任何对核心原则（`AGENTS.md` §3）的偏离，必须在 PR 描述里显式说明。
