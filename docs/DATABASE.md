# DATABASE — 数据库设计

> PostgreSQL 16。所有表使用 `uuid` 主键（`gen_random_uuid()`，需要 `pgcrypto` 扩展）、`created_at` / `updated_at` 时间戳。所有变更走 Alembic。
>
> 最后更新：2026-09-22 —— §6 不变量表：删 slot 的判据是「任何 placement（含已 removed）」；
> 补 SQLite 不强制部分唯一索引的警告（P0.3）。
> （2026-09-21：订正 §1「枚举用 PostgreSQL ENUM」（实际是 text + CHECK）、
> §3.11 `recommendations.status` 的 CHECK（`adjusted` → `superseded`，迁移 `0003`）。）
>
> 已落地的迁移：`0001_initial_schema` / `0002_assets` / `0003_update_recommendation_status`。

---

## 1. 通用约定

- **主键**：UUID，默认 `gen_random_uuid()`。
- **时间戳**：`created_at TIMESTAMPTZ NOT NULL DEFAULT now()`，`updated_at` 由 SQLAlchemy onupdate 维护。
- **软删**：本设计**不使用**软删。删除约束在应用层（Service 层校验依赖关系）。
- **枚举**：**不用 PostgreSQL `ENUM` 类型**，而是 `text` + `CHECK` 约束。值集中定义在
  `app/db/enums.py`（`StrEnum`），它是 SQLAlchemy 列类型、Pydantic schema、CHECK 约束三者的
  共同来源。新增枚举值必须**同时**改 `enums.py`、对应 Alembic 迁移的 CHECK 字符串、
  以及模型里手写的 CHECK（如 `app/models/recommendation.py:27`）——漏改任何一处，
  测试会通过而生产会 500。
- **命名**：表名复数 snake_case；列名 snake_case；外键 `<resource>_id`；索引 `ix_<table>_<col>`。
- **字符集 / 排序**：UTF-8 / `en_US.utf8`（如对中文排序有要求再调整 collation）。

---

## 2. ER 概览

```
users ──< home_memberships >── homes
                                  │
                                  ├──< rooms ──< storage_units ──< storage_sections ──< storage_slots
                                  │                                                            │
                                  ├──< items ──< item_images                                    │
                                  │       │                                                    │
                                  │       └──────< item_placements >───────────────────────────┘
                                  │
                                  ├──< home_rules
                                  ├──< user_preferences
                                  ├──< conversations ──< messages
                                  ├──< recommendations ── agent_traces
                                  └──< agent_traces
```

---

## 3. 表结构

### 3.1 users

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| email | text | NOT NULL, UNIQUE | 登录用 |
| password_hash | text | NOT NULL | bcrypt |
| display_name | text | NOT NULL | |
| created_at | timestamptz | NOT NULL, default now() | |
| updated_at | timestamptz | NOT NULL, default now() | |

索引：`UNIQUE (email)`。

---

### 3.2 home_memberships

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| user_id | uuid | NOT NULL, FK → users.id ON DELETE CASCADE | |
| home_id | uuid | NOT NULL, FK → homes.id ON DELETE CASCADE | |
| role | text | NOT NULL, CHECK in ('owner','member') | |
| joined_at | timestamptz | NOT NULL, default now() | |

索引：
- `UNIQUE (user_id, home_id)`
- `ix_home_memberships_home_id`

---

### 3.3 homes

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| name | text | NOT NULL | |
| owner_id | uuid | NOT NULL, FK → users.id | 创建者 |
| timezone | text | NOT NULL, default 'Asia/Shanghai' | |
| created_at | timestamptz | NOT NULL, default now() | |
| updated_at | timestamptz | NOT NULL, default now() | |

索引：`ix_homes_owner_id`。

---

### 3.4 rooms

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| home_id | uuid | NOT NULL, FK → homes.id ON DELETE CASCADE | |
| name | text | NOT NULL | |
| room_type | text | NOT NULL, CHECK in ('bedroom','kitchen','bathroom','study','living','storage','other') | |
| sort_order | int | NOT NULL, default 0 | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：`ix_rooms_home_id_sort (home_id, sort_order)`。

---

### 3.5 storage_units

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| room_id | uuid | NOT NULL, FK → rooms.id ON DELETE CASCADE | |
| name | text | NOT NULL | |
| unit_type | text | NOT NULL, CHECK in ('cabinet','shelf','drawer_cabinet','box','other') | |
| description | text | NULL | |
| sort_order | int | NOT NULL, default 0 | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：`ix_storage_units_room_id_sort (room_id, sort_order)`。

---

### 3.6 storage_sections

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| unit_id | uuid | NOT NULL, FK → storage_units.id ON DELETE CASCADE | |
| name | text | NOT NULL | |
| section_type | text | NOT NULL, CHECK in ('layer','drawer','box','compartment','other') | |
| sort_order | int | NOT NULL, default 0 | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：`ix_storage_sections_unit_id_sort (unit_id, sort_order)`。

---

### 3.7 storage_slots

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| section_id | uuid | NOT NULL, FK → storage_sections.id ON DELETE CASCADE | |
| code | text | NOT NULL | section 内唯一编号 |
| label | text | NULL | |
| capacity_hint | text | NULL | 自由文本 |
| allowed_categories | text[] | NOT NULL, default '{}' | 白名单 |
| sort_order | int | NOT NULL, default 0 | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：
- `UNIQUE (section_id, code)`
- `ix_storage_slots_section_id_sort (section_id, sort_order)`

---

### 3.8 items

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| home_id | uuid | NOT NULL, FK → homes.id ON DELETE CASCADE | |
| name | text | NOT NULL | |
| description | text | NULL | |
| category | text | NULL | |
| subcategory | text | NULL | |
| brand | text | NULL | |
| estimated_size | text | NULL | |
| is_sensitive | bool | NOT NULL, default false | |
| needs_lock | bool | NOT NULL, default false | |
| primary_image_id | uuid | NULL, FK → item_images.id (deferrable) | |
| created_by | uuid | NOT NULL, FK → users.id | |
| created_at | timestamptz | NOT NULL, default now() | |
| updated_at | timestamptz | NOT NULL, default now() | |

索引：
- `ix_items_home_id`
- `ix_items_home_category (home_id, category)`
- `ix_items_home_name_trgm (home_id, name)` —— 第一版可不用 trgm，文本搜索走 ILIKE 即可。

> `primary_image_id` 设置为 deferrable，因为 item_images 在 items 之后插入。SQLAlchemy 通过 `use_alter` 与延迟约束处理。

---

### 3.9 item_images

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| item_id | uuid | NOT NULL, FK → items.id ON DELETE CASCADE | |
| object_key | text | NOT NULL | MinIO key |
| url | text | NOT NULL | 访问 URL |
| width | int | NULL | |
| height | int | NULL | |
| is_primary | bool | NOT NULL, default false | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：
- `ix_item_images_item_id`
- 部分唯一：`UNIQUE (item_id) WHERE is_primary = true` —— 同一 item 只能有 1 张主图。

---

### 3.10 item_placements

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| item_id | uuid | NOT NULL, FK → items.id ON DELETE CASCADE | |
| slot_id | uuid | NOT NULL, FK → storage_slots.id ON DELETE RESTRICT | |
| placed_at | timestamptz | NOT NULL, default now() | |
| removed_at | timestamptz | NULL | NULL = active |
| placed_by | uuid | NOT NULL, FK → users.id | |
| source | text | NOT NULL, CHECK in ('ai_recommendation','user_manual') | |
| recommendation_id | uuid | NULL, FK → recommendations.id | |
| note | text | NULL | |

索引：
- `ix_item_placements_item_id`
- `ix_item_placements_slot_id`
- `ix_item_placements_slot_active (slot_id) WHERE removed_at IS NULL`
- 部分唯一：`UNIQUE (item_id) WHERE removed_at IS NULL`

---

### 3.11 recommendations

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| item_id | uuid | NOT NULL, FK → items.id ON DELETE CASCADE | |
| agent_trace_id | uuid | NOT NULL, FK → agent_traces.id ON DELETE RESTRICT | |
| candidates | jsonb | NOT NULL | 见 DOMAIN §5，9 步 pipeline 后的 Top 1~3 |
| pre_filter_count | int | NOT NULL, default 0 | Step 3 候选生成后的候选数（≤ 20） |
| post_filter_count | int | NOT NULL, default 0 | Step 5 约束过滤后剩余候选数 |
| chosen_slot_id | uuid | NULL, FK → storage_slots.id | |
| status | text | NOT NULL, CHECK in ('pending','accepted','rejected','superseded') | 见下方迁移注记 |
| created_at | timestamptz | NOT NULL, default now() | |

索引：`ix_recommendations_item_id`、`ix_recommendations_status (status)`。

> ⚠️ **CHECK 约束已变更。** 最初是 `('pending','accepted','adjusted','rejected')`；
> 迁移 **`0003_update_recommendation_status`** 删掉了 `adjusted`、加入 `superseded`，
> 并把既有的 `adjusted` 行回填为 `accepted`。
>
> 改动原因：`adjusted` 是为「用户手动选了别的位置」设的状态，但它和 `accepted` 在**业务上
> 是同一件事**（都落了 `ItemPlacement`），区别只在 placement 的 `source`。多一个状态意味着
> 每个下游查询都要记得带上它。现在换位置走 **PATCH + accept**，状态留在 `pending` 直到接受。
>
> `app/models/recommendation.py:27` 的 CHECK 字符串必须与迁移保持一致 —— 这是同一个值的
> 两处声明，`app/db/enums.py` 是它们的共同来源。

---

### 3.12 user_preferences

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| user_id | uuid | NOT NULL, FK → users.id ON DELETE CASCADE | |
| home_id | uuid | NOT NULL, FK → homes.id ON DELETE CASCADE | |
| key | text | NOT NULL | |
| value | jsonb | NOT NULL | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：`UNIQUE (user_id, home_id, key)`。

---

### 3.13 home_rules

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| home_id | uuid | NOT NULL, FK → homes.id ON DELETE CASCADE | |
| name | text | NOT NULL | |
| description | text | NOT NULL | 自然语言 |
| rule_type | text | NOT NULL, CHECK in ('hard','soft') | |
| scope | jsonb | NOT NULL, default '{}' | 见 DOMAIN §6 |
| enabled | bool | NOT NULL, default true | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：`ix_home_rules_home_id_enabled (home_id, enabled)`。

---

### 3.14 conversations（第二版启用）

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| user_id | uuid | NOT NULL, FK → users.id | |
| home_id | uuid | NOT NULL, FK → homes.id | |
| item_id | uuid | NULL, FK → items.id | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：`ix_conversations_user_id`。

---

### 3.15 messages（第二版启用）

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| conversation_id | uuid | NOT NULL, FK → conversations.id ON DELETE CASCADE | |
| role | text | NOT NULL, CHECK in ('user','assistant','tool') | |
| content | text | NOT NULL | |
| tool_calls | jsonb | NULL | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：`ix_messages_conversation_id (conversation_id, created_at)`。

---

### 3.16 agent_traces

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| item_id | uuid | NULL, FK → items.id | |
| user_id | uuid | NOT NULL, FK → users.id | |
| home_id | uuid | NOT NULL, FK → homes.id | |
| steps | jsonb | NOT NULL | 见 DOMAIN §7 |
| final_status | text | NOT NULL, CHECK in ('success','verifier_failed','error') | |
| total_duration_ms | int | NOT NULL | |
| llm_tokens_in | int | NULL | |
| llm_tokens_out | int | NULL | |
| llm_cost_usd | numeric(10,6) | NULL | |
| error | text | NULL | |
| created_at | timestamptz | NOT NULL, default now() | |

索引：
- `ix_agent_traces_item_id`
- `ix_agent_traces_home_id_created (home_id, created_at DESC)`

---

### 3.17 assets（Phase 3 上传资产）

通用上传二进制对象（图片，最终可扩到任意二进制）。`Item` 通过 `item_images` 间接引用 `Asset`；
保留独立表是为了 presigned URL 能独立签发、回收被用户放弃的上传。

| 列 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | uuid | PK | |
| home_id | uuid | NOT NULL, FK → homes.id ON DELETE CASCADE | |
| created_by | uuid | NOT NULL, FK → users.id ON DELETE RESTRICT | |
| bucket | text | NOT NULL | MinIO/S3 bucket 名 |
| object_key | text | NOT NULL, UNIQUE | 服务端生成的 S3/MinIO key（原始文件名**绝不**作为 key） |
| content_type | text | NOT NULL | MIME，需在白名单内 |
| size_bytes | bigint | NOT NULL, CHECK `>= 0` | 上传字节数 |
| sha256 | text | NULL | 十六进制 SHA-256，用于同 home 内去重 |
| width | int | NULL | 图片宽（上传时由服务端解析） |
| height | int | NULL | 图片高 |
| original_filename | text | NULL | 客户端上传时的文件名；展示用，不入 key |
| status | text | NOT NULL, default 'pending', CHECK in ('pending','ready','failed') | 上传流程状态 |
| failure_reason | text | NULL | status='failed' 时的说明 |
| uploaded_at | timestamptz | NULL | status 转到 'ready' 时设置 |
| created_at | timestamptz | NOT NULL, default now() | |
| updated_at | timestamptz | NOT NULL, default now() | |

索引：
- `ix_assets_home_id_created (home_id, created_at)`
- `ix_assets_sha256` —— 同 home 内按 sha256 查重

> 删除语义：`POST /api/v1/assets/{id}` 仅当该 asset 不被任何 `item_images` 引用时删得动；
> 否则返 **409**（对象仍占用、删除会让 `Item.primary_image_url` 变 404）。应用层校验，
> DB 没有级联触发器。

---

## 4. 扩展与触发器

第一版启用：

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid()
```

第二版再考虑：

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- 模糊搜索
CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector
```

---

## 5. 迁移策略

- 任何 schema 变更必须新增 Alembic revision。
- 命名规范：`<序号>_<动词>_<对象>.py`，如 `0007_add_recommendation_chosen_slot.py`。
- 升级：在 CI/CD 中先跑 `alembic upgrade head`，再部署新代码。
- 降级：必须显式实现 `downgrade()`。
- 索引创建：生产大表用 `CREATE INDEX CONCURRENTLY`，Alembic 中通过 `op.execute("...")` 实现。

---

## 6. 关键不变量（在应用层 + DB 层双重保证）

| 不变量 | DB 保障 | 应用层保障 |
| --- | --- | --- |
| 物品同时最多一个 active placement | 部分唯一索引 | Service 校验 |
| 物品最多一张主图 | 部分唯一索引 | Service 校验 |
| 候选 Slot 真实存在 | 暂无（JSONB 字段） | Verifier 校验 |
| 任何 Recommendation 必关联 AgentTrace | FK NOT NULL | 落库前确保 |
| 删除 StorageSlot 不允许存在任何 placement（含已 removed） | FK ON DELETE RESTRICT | Service 校验 |

> ⚠️ **部分唯一索引在 SQLite 上不存在。** 测试跑的是 SQLite，`UNIQUE (item_id) WHERE
> removed_at IS NULL` 完全不被强制 —— 所以「同一物品只有一条 active」这条不变量
> **必须由应用层保证，且测试必须显式数行数**，不能靠索引兜底。
>
> P0.3 之前 AI accept 路径只 `INSERT` 不关旧行：PG 上 `IntegrityError`（500），
> SQLite 上**静默留下两条 active**。现在两条写路径（accept / 手动落位）共用
> `app/tools/write_tools.py:_create_placement`，先 `close_active_placements` 再插。

---

## 7. 初始数据 / Seed

第一版可选 seed：
- 一个示例 home
- 几个示例 room（厨房、卧室、书房）
- 每个 room 1～2 个 storage unit + section + slot
- 几条 home_rule（"厨房不放过期食品"、"药品上锁"）

Seed 通过 `python -m app.db.seed` 跑；不进 Alembic。

---

## 8. 性能注意事项

- 物品列表 / 搜索查询：`(home_id, category)` 索引可覆盖大多数过滤。
- Slot 树形查询：每次推荐前批量加载当前 home 的所有 slot（一般规模 < 1000），内存中构造索引。
- `item_placements` 写多读少：active placement 查询靠部分索引。
- `agent_traces.steps` 是 jsonb，体积可能较大；按月分区（PARTITION BY RANGE）放到第二版考虑。

---

## 9. 备份与恢复

- 本地 dev：`docker compose down -v` 即清空。
- 生产（第二版）：每日 `pg_dump` 离线归档；WAL 归档；MinIO 数据走 erasure coding + 异地复制。
- 恢复演练：季度一次。
