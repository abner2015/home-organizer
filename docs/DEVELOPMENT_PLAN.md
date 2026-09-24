# DEVELOPMENT_PLAN — 阶段化开发计划

> **本文件分两篇，读之前先看这一行：**
>
> - **上篇 · 已交付**（项目史 Phase 1–13）：计划早已执行完。这里记录**实际情况** ——
>   真实产物路径、真实验收结论、真实基线。已完成的部分不再有「任务」，只有事实。
> - **下篇 · P0 路线图**（P0.0–P0.4）：唯一还在推进的计划。每个 P0.x 都带交付物与验收标准。
>
> 最后更新：2026-09-23 —— P0.9 创建新家（POST /homes）交付；**P0 九个批次全部完成**；基线 626 → 669 → 671 → 683 → 708 → 739 → **752**。
> （2026-09-23 补：**P0.A 切换家 UI** 交付，仅前端 0 后端改动；基线不变 752。）
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

- `services/api`：**752 passed / 1 skipped**
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
                    ├──► P0.2 拍照即建模 ✅ ──┬──► P0.3 反向录入 ✅ ──┬──► P0.5 并发 409 ✅
                    │                         └──► P0.4 闭环 + 讲理由 ✅ ┴──► P0.6 撤销排除 ✅
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
手动落位时 supersede `Recommendation`。并发落位的 409 兜底 → **见 P0.5**。

**依赖**：P0.2（得先有 slot 可放）✅。

---

## P0.4 — 闭环 + 讲理由 ✅ 已交付（2026-09-22）

**为什么**：推荐出来后，用户的接受 / 拒绝应当让**下一次更准**；并且用户要能看懂
「为什么是这个柜子这一格」。此前偏好没有被回灌（`UserPreference` 表存在、
`ranking._preference_match` 与 `verification.checks.check_user_preferences` 都在读它，
但**没有任何一行代码写过它**），拒绝也没有被记住（`_step_retrieve` 只读
`get_item_placements`，从不读历史 `Recommendation`）。理由则只到**一条**候选
（`_step_decide` 用 `max(..., key=confidence)` 只留 LLM 的第一名 reason，其余丢弃），
且无 LLM 的候选端点 reason 一律为空串。

**交付物**

1. ✅ **反馈回灌**：接受 / 手动落位 → 正偏好；拒绝 → 该 slot 对该物品**永久**排除。
   - 正偏好按**类别**限定：`user_preferences` 的 `preferred_slots` 存
     `{"<slot_id>": {"category": "<item.category>", "count": <n>}}`，
     仅当物品类别匹配时 `_preference_match` 才 +10（存了空 category 的老行恒定匹配，
     兼容旧形状 `preferred_slot_ids`）。**没做迁移** —— 表已有
     `uq_user_preferences_user_home_key`，`value` 是 `JSONBCompat`，够用。
   - 排除**派生而非存储**：`app/tools/recommendation_tools.py:get_rejected_slot_ids`
     从 `status='rejected'` 且 `chosen_slot_id` 非空的推荐反查（`reject_recommendation`
     只改 status，**不清** `chosen_slot_id`，所以旧行天然是正确的排除集）。零新增存储、
     无迁移、天然「同物品」。FILTER 步过滤掉它们 —— 因为
     `whitelist_slot_ids == known_slot_ids == 候选集`，verifier 白名单自动跟着收缩。
   - 写入是**单事务内的 flush**：`record_preferred_slot`（`app/tools/write_tools.py`）
     只 `flush`，由 accept / `place_item` 既有那一次 `commit` 收口。
     **必须 select-then-update**（`uq_user_preferences_user_home_key` 是普通唯一索引，
     SQLite 也强制），且**必须整体赋新 dict**（`JSONBCompat` 不跟踪原地修改）。
2. ✅ **讲理由**：每条候选都有非空中文理由，推荐页 / 物品页展示。
   - `app/agents/rank_slots` 给**每**行附带确定性理由 `reason`（由 ranker 的分项
     `score_terms` 拼出），推荐路径与无 LLM 的 `GET /items/{id}/candidates` 因此同时点亮。
   - `app/agents/reason.py:build_reason` 是**纯函数**（无 uuid / 时间戳 —— 否则
     `test_candidates_are_deterministic_and_ranked` 的 `second.json() == body` 会破）；
     `full_path` 含 ASCII 时**逐段丢弃**，退到 `room/unit/section` 中文名。
   - LLM 理由过闸 `is_acceptable_llm_reason`：非空、长度 2..512、含 CJK、且**不含任何
     ASCII 字母**。过闸的**全部**候选理由都保留（不再只留第一名），不过闸则确定性理由兜底。
   - 物品页摆放历史每条显示「为什么放这里」：AI 落位有其来源推荐的理由，手动落位为空串
     （`ItemPlacementView.reason`）。
   - 理由的从句按权重降序取第一个成立的，**偏好除外** —— 偏好项命中时**额外**补一句
     「符合你以往的收纳习惯」。`category`（+25，最重的一项）几乎对每条被推荐的 slot
     都成立，严格「第一个成立就返回」会让偏好那句永远轮不到，用户接受过的位置在理由里
     看不出痕迹。这是 P0.4 **真机验收**发现的唯一缺口（其余 23 项一次通过），已修。

**验收**

- [x] 接受某个 slot 后，对**同类别**的另一件物品再次推荐，该 slot 的排序**上升**
      （`tests/unit/test_feedback_loop.py` 断言 `det_score` 恰好 +10 且位次严格上升；
      API 级见 `tests/api/test_recommendation_api.py`）
- [x] 拒绝某个 slot 后，同物品的候选**不再包含**它（重跑 `run_recommendation` 与
      `GET /items/{id}/candidates` 两条路径都断言）
- [x] 每条候选都有非空的中文理由，且**不含 code / 英文**
      （`tests/unit/test_reason.py` + API 级遍历每一条候选断言含 CJK、无 `[A-Za-z]`）
- [x] 拒绝原因是**按物品**的：给物品 X 拒绝的 slot 对物品 Y 仍出现；
      `superseded` 的推荐**不**触发排除
- [x] 候选被全部排除后 `state=failed`，错误文案为「该物品的候选位置均已被你排除」
      而不是误导性的「无符合硬规则的位置」
- [x] 接受后对**同类别**的另一件物品再推荐，该 slot 的**理由里出现**「符合你以往的收纳习惯」
      （`tests/unit/test_reason.py::test_preference_is_narrated_alongside_a_stronger_clause`；
      真机：`儿童退烧药` 对该 slot 45 → 55、rank 2 → 1，理由里出现该句）
- [x] **真机验收**（真 DeepSeek，全新 seed 库，24 项检查）：接受 → 同类物品该 slot 得分 / 位次
      上升且只有该 slot 变化；拒绝 → 两条路径都不再出现该 slot，`candidates[0].audit_note`
      记下原因；全部排除 → 失败文案正确；摆放历史按「有无来源推荐」区分有无理由
- [x] 评测数字**未动**：61/61、Valid Slot 100%、Hard Violation 1.64%、
      Accuracy 59.02%、Top-3 Recall 70.49%（`score_terms` 重构保证
      `deterministic_score` 逐项相同）
- [x] 基线：**669 passed / 1 skipped**（+43），ruff 32，mypy 20；
      `tsc --noEmit` + `next lint` 干净

**已知副作用（必读）**：确定性兜底让理由**永远**满足 `check_reason_consistent`，
于是该 verifier 检查事实上被架空 —— VERIFY 变绿**不再**意味着「LLM 解释得很清楚」，
只意味着「理由合规」。这是本批决策的直接后果，不是 bug。
`tests/unit/test_recommendation_agent.py::test_scenario_9_retry_twice_still_fails`
因此改用一个**幻觉 slot id** 作失败杠杆（原先靠 ASCII 理由触发 verifier 失败，现在会被兜底救回）。

**其他决策**

- **偏好是 per-user 的**（`_step_retrieve` 带 `user_id`）：同一家的另一位成员看不到这份加成。
  这是表的语义决定的，正确，但要知道。
- **排除永久**：只增不减，「撤销排除」的入口记为 ⏳。
- 不做 verifier 检查、不做理由重试 —— 闸门不过就静默回落，不浪费一次 retry。

**落地位置**：后端 `app/{agents/reason.py, agents/ranking.py, agents/pipeline.py,
agents/context.py, agents/placement_service.py, tools/recommendation_tools.py,
tools/write_tools.py, services/recommendation_service.py, api/v1/items.py,
schemas/item.py}`；前端 `apps/web/src/app/items/[id]/page.tsx` +
`apps/web/src/app/recommendations/[id]/RecommendationActions.tsx`。

**本批不做**（留 ⏳）：跨用户偏好共享、`PATCH /placements/{id}`、
把偏好做成带权重列的结构化模型（现在够用）。并发落位的 409 兜底 → **见 P0.5**；撤销排除入口 → **见 P0.6**。

**依赖**：P0.2（有真实 slot 之后，闭环才有意义）✅。

---

## P0.5 — 并发落位 409 兜底 ✅ 已交付（2026-09-22）

**为什么**：P0.3 / P0.4 风险登记都点名。同一物品的两个 `POST /placements`
几乎同时落到后端，两边都跑「关旧 + 插新」：胜者的 `commit` 落在败者的
`close` 与 `flush` 之间，败者的 `flush` 撞上 `uq_item_placements_one_active_per_item`，
PG 抛 `IntegrityError` → 全局 `unhandled_handler` 兜成 **500**。前端看到
"Internal server error"，无法区分 "请重试" 与 "后端坏了"。

**交付物**

1. ✅ **写路径捕 IntegrityError**（`app/tools/write_tools.py:_create_placement`，flush 周围）：
   try / except `sqlalchemy.exc.IntegrityError` → `await db.rollback()` →
   过滤到 `uq_item_placements_one_active_per_item` → 抛 `ConflictError`
   （http 409 / `code=conflict`）；其他 integrity 错误原样上抛。**单点改动覆盖
   manual 与 accept 两条路径**（两者都调 `_create_placement`）。
2. ✅ **辅助函数**（同文件 `_constraint_name_from_integrity_error`）：先看
   `e.orig.diag.constraint_name`（PG psycopg2），回退到 `str(e.orig)` 文本匹配
   （SQLite），都拿不到就返 `None` → 走 "未知完整性错误" 兜底（原样上抛，由
   `unhandled_handler` 走 500 —— 这是我们没见过的索引，不应被静默吞掉）。
3. ✅ **测试**（2 条）：
   - `tests/unit/test_placement_service.py::test_place_item_translates_integrity_error_to_conflict`
   - `tests/api/test_placement_write_api.py::test_concurrent_place_returns_409`
   - 两条都通过 monkeypatch `AsyncSession.flush` 抛
     `IntegrityError("...uq_item_placements_one_active_per_item...")`，
     模拟竞态败者（SQLite 无部分唯一索引，靠 monkeypatch 走同一条代码路径）。

**验收**

- [x] `POST /placements` 遇 `IntegrityError(uq_item_placements_one_active_per_item)` → **409 + `code=conflict`**，DB 里**无新行**（session 同步 rollback）
- [x] 两条新测试通过；基线 **671 passed / 1 skipped**（+2）
- [x] ruff 32、mypy 20 —— **零上升**
- [x] `tsc --noEmit` + `next lint` 干净（无前端改动）
- [x] 未对**未知** integrity 错误静默吞掉 —— 任何识别不到的约束名都原样上抛为 500（避免把别的 bug 隐藏成 409）

**本批不做**（仍留 ⏳）：SQLite 上的并发安全（无部分唯一索引，要 `BEGIN IMMEDIATE` 或应用层锁，**与本批目标不同**）；前端错误提示文案优化（`code=conflict` 已能被现有 `src/lib/api.ts` 错误处理识别为可重试状态，但还没专门做 toast 文案）；自动 retry。

**落地位置**：`app/tools/write_tools.py`（`_create_placement` + `_constraint_name_from_integrity_error`）；
测试：`tests/unit/test_placement_service.py` + `tests/api/test_placement_write_api.py`。

**依赖**：无；纯追加行为，不动任何已交付路径的语义。

---

## P0.6 — 撤销排除入口 ✅ 已交付（2026-09-23）

**为什么**：P0.4 把"拒绝 → slot 对该物品永久排除"作为用户偏好的天然存储。优点是
零新增 schema；缺点是**误拒绝不可恢复** —— 用户点错了、或后来改主意了，slot
永远进不了候选，直到代码直接改 DB。P0.4 自己的「本批不做」段就留了这条 ⏳。
本次实现它。

**交付物**

1. ✅ **新状态 `revoked`**（`app/db/enums.py:RecommendationStatus`）：与 `rejected` 对称。
   `get_rejected_slot_ids` 仍然只过滤 `status='rejected'`；status 一翻，slot 自动
   重新进候选 —— **读路径零代码改动**。
2. ✅ **新迁移**（`alembic/versions/0004_recommendation_revoked.py`）：drop + add
   `ck_recommendations_status`（仿 0003）。无数据迁移（无行起始是 `revoked`）。
   SQLAlchemy 模型 `app/models/recommendation.py:__table_args__` 同步更新（test
   conftest 走 `Base.metadata.create_all` 而不走 Alembic，所以必须更新）。
3. ✅ **`revoke_recommendation` 服务**（`app/agents/placement_service.py`）：新
   `RevokeOutcome` dataclass + 异步函数，跨 home → 404，非 `rejected` 状态 → 409，
   不接受 body（撤销是 un-do，不是新事实）。原有 reject 时写在
   `candidates[0].audit_note` 的拒绝原因**保留**。
4. ✅ **新端点**（`app/api/v1/recommendations.py`）：`POST /api/v1/recommendations/{rec_id}/revoke`。
   空 body，固定返 `{recommendation_id, status: "revoked"}`。
5. ✅ **Pydantic schemas**（`app/schemas/recommendation.py`）：`RevokeRequest` /
   `RevokeResponse`，`RecommendResponse.status` 描述加 `'revoked'`。
6. ✅ **前端**：
   - `apps/web/src/lib/types.ts` —— `RecommendationStatus` 加 `revoked`，新增 `RevokeResponse` 接口。
   - `apps/web/src/lib/api.ts` —— 新方法 `revokeRecommendation(recId, session)`。
   - `apps/web/src/app/recommendations/[id]/page.tsx` —— `STATUS_LABELS` 加
     `revoked: "已撤销排除"`；`rec.status === "rejected"` 时渲染新组件。
   - `apps/web/src/app/recommendations/[id]/RevokeAction.tsx` —— 新客户端组件，
     照搬 `RecommendationActions` 的 Spinner + `extract(err, ...)` 风格，按钮文案
     「撤销排除」+ 提示「撤销后，下一次推荐会把这个位置重新纳入候选」。

**验收**

- [x] `POST /api/v1/recommendations/{rec_id}/revoke` 在 `rejected` 状态 → **200 + status='revoked'**
- [x] pending / accepted / revoked / superseded 状态 → **409 + code='conflict'**
- [x] 跨 home / 未知 id → **404**
- [x] 无凭证 → **401**
- [x] 端到端：recommend → reject → revoke → 再次 recommend，该 slot 重新在候选集
      （`tests/unit/test_placement_service.py::test_revoked_slot_reappears_in_next_recommend`
      断言 `get_rejected_slot_ids` 返空、且新 recommend 的 `chosen_slot_id` 回到
      撤销前的 slot）
- [x] 历史保留：recommendation 行还在，status 是 `'revoked'`，reject 时的
      `audit_note` 完整保留（单元测试断言）
- [x] 基线：**683 passed / 1 skipped**（+12：6 单元 + 6 API）
- [x] ruff 32、mypy 20 —— **零上升**
- [x] `tsc --noEmit` + `next lint` 干净

**本批不做**（留 ⏳ → P0.B 收口）：**批量撤销 + 撤销即自动重跑推荐**
（→ 已交付，见 P0.B）；撤销原因持久化（撤销是 un-do，不是新事实）；
跨用户偏好共享；`PATCH /placements/{id}`。

**落地位置**：后端 `app/{db/enums.py, models/recommendation.py, agents/placement_service.py,
schemas/recommendation.py, api/v1/recommendations.py}` + `alembic/versions/0004_recommendation_revoked.py`；
前端 `apps/web/src/{lib/types.ts, lib/api.ts, app/recommendations/[id]/{page.tsx, RevokeAction.tsx}}`。
测试 `tests/unit/test_placement_service.py` + `tests/api/test_recommendation_api.py`。

**依赖**：无；纯追加行为，不动任何已交付路径的语义（`get_rejected_slot_ids` 零改动）。

---

## P0.7 — PATCH /placements/{id}（改备注 / 换位置）✅ 已交付（2026-09-23）

P0.3 / P0.4 都点名了：手动落位后没有 PATCH 入口 —— 用户改不了备注
（typo / 加「为什么放这里」），也不能保留备注地把物品换到别的 slot。
"放到别处"按钮已经能用 POST /placements 走 close-old + insert-new 流程，
但那条路**会丢旧备注**；而且新备注根本没法写，因为手动放置流程只接受
POST，POST 又必须给 slot_id。

修法：补 `PATCH /api/v1/placements/{id}`，支持：

- **改备注**（`note`）
- **换位置**（`slot_id`，自动关旧 + 插新，**保留**旧备注作为新行 note 的默认值）
- **两者一起改**

### 设计与语义

PATCH 字段语义（用 `model_fields_set` 区分「没传」 vs「传了 null」）：

| 字段 | 不传 | 字符串 | `null` |
| --- | --- | --- | --- |
| `note` | 不变 | 更新 | 清空 |
| `slot_id` | 不动 | 迁位置（关旧 + 插新） | 不允许（保留旧） |

- **同时传** → 迁位置，且新行的 `note` 用请求里的新值
  （**不**保留旧备注 —— 用户主动给了新值就该用新的）。
- **只传 `slot_id`** → 迁位置，新行 `note` 继承旧行的 `note`
  （这样"放到别处"改走 PATCH 就能保留备注了）。
- **空 body（两个字段都没传）→ 400 ValidationFailedError**
  （项目惯例 —— 业务校验用 `ValidationFailedError` 走 400，
  Pydantic schema 校验走 422）。

### move 路径：复用 `_create_placement`

`_create_placement` 已经在 `app/tools/write_tools.py` 实现
"close active + insert new" 的原语 —— 被 manual `place_item` 和 AI
`accept_recommendation` 共用。本次也走它：

- 旧行被 `close_active_placements` 软关闭（`removed_at` set，行保留）
- 新行 `source='user_manual'`、`recommendation_id=None`
- **并发安全同 P0.5**：用 `_create_placement` 自带的
  `IntegrityError → ConflictError` 兜底

不需要再加 try/except。

### 边界

- 对已关闭（`removed_at` 非空）的 placement 做 PATCH → **409**
  （不能改历史 —— 调 `DELETE /placements/{id}` 然后 PATCH 新行）
- 跨 home / 未知 id → **404**
- `slot_id` 跨 home → **404**（`_create_placement` 内部的 `_ensure_slot_in_home` 兜底）
- 同 slot_id 重复 PATCH → **no-op**（视作不变，返 200）

### 验收

- [x] PATCH `/placements/{id}` body `{"note": "..."}` → **200**，DB 行 note 更新
- [x] PATCH `{"note": null}` → **200**，DB 行 note 清空
- [x] PATCH `{"slot_id": new_id}` → **200**，旧行 closed、新行 active、
      source=user_manual、**新行 note = 旧行 note**
- [x] PATCH `{"note": "...", "slot_id": new_id}` → **200**，新行 note 用新值
- [x] PATCH `{}` → **400 ValidationFailedError**（项目惯例）
- [x] PATCH `{"hacker": true}` → **422**（Pydantic extra forbid）
- [x] PATCH 已关闭 placement → **409**
- [x] PATCH 跨 home / 未知 id → **404**
- [x] PATCH 无凭证 → **401**
- [x] 旧行软关闭而非物理删除（历史保留）
- [x] e2e：`place_item` → PATCH 改 slot → 1 active + 1 closed
      （`tests/api/test_placement_write_api.py::test_patch_placement_moves_slot` 断言）
- [x] 基线：**683 → 708 passed / 1 skipped**（+25：12 单元 + 13 API）
- [x] ruff 32、mypy 20 —— **零上升**
- [x] `tsc --noEmit` + `next lint` 干净

### 落地位置

后端：

- `app/schemas/item.py` —— 新增 `PlacementUpdateRequest`（`note` + `slot_id` 都 optional，`extra="forbid"`）
- `app/agents/placement_service.py` —— 新增 `_NoChangeType` 单例哨兵 + `update_placement`
- `app/api/v1/placements.py` —— 新增 `PATCH /{placement_id}` 路由，422/400 分流
- 模块 docstring 顶部从 "Two routes" 更新为 "Three routes"

前端：

- `apps/web/src/lib/types.ts` —— 新增 `PlacementUpdateBody`
- `apps/web/src/lib/api.ts` —— 新增 `updatePlacement`
- `apps/web/src/components/placements/EditPlacementNote.tsx` —— 新组件，照搬
  `RevokeAction` 的 Spinner + `extract(err, ...)` 风格，inline 编辑当前 active
  placement 的备注；空字符串 → `null`（清空）
- `apps/web/src/app/items/[id]/page.tsx` —— 在「进行中」placement 行后加
  `<EditPlacementNote>`，把 `data.placements.find(p => p.removed_at === null)`
  的 id + note 传下去

测试：

- `tests/unit/test_placement_service.py` —— 12 条新增
  （`# --------------------------------------------------------------- update (P0.7)` section）
- `tests/api/test_placement_write_api.py` —— 13 条新增
  （`# ----------------------------------------------------------------------- patch` section）

### 复用

- `app/tools/write_tools.py:_create_placement` —— move 路径
  （**自动获得 P0.5 的 409 兜底**）
- `app/tools/write_tools.py:close_active_placements` —— 旧行软关闭
- `app/tools/write_tools.py:record_preferred_slot` —— move 算正反馈
- `app/agents/placement_service.py:_load_placement_in_home` —— 404
- `app/core/exceptions.py:{ConflictError, NotFoundError, ValidationFailedError}` —— 状态码
- `app/api/v1/placements.py:{_slot_path, _placement_view}` —— 响应装配
- `apps/web/src/components/RevokeAction.tsx` 模式 —— 新组件的 Spinner + extract

### 一个小坑

`ValidationFailedError` 映射到 **400**（项目惯例 —— 见
`app/core/exceptions.py:61-64`），不是 422。422 只用于 FastAPI 的
Pydantic RequestValidationError（schema 校验）。空 body 走的是路由自己的
业务校验，**正确状态码是 400**，不是 plan 里写的 422。
（`tests/api/test_placement_write_api.py::test_patch_placement_no_fields_is_400`）
extra field 走 Pydantic → 422（`test_patch_placement_extra_field_is_422`）。

### 本批不做

- 不做"批量 PATCH"（一次改多条 placement）—— 用批量页逐条 POST 已经覆盖
- 不动"放到别处"按钮（继续走 POST /placements；本批不切换到 PATCH —— 避免改写既有 UI）
- 不做 placement 的"创建时间 / 来源"等只读字段的 PATCH
- 不加 reason 字段（reason 来源是 AI 推荐的 derived 数据，不该用户手填）
- 不做并发 retry（沿用 P0.5 的 409 兜底）

---

## P0.8 — 成员管理 / 多用户共享家 ✅ 已交付（2026-09-23）

P0 走完七个批次时，"家"仍然是单人体验 —— 注册账号时被 seed
`我的家` + OWNER membership，**就这一个成员**。整套数据模型是
多人的（`HomeMembership` 表 + `HomeRole` enum 都已存在），但
API 没有任何路径把第二个人拉进来。P0.2 的 "本批不做" 就留了这条 ⏳；
P0.4 又顺手补了一句"偏好是 per-user 的"——那个限制今天**先**
是 feature，但「同家不同人」必须先打通。

修法：补一个最小可用的成员管理流 —— **按邮箱加已有用户**、
**改角色**、**移除**。不做邮件邀请（零 SMTP 基础设施不值得为 demo 加）。

### 4 个路由（挂在 `app/api/v1/homes.py`）

- `GET    /homes/{id}/members` —— **任何成员**都能看名单（不只是 owner，
  便于发现"谁和我共用一个家"）
- `POST   /homes/{id}/members` body `{email, role}` —— **owner only**
  - email 对应 User 存在 → 200 + 新成员视图
  - email 对应 User 不存在 → **404** `code=not_found`
    「该邮箱还没注册账号」（不发邮件，不存邀请 token）
  - 重复加 → **409** `code=conflict`（唯一索引命中）
- `PATCH  /homes/{id}/members/{user_id}` body `{role}` —— **owner only**
- `DELETE /homes/{id}/members/{user_id}` —— **owner only**

### 错误码惯例

| 情形 | 状态 | code | 说明 |
| --- | --- | --- | --- |
| caller 不是 home 成员 | 404 | not_found | 项目惯例，不泄露"这个家存在" |
| caller 是 member 不是 owner（管理类操作） | 403 | forbidden | **本项目仅此一处允许 403 在 home 内部**（另一处是 PATCH /homes/{id} 非 owner） |
| email 对应 User 不存在 | 404 | not_found | "该邮箱还没注册账号" |
| 已经是成员（重复加） | 409 | conflict | |
| 试图降级 / 移除最后一个 owner | 409 | conflict | "至少需要保留一个 owner" |
| role 字段不在枚举 | 422 | validation_error | Pydantic extra=forbid + 字面量 |

### 最后一个 owner 保护

`change_role` 和 `remove_member` 都先数一下 `HomeMembership.role == 'owner'`
的行数；≤ 1 时拒绝操作。这是 P0 的"零自愈"前提：
现在没有 `POST /homes`，如果最后一个 owner 被降级 / 移除，
家就再也找不到 admin 入口了——所以这个 guard 是真的必要，不是 over-engineer。

### 复用 / 不动

- **完全复用**：`HomeMembership` 模型 + `HomeRole` enum + `get_actor`
  + `ensure_member` —— **不需要新表 / 新迁移**。
- 业务逻辑在新的 `app/services/membership_service.py`（`list_members` /
  `invite_member` / `change_role` / `remove_member` + `build_member_view` 投影）。
- 路由都在已有 `app/api/v1/homes.py` 上加，不另起 router —— 一个家就是
  一个 membership boundary，分开路由只是把 imports 挪位置。

### 前端

- `apps/web/src/lib/types.ts` —— 新增 `Member` / `MemberInviteBody` / `MemberUpdateBody`
- `apps/web/src/lib/api.ts` —— 新增 4 个方法：
  `listHomeMembers` / `inviteHomeMember` / `updateHomeMember` / `removeHomeMember`
- `apps/web/src/app/home/[id]/members/page.tsx` —— **新页面**：
  server component 渲染列表，client 子组件管 invite / role / remove 三个表单。
  非 owner 看到只读视图 + 「只有 owner 可以邀请 / 调整成员」提示。
  「该邮箱还没注册账号」识别 404 后内联提示在 email input 下面；
  「至少需要保留一个 owner」识别 409 后用顶部红色 toast。
- `apps/web/src/app/home/page.tsx` —— 在 home 概览加「管理成员 →」链接，
  **仅 owner 可见**（用 `/auth/me` 比对 `home.owner_id`）

### 验收

- [x] `GET /homes/{id}/members` → 200，所有成员
      (user_id, display_name, email, role, joined_at)
- [x] `POST /homes/{id}/members {email, role}` 已存在用户 → 200；
      不存在 → 404「该邮箱还没注册账号」
- [x] `PATCH /homes/{id}/members/{user_id} {role}` → 200；
      最后一个 owner 降级 → 409「至少需要保留一个 owner」
- [x] `DELETE /homes/{id}/members/{user_id}` → 200；
      最后一个 owner 移除 → 409
- [x] 非 owner 调管理类 → 403；非成员 → 404；无凭证 → 401
- [x] role 字段不在枚举 → 422
- [x] Web：owner 能在 `/home/{id}/members` 邀请、改角色、移除，看到错误提示
- [x] 基线：**708 → 739 passed / 1 skipped**
      （+31：14 单元 + 17 API）
- [x] ruff 32、mypy 20 —— **零上升**
- [x] `tsc --noEmit` + `next lint` 干净

### 本批不做

- **不做邮件邀请 / SMTP**（零基础设施，不为 demo 引入）
- **不做邀请 token / 一次性链接**（无邮件系统支撑）
- **不做 owner 转让**（"最后一个 owner 不能被降级"已覆盖大部分需求；
  想换主人先邀请新 owner 再降级自己）
- **不做跨用户偏好共享**（**单独批次** —— P0.4 ⏳ 留的口子，
  那是个独立可测的 feature，混进来会把本批不可逆）
- **不做"加入多家"流程优化**（`POST /homes` 单独批次 —— 现在没有）
- **不做头像上传** / **不做操作历史审计** / **不做"成员能否上传"权限细分**
  （owner 全权、member 全权，足够 demo）

### 一个测试细节

`_make_user` 测试 helper 必须按 `auth_service.signup` 的方式 normalize email
（strip + lower）—— 否则「该邮箱还没注册账号」测试在插入 user 时
保留大小写，invite 时又 normalize 一次，找不到 → 误报 404。
Signup 是 lower+strip，invite 同样 lower+strip，**两次都一致**就匹配。

另一个细节：401 测试必须带 `X-Home-Id` 但**不**带 `Authorization`。
完全没 headers 时 FastAPI 的 header 校验先抛 422，不会走到 `get_actor`。
要触发「凭证缺失」分支就得让 header 校验过、auth 校验不过。

---

## P0.9 — `POST /homes` 创建新家 ✅ 已交付（2026-09-23）

### Context

进一个家的路径只有「注册时自动 provision 一个『我的家』」。`GET /homes`
已经列出当前用户所属的所有 home —— 数据模型**早就**支持多 home，只是没
HTTP 入口。P0.2 / P0.8 的「本批不做」段都把这条留着 ⏳。

后果：多家庭用户（老家 / 新家 / 工作室）没法在 UI 上加第二个家，只能改 DB；
owner 转让 / 跨家共享体验都被这条卡住。

修法：补 `POST /homes` 一个最小可用入口 —— **Bearer + name + 可选 timezone**
→ 201 + 新 `HomeView`，调用者自动 OWNER。**不**做切换家 UI（沿用 `GET /homes`
现有 read 路径）、不做 owner 转让（单独批次）、不做跨用户偏好共享（单独批次）。

### 接口

```
POST /api/v1/homes
Authorization: Bearer <access_token>     ← get_current_user，不要 X-Home-Id
Content-Type: application/json
{ "name": "老家", "timezone": "Asia/Shanghai" | null }   ← timezone 可选
```

| 状态 | code | 含义 |
| --- | --- | --- |
| **201** | — | 新 `HomeView`（含 `member_count=1, item_count=0, rule_count=0`） |
| 401 | unauthenticated | 没 Bearer / token 无效 / user 不存在 |
| 422 | validation_error | `name` 空 / 超 100 / `extra="forbid"` 命中 |
| 400 | validation_error | `name.strip() == ""`（业务校验，按 P0.7 惯例走 `ValidationFailedError`） |

### 数据 & 复用

- `Home` 表已存在：`name String(100)`、`owner_id FK users.id (RESTRICT)`、
  `timezone String(64) default 'Asia/Shanghai'`。**无需迁移**。
- **抽出 `home_service.create_home_for(db, *, owner_id, name, timezone=None) -> Home`**。
  P0.2 早就该抽的：`signup` 的内联 `Home + HomeMembership` 逻辑改走这个函数，
  **单一来源**。`test_signup_provisions_exactly_one_home` 继续通过 —— 行为字节级等价。
- **不做**：切换家 UI（**已交付 — 见 P0.A**）、owner 转让、跨用户偏好共享、
  邮件邀请、「带模板创建」（一上来附赠客厅 / 主卧 —— `signup` 也没附赠，
  保持一致）。

### 落地位置

后端 `app/services/home_service.py`（NEW）、`app/schemas/home.py:HomeCreateRequest`、
`app/api/v1/homes.py:create_home`、`app/services/auth_service.signup`（改用新 primitive）。
前端 `apps/web/src/app/home/new/{page,CreateHomeForm}.tsx`（NEW）、「+ 新家」链接
在 `apps/web/src/app/home/page.tsx`。新增测试 `tests/unit/test_home_service.py`（5 条）+
`tests/api/test_homes_api.py` +8 条。

### 验收

- [x] `POST /homes {"name":"老家"}` 无 `X-Home-Id` → 201 + HomeView（含 `id, name, timezone, owner_id=caller, member_count=1`）
- [x] `POST /homes {"name":" 老家 "}` → `name == "老家"`（strip 后）
- [x] `POST /homes {"name":"   "}` → 400 `name 不能为空`
- [x] `POST /homes {"name":"x" * 101}` → 422
- [x] `POST /homes {"name":"x", "is_admin": true}` → 422（extra=forbid）
- [x] 无 Bearer → 401
- [x] `GET /homes` 返回 2 行（已有 + 新建）
- [x] Web：在 `/home` 看到 `+ 新家` 链接 → 进 `/home/new` → 提交 → 跳 `/home/setup`
- [x] 旧测试全过（`signup` 行为不变）
- [x] 基线：**739 → 752 passed / 1 skipped**（+13：5 单元 + 8 API）
- [x] ruff 32、mypy 20 —— 零上升
- [x] `tsc --noEmit` + `next lint` 干净

---

## P0.A — 切换家 UI（AppShell 顶部 dropdown）✅ 已交付（2026-09-23）

### Context

P0.9 补了 `POST /homes`，能建第二个家；但 `homeId` cookie 写死后没 UI 切入口。
多 home 用户卡在最后一公里 —— 建了家切不回去，所有页面按错的
`X-Home-Id` 取数据。

修法：纯前端。在 `AppShell` 顶部右侧加一个 dropdown：显示当前 active 家名 +
用户全部家列表；点击切换 → `setSession({...current, homeId: newId})` 重写
cookie + `router.refresh()` 让 server component 用新 X-Home-Id 重渲染。

**零后端改动、零迁移、零新增 pytest** —— cookie 写读已被 `auth-and-session.md`
里的测试覆盖。

### 设计要点

- **位置**：`apps/web/src/components/AppShell.tsx` 头部右侧，在 user button
  **之前**插入。桌面 + 移动端**都显示**（与 user button 的
  `hidden ... sm:flex` 反着来 —— 跨家切换是高频入口，不能藏）。
- **数据**：组件 mount 时 `api.listHomes({token})` 拿用户全部家。
- **切家**：`setSession({...current, homeId: newId})` → `router.refresh()`。
  cookie 改变不自动触发 server 重渲染，必须显式 `refresh()`。
- **单家场景**：dropdown 显示当前家名 + 「+ 新家」入口（列表只有 1 项，
  点击切回自己 = noop）。
- **空 / 加载 / 失败**：分别显示「加载中…」「加载失败，点重试」「还没有家」。
- **关闭**：点击 dropdown 外部 + ESC 键（标准 click-outside + keydown）。
- **可访问性**：`aria-haspopup="listbox"`、`aria-expanded={open}`、
  当前 active 项 `aria-selected="true"`。

### 落地位置

- 新增 `apps/web/src/components/HomeSwitcher.tsx`（约 220 行，含两个 inline
  图标 `HouseIcon` / `ChevronDown`）。
- 修改 `apps/web/src/components/AppShell.tsx`：`+1` 行 import + `+1` 行 JSX。

### 验收

- [x] AppShell 顶部右侧 dropdown 可见，桌面 + 移动端均显示
- [x] 单家场景下 dropdown 显示当前家名 + 「+ 新家」入口
- [x] 多家场景下 dropdown 列出全部家，当前 active 高亮（带「当前」徽章）
- [x] 点击另一个家 → dropdown 关闭 → `router.refresh()` 触发 server component
      重渲染（无白屏闪烁）
- [x] 点击 dropdown 外部 / ESC → 关闭
- [x] 「+ 新家」链接跳 `/home/new`
- [x] `apps/web/src/app/home/page.tsx` 的「+ 新家」链接保留
      （home 概览上下文相关的 CTA，与 dropdown 不冲突）
- [x] `npx tsc --noEmit` 干净
- [x] `npx next lint` 干净
- [x] 基线 **752 passed / 1 skipped**（无变化 —— 纯前端）
- [x] ruff 32 / mypy 20（无变化）

### 本批不做

- **底部 nav 不动**（已经 5 列太挤，跨家切换这种「低频但重要」操作放顶部更合理）
- **不**显示每个家的物品数（dropdown 太长不好读；只显示成员数）
- **不**做「设为默认家」（cookie 已经是默认）
- **不**做删除家（P1+，需要 owner + cascade 思考）
- **不**做键盘快捷键（Cmd/Ctrl+K 之类的命令面板 —— 等 P1+ 思考）

---

## P0.B — 批量撤销 + 同步重跑推荐 ✅ 已交付（2026-09-24）

P0.6 收尾时自己的「本批不做」挂着两条 ⏳：**批量撤销**（一个 item 一次性撤销所有排除）和**撤销即自动重跑推荐**（用户决定时机）。两条都痛：物品被多 slot 拒绝后要循环点 N 次撤销，撤销完还得再点一次「再推荐一次」。

修法：补 `POST /api/v1/recommendations/bulk-revoke` —— 请求带
`recommendation_ids[]` + `auto_rerun: bool`，响应分三个区：
`revoked[]` / `rerun_results[]` / `errors[]`。**同步重跑**：成功撤销后立即
`await run_recommendation()` 给每件受影响物品（用户已确认走同步方案）。

前端：`/items/{id}` 详情页加「已被排除的位置」区块，列出该物品的
`rejected` 推荐；区块底部放「撤销全部排除」按钮 → 调 bulk-revoke +
`auto_rerun=true` → 页面 `router.refresh()` 让新推荐可见。

### 接口

```
POST /api/v1/recommendations/bulk-revoke
Authorization: Bearer <token>
X-Home-Id: <home_uuid>

{
  "recommendation_ids": ["uuid", ...],   // 1..50
  "auto_rerun": true                     // 默认 false
}

→ 200 OK
{
  "revoked":         [{"recommendation_id", "status": "revoked"}, ...],
  "rerun_results":    [{"item_id", "new_recommendation_id", "state",
                       "chosen_slot_id", "candidates[<=3]"}, ...],
  "errors":           [{"recommendation_id"|"item_id", "code",
                        "message"}, ...]
}
```

| 状态 | 含义 |
| --- | --- |
| **200** | 永远返 200；per-item 失败入 `errors[]` |
| 401 | unauthenticated |
| 422 | `recommendation_ids` 为空 / 超 50 / `extra="forbid"` 命中 |

`bulk-revoke` 路径整体永远不返 4xx —— per-rec 的 not_found / conflict
都入 `errors[]`，**包括**「rec 已被撤销」这种 conflict（避免半成功状态）。

### 关键实现点

- **两阶段执行**（`app/agents/placement_service.py:bulk_revoke`）：
  Phase 1 顺序 `revoke_recommendation` 每条 rec，Phase 2 仅在
  `auto_rerun=True` 且至少一条撤销成功时跑 `run_recommendation` 给
  每件受影响物品。
- **每条原子提交**：`revoke_recommendation` / `run_recommendation` 各自
  self-commit（placement_service:401, recommendation_service:305）—— bulk 整体
  不需额外 commit，**部分成功是正常的**：成功的撤销不会被失败的 rerun 回滚。
- **`AIProviderError` 单捕**：rerun 阶段 provider 抛错入 `errors[].code='ai_error'`，
  不冒到顶层 503。
- **新读端点** `GET /api/v1/items/{item_id}/recommendations?status=`：跨 home → 404，
  无凭证 → 401；项目惯例。
- **新前端块**：`apps/web/src/components/items/RejectedRecsList.tsx`
  + 物品详情页的 server-side `loadRejectedRecs` 调用。

### 验收

- [x] `POST /recommendations/bulk-revoke` 一次撤销 3 条 rejected rec → 200, revoked=[3], errors=[]
- [x] `auto_rerun=true` → 同响应 `rerun_results[]` 包含每件受影响物品的新推荐
- [x] 部分失败（not_found / conflict）→ `errors[]` 收集，**不影响**成功项
- [x] 空数组 → 422；超 50 → 422；extra 字段 → 422
- [x] 无凭证 → 401
- [x] 跨 home rec → `errors[].code='not_found'`（不是顶层 404）
- [x] pending rec → `errors[].code='conflict'`（不是顶层 409）
- [x] `GET /items/{id}/recommendations?status=rejected` → 列表按 created_at desc
- [x] 前端：物品页显示「已被排除的位置」+「撤销全部排除」按钮；点击 →
      bulk-revoke + auto_rerun=true → 页面 `router.refresh()` 让新候选可见
- [x] 基线 **752 → 765 passed / 1 skipped**（+13：4 单元 + 6 API bulk + 3 API list）
- [x] ruff 32 / mypy 20 / tsc / next lint 干净

### 本批不做

- **不**做撤销即自动重跑的「异步」版本（用户已选同步；生产环境异步走
  FastAPI BackgroundTasks，单独批次）
- **不**做撤销历史页面（单个 rec 的 status 在 `GET /recommendations/{id}` 已经能看到）
- **不**做「撤销全部」按物品（`bulk_revoke` 已通过 `recommendation_ids` 实现等价
  功能 —— 客户端传同一物品的 N 条 rec 即可）
- **不**做撤销原因持久化（撤销是 un-do，不是新事实 —— P0.6 决策）

### 落地位置

后端：

- `app/schemas/recommendation.py` —— 新增 5 个 schema（`BulkRevokeRequest` /
  `BulkRevokeRevokedItem` / `BulkRevokeRerunItem` / `BulkRevokeError` /
  `BulkRevokeResponse`），`__all__` 更新
- `app/agents/placement_service.py` —— 新增 `BulkRevokeOutcome` dataclass +
  `bulk_revoke()` 两阶段函数 + TYPE_CHECKING import 防循环依赖
- `app/services/recommendation_service.py` —— 新增 `list_recommendations_for_item()`
- `app/api/v1/recommendations.py` —— 新增 `bulk_revoke_endpoint` 路由
- `app/api/v1/items.py` —— 新增 `list_item_recommendations` 路由（`?status=` query）

前端：

- `apps/web/src/lib/types.ts` —— `BulkRevokeRequest/Response/RevokedItem/RerunItem/Error` +
  `ItemRecommendationRow` / `ItemRecommendationsResponse`
- `apps/web/src/lib/api.ts` —— `bulkRevokeRecommendations` + `listItemRecommendations`
- `apps/web/src/components/items/RejectedRecsList.tsx` —— 新客户端组件，列出 rejected
  recs + 「撤销全部排除（n）并重跑推荐」按钮
- `apps/web/src/app/items/[id]/page.tsx` —— server component fetch rejected_recs +
  slot_paths map，传给 `RejectedRecsList` 客户端组件

测试：

- `tests/unit/test_placement_service.py` —— 4 条 `bulk_revoke` 单元测试
- `tests/api/test_recommendation_api.py` —— 6 条 `bulk_revoke` API 测试
- `tests/api/test_items_read_api.py` —— 3 条 `list_item_recommendations` API 测试

---

## 风险登记与应对

| 风险 | 触发条件 | 应对 |
| --- | --- | --- |
| Provider 行为漂移导致 parse 失败 | parse_error 上升 | 把失败样本纳入 golden set；调 prompt / Pydantic schema |
| 推荐接受率低 | 低于 60% | 调 prompt；补强「讲理由」；做用户访谈 |
| Verifier 误拦截高 | 拦截后用户仍接受的占比 > 5% | 软化 hard 规则；增加 evidence 字段 |
| AI 提议结构「太啰嗦」 | 用户确认率低 | 减少单次提议数量；优先提议层 / 格而非整个柜子 |
| 并发落位产生两条 active | 同一物品几乎同时落位 | PG 部分唯一索引抛 `IntegrityError`（500）。单用户 UI 下几乎不可能；要封死就在路由捕 `IntegrityError` 返 409 —— **已实现**：`_create_placement` 在 `flush` 周围捕 `IntegrityError`，过滤到 `uq_item_placements_one_active_per_item` → `ConflictError`（409），session 同步 rollback；2 条新测试（`tests/unit/test_placement_service.py::test_place_item_translates_integrity_error_to_conflict` + `tests/api/test_placement_write_api.py::test_concurrent_place_returns_409`），基线 669 → 671。SQLite 上的并发安全不在本批范围（无部分唯一索引）。 |
| Token 成本失控 | 月成本超预算 | 限流；切更便宜模型；缓存空间快照 |

## 节奏

- 一个批次至少 1 次完整 demo（截图 / 录屏）。
- 每个批次结束更新 `docs/EVALUATION.md` 与本文件。
- 任何对核心原则（`AGENTS.md` §3）的偏离，必须在 PR 描述里显式说明。
