# DOMAIN — 领域模型

> 完整定义核心实体、属性、关系与业务规则。表结构见 `docs/DATABASE.md`。

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
| recommendation_id | UUID? | FK → Recommendation（如果是 AI 推荐落库的） |
| note | str? | |

部分唯一索引：`UNIQUE (item_id) WHERE removed_at IS NULL` —— 一个物品同时只能在一个 Slot 中。

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
| status | enum | `pending` / `accepted` / `adjusted` / `rejected` |
| created_at | timestamptz | |

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
- 删除 `StorageSlot` 需校验无 active `ItemPlacement`；否则禁止。

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

```python
class CandidateSlot(BaseModel):
    slot_id: UUID
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    matched_rules: list[str] = []
    evidence_item_ids: list[UUID] = []

class CandidatesPayload(BaseModel):
    candidates: list[CandidateSlot] = Field(min_length=1, max_length=5)
```

LLM 输出必须能解析为该 schema，失败则重试（最多 2 次）。

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

```python
class TraceStep(BaseModel):
    step_index: int
    step_type: Literal["think", "act", "observe", "verify", "retry", "answer"]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    payload: dict            # 步骤输入/输出（截断到 4KB）
    error: str | None = None

class TracePayload(BaseModel):
    steps: list[TraceStep]
```

---

## 8. 关键不变量（Invariant）

- 任意时刻，DB 中对每个 Item 最多一个 `ItemPlacement` 满足 `removed_at IS NULL`。
- 任意 `Recommendation.candidates[*].slot_id` ∈ 现存 `StorageSlot.id`。
- 任意 `Recommendation` 都有一条 `AgentTrace` 关联。
- 删除 `StorageSlot` 不允许存在 active `ItemPlacement`。
- `HomeRule.rule_type = hard` 必须在 Verifier 路径中被强制执行。

---

## 9. 后续扩展点

- 物品图像相似度（pgvector 存 embedding）
- 多 Home 共享一个物品（家庭之间互借）
- 整理任务（chore）实体
- 物品使用频率追踪
- "季节性" 推荐（冬季放被子的位置）
