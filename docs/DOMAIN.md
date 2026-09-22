# DOMAIN — 领域模型

> 完整定义核心实体、属性、关系与业务规则。表结构见 `docs/DATABASE.md`。
>
> 最后更新：2026-09-22 —— §2.10 补 `ItemPlacement` 的两条写路径（AI accept / 手动落位，
> 共用一个原语）、§4.1 与 §8 订正「删 slot 的判据是任何 placement 记录，不只是 active」。
> （2026-09-21：订正 `Recommendation.status`（`adjusted` → `superseded`）、
> §5 的 schema（类名 `RankingOutput`、上限 3、不加 `strict=True`）、
> §7 的 `AgentTrace.steps` 形状（键名 `state`，取值是 `RecommendationState`，
> 不是 `think/act/observe/...` 那套 ReAct 词汇）。）
>
> **代码是唯一事实来源。** 本文档在描述 schema 时尽量贴出代码原文，但仍可能滞后；
> 冲突时以 `app/models/` 与 `app/ai/provider.py` 为准。

---

## 1. 实体总览

```
User ──< HomeMembership >── Home
                              │
                              ├──< Room
                              │     │
                              │     └──< StorageUnit
                              │                │
                              │                └──< StorageSection
                              │                            │
                              │                            └──< StorageSlot
                              │                                      │
                              │                                      │ (1:N)
                              │                                      ▼
                              ├──< HomeRule                       ItemPlacement >── Item
                              │                                                    │
                              ├──< UserPreference                                  │ (1:N, 历史)
                              │                                                    ▼
                              ├──< Conversation ──< Message              ItemPlacement (历史)
                              │
                              └──< Recommendation ── Item (候选)
                              └──< AgentTrace
```

---

## 2. 实体定义

### 2.1 User（用户）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| email | str | 唯一 |
| password_hash | str | bcrypt |
| display_name | str | 显示名 |
| created_at | timestamptz | |
| updated_at | timestamptz | |

关系：User M:N Home（通过 `HomeMembership`）。

---

### 2.2 HomeMembership（家庭成员关系）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| user_id | UUID | FK → User |
| home_id | UUID | FK → Home |
| role | enum | `owner` / `member` |
| joined_at | timestamptz | |

唯一约束：`(user_id, home_id)`。

---

### 2.3 Home（家）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| name | str | 例 "我的家" |
| owner_id | UUID | FK → User（创建者） |
| timezone | str | IANA tz，默认 Asia/Shanghai |
| created_at | timestamptz | |
| updated_at | timestamptz | |

---

### 2.4 Room（房间）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| home_id | UUID | FK → Home |
| name | str | 例 "主卧"、"厨房" |
| room_type | enum | `bedroom` / `kitchen` / `bathroom` / `study` / `living` / `storage` / `other` |
| sort_order | int | 排序 |
| created_at | timestamptz | |

索引：`(home_id, sort_order)`。

---

### 2.5 StorageUnit（柜子 / 架子 / 抽屉柜）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| room_id | UUID | FK → Room |
| name | str | 例 "衣柜 A" |
| unit_type | enum | `cabinet` / `shelf` / `drawer_cabinet` / `box` / `other` |
| description | str? | |
| sort_order | int | |
| created_at | timestamptz | |

---

### 2.6 StorageSection（层 / 抽屉 / 收纳盒）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| unit_id | UUID | FK → StorageUnit |
| name | str | 例 "第二层"、"抽屉 1"、"收纳盒 A" |
| section_type | enum | `layer` / `drawer` / `box` / `compartment` / `other` |
| sort_order | int | |
| created_at | timestamptz | |

---

### 2.7 StorageSlot（具体一格）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| section_id | UUID | FK → StorageSection |
| code | str | 该 Section 内唯一编号，如 "A-2-3"（可由系统生成也可用户指定） |
| label | str? | 例 "左边第二格" |
| capacity_hint | str? | 自由文本 / JSON 描述容量："small / 衣物 / ≤ 2L" |
| allowed_categories | str[] | 可选，允许存放的物品类别（白名单），空表示不限 |
| sort_order | int | |
| created_at | timestamptz | |

唯一约束：`(section_id, code)`。
重要：**AI 推荐只允许从真实 StorageSlot 中选取，禁止凭空创造。**

---

### 2.8 Item（物品）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| home_id | UUID | FK → Home |
| name | str | 用户给定或 AI 识别 |
| description | str? | 用户补充 |
| category | str? | 一级类别，如 "衣物"、"厨具"、"工具" |
| subcategory | str? | 二级类别 |
| brand | str? | |
| estimated_size | str? | "small" / "medium" / "large" / 尺寸描述 |
| is_sensitive | bool | 是否敏感（药品、贵重物品等） |
| needs_lock | bool | 是否需要上锁 |
| primary_image_id | UUID? | FK → ItemImage |
| created_by | UUID | FK → User |
| created_at | timestamptz | |
| updated_at | timestamptz | |

---

### 2.9 ItemImage（物品图片）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| item_id | UUID | FK → Item |
| object_key | str | MinIO key |
| url | str | 访问 URL（可预签名） |
| width | int? | |
| height | int? | |
| is_primary | bool | |
| created_at | timestamptz | |

---

### 2.10 ItemPlacement（物品摆放记录）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| item_id | UUID | FK → Item |
| slot_id | UUID | FK → StorageSlot |
| placed_at | timestamptz | |
| removed_at | timestamptz? | null = 当前在位 |
| placed_by | UUID | FK → User |
| source | enum | `ai_recommendation` / `user_manual` |
| recommendation_id | UUID? | FK → Recommendation（如果是 AI 推荐落库的；**手动落位为 `NULL`**） |
| note | str? | |

部分唯一索引：`UNIQUE (item_id) WHERE removed_at IS NULL` —— 一个物品同时只能在一个 Slot 中。

**两条写路径，一个原语**（`app/tools/write_tools.py:_create_placement`）：

1. **AI 路径** —— `POST /recommendations/{id}/accept`（`source = ai_recommendation`，
   用户 PATCH 改过则是 `user_manual`）。
2. **手动路径（P0.3 反向录入）** —— `POST /placements`（`source = user_manual`，
   `recommendation_id = NULL`），**不调用任何 LLM**，也**不碰任何 `Recommendation` 行** ——
   一条手动落位并不「解决」一条 AI 建议，用户之后仍可接受 / 拒绝它。

两条路径都会先软关闭该物品已有的 active placement。**"移出"** 同样是软关闭
（`DELETE /placements/{id}` → `removed_at = now()`），**行永不物理删除** ——
历史靠 `removed_at` 而非删除来体现（F-2.3）。

**两条路径都写反馈**（P0.4）：同一次事务里 upsert 一条按类别限定的
`UserPreference`（key = `preferred_slots`），用于下次同类物品的排序；写入只 `flush`，
与落位共用那一次 `commit`。

**「为什么放这里」不在这张表上**：`ItemPlacement` **没有** `reason` 列。API 的
`reason` 字段是**读时拼出来的** —— `recommendation_id` join 回 `Recommendation.candidates`
取该 slot 的 reason（见 `docs/API.md` §6）。手动落位 `recommendation_id = NULL`，
所以恒为空串。这样理由只有一个来源，不会出现行内快照与推荐对不上的情况。

---

### 2.11 Recommendation（推荐记录）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| item_id | UUID | FK → Item |
| agent_trace_id | UUID | FK → AgentTrace |
| candidates | JSONB | 推荐候选 Slot 列表（见 §5 schema）—— **经过完整 9 步 pipeline 后的最终 Top 1~3** |
| pre_filter_count | int | Step 3 候选生成后的候选数（≤ 20），用于可观测性 |
| post_filter_count | int | Step 5 约束过滤后剩余候选数 |
| chosen_slot_id | UUID? | 用户最终接受的位置（落库时回填） |
| status | enum | `pending` / `accepted` / `rejected` / `superseded` |
| created_at | timestamptz | |

**状态机**（`app/db/enums.py:RecommendationStatus`，CHECK 约束见 `docs/DATABASE.md`）：

| 状态 | 含义 |
| --- | --- |
| `pending` | AI 已给出候选，等用户决定。**用户 PATCH 改了 `chosen_slot_id` 之后仍是 `pending`** |
| `accepted` | 用户接受（或其改过的版本）；已生成 `ItemPlacement` |
| `rejected` | 用户否决；什么都没放 |
| `superseded` | 同一物品重新推荐并被接受后，旧的 pending 推荐被作废（保留供审计） |

> ⚠️ **`adjusted` 已不存在。** 早期设计用 `status = 'adjusted'` 表示「用户手动选别的位置」，
> 该值已从 CHECK 约束移除（迁移 `0003_update_recommendation_status`，历史 `adjusted` 回填为
> `accepted`）。现在换位置走 **PATCH + accept**：PATCH 只改 `chosen_slot_id`（状态留在
> `pending`），accept 才落 `ItemPlacement`，且该 placement 记为 `source = user_manual`。
> 这样「用户接受了一个改过的推荐」与「用户接受了原推荐」在数据上不再需要区分状态，
> 差异体现在 placement 的来源上。

`candidates` 顶层 JSON schema：

```json
{
  "candidates": [
    {
      "slot_id": "uuid",
      "confidence": 0.86,
      "reason": "厨房抽屉 1，常用于放置厨房小工具",
      "matched_rules": ["kitchen_for_kitchen_tools"],
      "evidence_item_ids": ["uuid", "uuid"]
    }
  ]
}
```

---

### 2.12 UserPreference（用户偏好）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| user_id | UUID | FK → User |
| home_id | UUID | FK → Home |
| key | str | 例 "default_tool_location" |
| value | JSONB | 值 |
| created_at | timestamptz | |

唯一约束：`(user_id, home_id, key)`。

---

### 2.13 HomeRule（家庭收纳规则）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| home_id | UUID | FK → Home |
| name | str | 规则名 |
| description | str | 自然语言描述 |
| rule_type | enum | `hard` / `soft` |
| scope | JSONB | 见 §6 |
| enabled | bool | |
| created_at | timestamptz | |

`hard` 规则必须被 Verifier 强制满足；`soft` 仅影响推荐打分。

---

### 2.14 Conversation（会话，第二版）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| user_id | UUID | FK → User |
| home_id | UUID | FK → Home |
| item_id | UUID? | FK → Item（如果围绕某件物品） |
| created_at | timestamptz | |

---

### 2.15 Message（消息，第二版）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| conversation_id | UUID | FK → Conversation |
| role | enum | `user` / `assistant` / `tool` |
| content | str | |
| tool_calls | JSONB? | |
| created_at | timestamptz | |

---

### 2.16 AgentTrace（Agent 调用轨迹）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| item_id | UUID? | FK → Item |
| user_id | UUID | FK → User |
| home_id | UUID | FK → Home |
| steps | JSONB | 步骤数组（见 §7） |
| final_status | enum | `success` / `verifier_failed` / `error` |
| total_duration_ms | int | |
| llm_tokens_in | int? | |
| llm_tokens_out | int? | |
| llm_cost_usd | float? | |
| error | str? | |
| created_at | timestamptz | |

每条 `Recommendation` 必关联一条 `AgentTrace`。

---

## 3. 实体关系清单

| 关系 | 类型 | 说明 |
| --- | --- | --- |
| User — Home | M : N | 通过 HomeMembership |
| Home — Room | 1 : N | |
| Room — StorageUnit | 1 : N | |
| StorageUnit — StorageSection | 1 : N | |
| StorageSection — StorageSlot | 1 : N | |
| Home — Item | 1 : N | |
| Item — ItemImage | 1 : N | |
| Item — ItemPlacement | 1 : N（active 唯一） | |
| StorageSlot — ItemPlacement | 1 : N | |
| Item — Recommendation | 1 : N | |
| Recommendation — AgentTrace | N : 1 | |
| User — UserPreference | 1 : N | |
| Home — HomeRule | 1 : N | |
| User — Conversation | 1 : N | |
| Conversation — Message | 1 : N | |

---

## 4. 业务规则

### 4.1 空间模型

- 任何 `StorageSlot` 必须挂在一个 `StorageSection` 下；不允许跳过层级。
- 删除 `StorageUnit` 需校验无 active `ItemPlacement`；否则禁止。
- 删除 `StorageSlot` 需校验**不存在任何 `ItemPlacement` 记录（含已 `removed` 的）**；
  否则 409 —— `item_placements.slot_id` 是 `ondelete="RESTRICT"`，只数 active 会让数据库
  抛 `IntegrityError`（500）。`details` 分开给 `active_count` / `historical_count`。
  （实现见 `app/services/structure_service.py`；`docs/DEVELOPMENT_PLAN.md` P0.2。）

### 4.2 物品

- 一个 Item 同时只能有一个 `ItemPlacement`（active，removed_at = NULL）。
- 删除 Item 需先结束所有 active `ItemPlacement`。
- 物品图片存 MinIO；DB 存 object_key 与访问 URL。

### 4.3 推荐

- `Recommendation.candidates[*].slot_id` 必须指向真实存在的 `StorageSlot`。
- 候选 Slot **必须**经过完整 9 步 pipeline：Vision → Storage Retrieval → Candidate Generation → Constraint Filtering → Ranking → LLM Decision → Verifier → Persist。
- 候选 Slot **必须 ∈** Step 4 (Candidate Generation) 输出的 ≤ 20 个候选，**必须 ∈** Step 5 (Constraint Filtering) 过滤后的 surviving 集合；LLM 不得越界选取。
- 候选 Slot 必须全部通过 Step 8 (Verifier) 的最终安全网校验（白名单 + 存在性 + 硬规则 + 去重 + 数量）。
- Verifier 失败的整批候选不允许写入 `Recommendation`（视为推荐失败，落 `final_status = verifier_failed`）。

### 4.4 规则

- `HomeRule.rule_type = hard` 必须被 Verifier 强制满足；任一 hard 规则被违反则 Verifier 失败。
- `soft` 规则违反不会让 Verifier 失败，但会显著降低 candidate 分数。

### 4.5 偏好

- `UserPreference` 仅在显式引用其 `key` 时参与推荐；默认不强制注入。

---

## 5. Recommendation.candidates JSON Schema（Pydantic）

实际定义在 **`app/ai/provider.py`**（本文档只作说明，代码是唯一事实来源）：

```python
class CandidateSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 刻意 NOT strict，见下
    slot_id: UUID
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=512)
    matched_rules: list[str] = []
    evidence_item_ids: list[UUID] = []

class RankingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[CandidateSlot] = Field(min_length=1, max_length=3)
```

LLM 输出必须能解析为该 schema，失败则重试（最多 2 次）。

**两处订正**：

- 类名是 **`RankingOutput`**，不是 `CandidatesPayload`。
- 上限是 **3**，不是 5 —— 与 `docs/AGENT.md` §7.2「Top 1~3」以及产品口径（给用户 1~3 个
  选择）一致。原稿的 5 与任何一处都对不上。
- **不要加 `strict=True`**：这是拿 LLM 的 JSON 文本校验的，`slot_id` / `evidence_item_ids`
  在 JSON 里是字符串，strict 模式要求真正的 `UUID` 实例、会拒掉它们，表现为一句误导性的
  "missing required fields"（详见 `docs/AI.md` §4.2）。

---

## 6. HomeRule.scope JSON Schema

```python
class RuleScope(BaseModel):
    item_categories: list[str] = []          # 适用物品类别
    slot_room_types: list[str] = []          # 适用房间
    slot_unit_types: list[str] = []          # 适用柜子类型
    requires_attributes: list[str] = []      # 物品必须具有的属性
    forbids_attributes: list[str] = []       # 物品禁止具有的属性
```

---

## 7. AgentTrace.steps JSON Schema

每个元素是 `AgentStepResult.to_json()` 的产物（`app/agents/state.py`）：

```python
# 存进 AgentTrace.steps 的实际形状
{
    "state": "retrieve",          # RecommendationState 的值，见下
    "started_at": "2026-09-21T...",
    "ended_at": "2026-09-21T...",
    "payload": {...},             # 该步的输入/输出（无 LLM 原文）
    "error": null
}
```

> ⚠️ **原稿与代码不符的两处，都已订正：**
>
> 1. 键名是 **`state`**（不是 `step_type`），且**没有** `step_index` / `duration_ms`
>    —— 时长由 `started_at` / `ended_at` 相减得出。
> 2. 取值是 **`RecommendationState`**（`app/agents/state.py`），不是
>    `think / act / observe / verify / retry / answer` 那套 ReAct 词汇：

```python
class RecommendationState(StrEnum):
    INTAKE = "intake"
    UNDERSTAND = "understand"
    RETRIEVE = "retrieve"
    CANDIDATE_GENERATION = "candidate_generation"
    FILTER = "filter"
    RANK = "rank"
    DECIDE = "decide"
    VERIFY = "verify"
    RETRY = "retry"
    ANSWER = "answer"
    FAILED = "failed"
```

> 注意 **`ANSWER` 不产生 step**：pipeline 在 VERIFY 成功后直接返回 `state=ANSWER`，
> 不再 append 一步。断言步骤时应断言 run 的 `state`，而不是「存在一个 answer 步骤」。
>
> `RETRY` 步骤的**条数**就是 `retries_used` —— `GET /recommendations/{recId}` 是靠数
> RETRY 步骤把它算出来的，DB 里没有这一列。

---

## 8. 关键不变量（Invariant）

- 任意时刻，DB 中对每个 Item 最多一个 `ItemPlacement` 满足 `removed_at IS NULL`。
- 任意 `Recommendation.candidates[*].slot_id` ∈ 现存 `StorageSlot.id`。
- 任意 `Recommendation` 都有一条 `AgentTrace` 关联。
- 删除 `StorageSlot` 不允许存在**任何** `ItemPlacement` 记录（active 或已 `removed`）——
  FK 是 `RESTRICT`，历史记录同样是拒绝删除的理由。
- `HomeRule.rule_type = hard` 必须在 Verifier 路径中被强制执行。

---

## 9. 后续扩展点

- 物品图像相似度（pgvector 存 embedding）
- 多 Home 共享一个物品（家庭之间互借）
- 整理任务（chore）实体
- 物品使用频率追踪
- "季节性" 推荐（冬季放被子的位置）
