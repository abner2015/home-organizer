# API — REST API 设计

> FastAPI，OpenAPI 自动生成。所有路径以 `/api/v1` 开头。
>
> 最后更新：2026-09-21 —— 补实现状态总览（§0），订正 4 处与代码不符的事实
> （recommend 路径、accept 请求体、adjust 端点已废弃、object_key 前缀）。

---

## 0. 实现状态总览

**不要假设本文档里出现的路径都已经存在。** 图例：

- ✅ **已实现** —— 路由在 `app/api/v1/` 里真实存在，可直接调用
- ⏳ **未实现（已排期）** —— 设计已定，对应 `docs/DEVELOPMENT_PLAN.md` 里的某个 P0.x
- 📋 **设计稿（未排期）** —— 只是设想，不要照做

| 节 | 内容 | 状态 |
| --- | --- | --- |
| §2 认证 | signup / login / refresh / me | ✅ |
| §3 家庭 | `GET /homes`、`GET /homes/{id}` | ✅ 读 |
| | 创建 home、改名、加 / 移除成员 | ⏳ P0.2（成员管理未排期） |
| §4 房间 | `GET /homes/{id}/rooms`、`GET /rooms/{id}/storage-units` | ✅ 读 |
| | 创建 / 编辑 / 删除 room | ⏳ P0.2 |
| §5 存储 | `GET /homes/{id}/space-tree`、`GET /homes/{id}/slots` | ✅ 读 |
| | 创建 / 编辑 / 删除 unit / section / slot | ⏳ P0.2 |
| §6 物品 | presign、assets、files、items（创建 / 查询 / 改 / placements / candidates）、recognize | ✅ |
| §7 AI | vision、infer、recommend、accept、reject、PATCH、search | ✅ |
| | ~~`/recommendations/{recId}/adjust`~~ | ❌ **已废弃**，见 §7 |
| §8 摆放 | `POST /placements`（直接落位，不经 LLM） | ⏳ P0.3「反向录入」 |
| §9 规则与偏好 | 规则 / 偏好的 CRUD | 📋 设计稿 |
| §10 错误码 | —— | ✅ |
| §11 请求示例 | —— | ✅ 已订正 |
| §12 版本与兼容 | —— | —— |

**当前最大的缺口是 §3–§5 的写接口**（P0.2）：没有它们，新账号的家是一棵空树，
推荐永远候选为空。详见 `docs/PRD.md` §2.2 旅程 A。

---

## 1. 通用约定

### 1.1 路径前缀

```
/api/v1
```

### 1.2 认证

身份由两部分组成：

| | 作用 | 位置 |
|---|---|---|
| `Authorization: Bearer <access_token>` | **你是谁** —— 签名的 JWT，不可伪造 | 请求头 |
| `X-Home-Id: <home_uuid>` | **你在哪个家里操作** —— 只是一个选择器，不是凭证 | 请求头 |

- 除 `POST /api/v1/auth/signup`、`POST /api/v1/auth/login`、`POST /api/v1/auth/refresh`、
  `GET /api/v1/files/{key}`（签名即凭证）外，所有接口需同时带上面两个头。
- `X-Home-Id` **每次请求都会重新校验** `HomeMembership`，不是"登录时选一次就记住"。
- 401：未带 token、token 无效/过期、或带了 refresh token 去访问业务接口。
- 404：已认证，但不是该 home 的成员。**故意不用 403**——"这个家存在但不属于你"本身就是
  一条不该泄露的信息（其他 home 同理，见 §3）。
- `X-User-Id` / `X-Home-Id` 的旧 header 认证已于 2026-09-21 移除：identity 只来自签名 token，
  伪造 `X-User-Id` 不再有任何作用。

### 1.3 请求 / 响应格式

- `Content-Type: application/json`。
- 时间字段统一 ISO 8601（`2026-09-17T08:30:00Z`）。
- UUID 字段为字符串。
- 错误响应统一结构（见 §1.6）。

### 1.4 分页

```json
GET /api/v1/items?page=1&page_size=20
```

```json
{
  "items": [...],
  "page": 1,
  "page_size": 20,
  "total": 123
}
```

### 1.5 排序

支持 `sort=field:asc` / `sort=field:desc`，白名单字段（避免任意字段排序）。

### 1.6 错误响应

```json
{
  "error": {
    "code": "rule_violation",
    "message": "无法满足硬规则：药品必须上锁",
    "details": {
      "rule_id": "uuid",
      "candidate_slot_id": "uuid"
    },
    "request_id": "uuid"
  }
}
```

错误码字典见 §10。

---

## 2. 认证

### POST /api/v1/auth/signup

```json
// request
{ "email": "user@example.com", "password": "...", "display_name": "Alice" }
// response 201
{ "user": { "id": "uuid", "email": "...", "display_name": "..." } }
```

注册即开箱可用：本接口会**自动创建一个名为「我的家」的 Home 并把注册者设为 owner**。
没有这一步，"注册 → 使用"之间就缺了一环 —— 每个业务接口都要 `X-Home-Id`，而当时没有任何
接口能创建 home。客户端通过 `GET /api/v1/homes` 发现它（该接口不需要 `X-Home-Id`，
正因为它的职责就是告诉你该选哪个 home，见 §3）。

密码最少 8 位；邮箱重复返回 409。

### POST /api/v1/auth/login

```json
// request
{ "email": "user@example.com", "password": "..." }
// response 200
{ "access_token": "...", "refresh_token": "...", "token_type": "bearer", "expires_in": 3600 }
```

### POST /api/v1/auth/refresh

```json
{ "refresh_token": "..." }
```

### GET /api/v1/auth/me

返回当前用户。

---

## 3. 家庭与成员

> **已实现（2026-09-21）**：`GET /homes`、`GET /homes/{homeId}`、
> `GET /homes/{homeId}/rooms`、`GET /homes/{homeId}/space-tree`、
> `GET /homes/{homeId}/slots`、`GET /rooms/{roomId}/storage-units`。
> 本节其余接口（创建 home、改名、加成员、建房间/柜子/层/格）**仍是设计稿，尚未实现** ——
> 当前没有 Home 创建接口，所以注册时自动 provision 一个（见 §2），而收纳结构只能通过 seed 建立。
> 这正是 P0.2「拍照即建模」要补的口子。

### GET /api/v1/homes

返回当前用户所属的 Home 列表。**不需要 `X-Home-Id`** —— 它的职责恰恰是告诉你该选哪个 home。

### POST /api/v1/homes

```json
{ "name": "我的家", "timezone": "Asia/Shanghai" }
```

创建并自动赋予 owner 角色。

### GET /api/v1/homes/{homeId}

详情 + 当前用户的角色 + 房间数 + 物品数 + 规则数。

### PATCH /api/v1/homes/{homeId}

```json
{ "name": "新名字" }
```

仅 owner。

### POST /api/v1/homes/{homeId}/members

```json
{ "email": "bob@example.com", "role": "member" }
```

仅 owner；自动给对方账号发邀请（第一版：如果对方无账号，返回"待注册链接"）。

### DELETE /api/v1/homes/{homeId}/members/{userId}

仅 owner；不能移除自己。

---

## 4. 房间

### GET /api/v1/homes/{homeId}/rooms

```json
[
  { "id": "uuid", "name": "主卧", "room_type": "bedroom", "sort_order": 0 }
]
```

### POST /api/v1/homes/{homeId}/rooms

```json
{ "name": "厨房", "room_type": "kitchen" }
```

### PATCH /api/v1/rooms/{roomId}

### DELETE /api/v1/rooms/{roomId}

禁止：房间下还有 storage unit。

---

## 5. 存储（Unit / Section / Slot）

### GET /api/v1/rooms/{roomId}/storage-units

### POST /api/v1/rooms/{roomId}/storage-units

```json
{ "name": "衣柜 A", "unit_type": "cabinet" }
```

### GET /api/v1/storage-units/{unitId}

详情（含 sections）。

### PATCH /api/v1/storage-units/{unitId}

### DELETE /api/v1/storage-units/{unitId}

禁止：unit 下还有 section。

### POST /api/v1/storage-units/{unitId}/sections

```json
{ "name": "第二层", "section_type": "layer" }
```

### GET /api/v1/sections/{sectionId}

详情（含 slots）。

### PATCH /api/v1/sections/{sectionId}

### DELETE /api/v1/sections/{sectionId}

禁止：section 下还有 slot。

### POST /api/v1/sections/{sectionId}/slots

```json
{
  "code": "A-2-1",
  "label": "左侧",
  "capacity_hint": "small / 衣物",
  "allowed_categories": ["衣物"]
}
```

### PATCH /api/v1/slots/{slotId}

### DELETE /api/v1/slots/{slotId}

禁止：slot 上有 active placement。

### GET /api/v1/homes/{homeId}/space-tree

返回完整树（一次拉全），便于前端做空间视图：

```json
{
  "home": { "id": "...", "name": "我的家" },
  "rooms": [
    {
      "id": "...", "name": "厨房", "room_type": "kitchen",
      "units": [
        {
          "id": "...", "name": "橱柜 A", "unit_type": "cabinet",
          "sections": [
            {
              "id": "...", "name": "第一层", "section_type": "layer",
              "slots": [
                { "id": "...", "code": "A-1-1", "label": "左", "allowed_categories": [], "capacity_hint": null }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

---

## 6. 物品

### POST /api/v1/uploads/presign

```json
{ "file_name": "tea.png", "content_type": "image/png" }
```

返回：

```json
{ "upload_url": "https://minio/...", "object_key": "home/<home_uuid>/2026/09/17/abc.png", "method": "PUT", "expires_in": 600 }
```

Web 拿到 `upload_url` 后 PUT 上去。**签名的 `Content-Type` 是 URL 的一部分**，
浏览器必须原样带上同一个头，否则 MinIO 返回 403 SignatureDoesNotMatch。

> **`STORAGE_BACKEND=local` 时此端点返回 409**（`presign_unsupported`）：
> 本地文件后端没有可供浏览器直传的地址，改用下面的 multipart 上传。

### POST /api/v1/assets/upload

`multipart/form-data`，字段名 `file`。服务端收下字节、校验魔数与宽高、
按 SHA-256 去重后落盘，返回：

```json
{
  "asset_id": "uuid",
  "object_key": "home/<home_uuid>/2026/09/17/abc.png",
  "content_type": "image/png",
  "size": 12345,
  "width": 800, "height": 600,
  "deduplicated": false,
  "url": "https://..."
}
```

`object_key` 就是要传给 `POST /api/v1/items` 的 `image_object_keys`。

### GET /api/v1/files/{key}

只在 `STORAGE_BACKEND=local` 下存在，其余后端一律 404。
`key` 由 `LocalBackend.url_for()` 签出的 `?exp=<unix>&sig=<hmac>` 授权，
签名即凭证（`<img src>` 带不了 Bearer 头）——错签名或过期一律 403。
MinIO 部署请继续使用 presigned GET。

### POST /api/v1/items/recognize

按 `asset_id` 触发一次识别（Phase 4 的资产路径，返回 `RecognizeResponse`）。
`/items/{itemId}/vision` 是物品路径，两者共用同一条识别链路。

### POST /api/v1/items

```json
{
  "name": "龙井茶叶",
  "description": "今年的新茶",
  "category": "food",
  "subcategory": "茶叶",
  "image_object_keys": ["home/<home_uuid>/2026/09/17/abc.png"],
  "primary_image_object_key": "home/<home_uuid>/2026/09/17/abc.png"
}
```

返回创建好的 Item（带 `id`）。

> **`image_object_keys` 的前缀是访问控制，不是格式约定。** key 必须以
> `home/<调用者的 home_uuid>/` 开头，否则 **400**；`_item_views` 会为它拿到的任何 key
> 签发读取 URL，所以不校验前缀等于开放任意对象读取。
> `primary_image_object_key` 必须 ∈ `image_object_keys`。
>
> 另外注意 `category` 是**受控词表**（`food` / `books` / `medicine` …），不是自由文本。
> 填一个词表外的值不会报错，但该物品在候选生成阶段匹配不到任何 `allowed_categories`，
> 推荐会直接 `state=failed`。

### GET /api/v1/items

查询参数：`q`（名称/类别模糊）、`category`、`room_id`、`in_slot`（bool）、`page`、`page_size`、`sort`。

```json
{
  "items": [
    {
      "id": "...",
      "name": "龙井茶叶",
      "category": "食品",
      "primary_image_url": "https://...",
      "current_placement": { "slot_id": "...", "slot_path": "厨房/橱柜 A/第一层/A-1-1" }
    }
  ],
  "page": 1, "page_size": 20, "total": 123
}
```

### GET /api/v1/items/{itemId}

详情 + 全部历史 placement + 推荐历史。

### PATCH /api/v1/items/{itemId}

### DELETE /api/v1/items/{itemId}

需先结束所有 active placement。

### GET /api/v1/items/{itemId}/placements

历史摆放时间线。

---

## 7. AI 识别与推荐

### POST /api/v1/items/{itemId}/vision

> 触发一次 Vision 识别（一般与上传同流程自动触发，也可手动重跑）。
> 识别结果会**写回**该物品（`description` 只在用户没填时覆盖），
> 所以随后 `GET /items/{itemId}` 就能看到。

图片以 `data:image/jpeg;base64,...` 内联进 prompt（见 `app/services/image_payload.py`）,
模型不需要、也无法访问 `object_key` 对应的 URL。

```json
// response 200
{
  "item_id": "uuid",
  "vision": {
    "name": "...",
    "category": "...",
    "subcategory": "...",
    "description": "...",
    "estimated_size": "small | medium | large",
    "confidence": null,
    "is_sensitive": false,
    "needs_lock": false,
    "attributes": []
  },
  "trace_id": "uuid"
}
```

> `confidence` 恒为 `null`、`attributes` 恒为 `[]`：底层 Vision schema 不产出这两项，
> 与其编一个数字不如显式留空。`category` 由模型从**调用者家中的真实词表**里选
> （`build_home_context_for`），编造的类别匹配不到任何收纳位。

### POST /api/v1/items/infer

只给名字（和一句可选描述），让模型补全其余属性——用户不再手填表单。

```json
{ "name": "雨伞", "description": "长柄的" }
```

返回与 `/items/{itemId}/vision` **完全相同的 `vision` 形状**，
前端两条路径共用一套类型。模型无法判断的字段返回 `null`/空串。

```json
{
  "vision": { "name": "雨伞", "category": "misc", "subcategory": "雨具",
              "description": "...", "estimated_size": "medium",
              "is_sensitive": false, "needs_lock": false },
  "trace_id": "uuid"
}
```

> **只推理不落库**：仅写一行 `AgentTrace`（`item_id` 为 `null`），不创建 `Item`。
> 用户确认后前端再 `POST /items`。

### POST /api/v1/recommendations/items/{itemId}/recommend

> 路径挂在 `/recommendations` 下，**不是** `/items/{itemId}/recommend` —— 它产出的是一个
> `Recommendation` 资源。另有一条 `GET /api/v1/recommendations/{recId}` 读回单个推荐。

```json
// request (可选)
{ "include_history": true }

// response 200
{
  "recommendation_id": "uuid",
  "trace_id": "uuid",
  "candidates": [
    {
      "slot_id": "uuid",
      "slot_path": "厨房/橱柜 A/第一层/A-1-1",
      "confidence": 0.86,
      "reason": "厨房常用于存放茶叶...",
      "matched_rules": ["kitchen_for_food"],
      "evidence_item_ids": ["uuid", "uuid"]
    }
  ],
  "pre_filter_count": 18,
  "post_filter_count": 12,
  "verifier_passed": true,
  "retry_count": 0
}
```

失败（候选为空 / 全部被硬规则过滤 / verifier 失败 / 超过 retry）：

```json
{
  "recommendation_id": null,
  "trace_id": "uuid",
  "candidates": [],
  "pre_filter_count": 0,
  "post_filter_count": 0,
  "verifier_passed": false,
  "retry_count": 2,
  "error": { "code": "verifier_failed", "message": "..." }
}
```

### GET /api/v1/items/{itemId}/candidates（调试端点）

> 返回 9 步 pipeline 的中间产物（不含 LLM response 内容，避免泄露），用于排查"为什么没推荐这个 slot"。

```json
{
  "vision": { "name": "龙井茶叶", "category": "食品", "confidence": 0.9 },
  "pre_filter": [
    { "slot_id": "uuid", "score": 87, "score_breakdown": {"history": 40, "category": 25, "room": 15, "capacity": 7, "preference": 0, "default": 0} }
  ],
  "post_filter": [
    { "slot_id": "uuid", "score": 87, "passed_hard_rules": true }
  ],
  "final_candidates": [
    { "slot_id": "uuid", "confidence": 0.86, "reason": "..." }
  ]
}
```

仅 home 成员可访问；仅展示不含敏感 LLM 响应。

### POST /api/v1/search（自然语言检索）

请求：

```json
{ "query": "我有一把雨伞适合放哪里" }
```

响应：

```json
{
  "state": "answer",
  "intent": "suggest_placement",
  "answer_text": "建议把「雨伞」放在 客厅/客厅装饰柜/左玻璃柜/L1。",
  "matches": [],
  "clarification_question": null,
  "suggested_slot": {
    "slot_id": "uuid",
    "code": "L1",
    "label": "左玻璃柜L1",
    "room_name": "客厅",
    "unit_name": "客厅装饰柜",
    "section_name": "左玻璃柜",
    "full_path": "客厅/客厅装饰柜/左玻璃柜/L1"
  },
  "suggested_reason": "该位置类别匹配，且当前有空位。",
  "suggested_item_name": "雨伞",
  "trace_id": "uuid"
}
```

- `state` ∈ `answer` / `needs_clarification` / `not_found` / `exists_but_not_placed` / `error`。
- `intent` ∈ `find_item` / `find_items` / `find_location` / `check_existence` /
  `list_category` / `suggest_placement` / `unknown`。
- **只读**：检索不写 `ItemPlacement` / `Recommendation`，只落一行 `AgentTrace`。
- `suggest_placement`（"这个东西该放哪"）走确定性 generate → hard_filter → rank
  管线，结果放 `answer_text` + `suggested_slot` / `suggested_reason` /
  `suggested_item_name`（其余 intent 三个字段为 `null` / `""`）。前端据此渲染建议卡，
  CTA 跳 `/items/new?name=…` 让用户确认后再落库。
- 该 intent 无候选位置时 `state = not_found`、`suggested_slot = null`（仍 200）；
  名与类别都空时 `state = needs_clarification`。
- 传给 LLM 的 prompt 会用调用者**真实的**位置与类别做接地（见 `docs/AI.md`）。

### POST /api/v1/recommendations/{recId}/accept

请求体为 **`{}`（空对象）** —— slot 不在这里传。

```json
{}
```

选择的位置是推荐自身携带的 `chosen_slot_id`，落 `ItemPlacement`，
置 `Recommendation.status = accepted`。同时把该物品的**其他 pending 推荐置为 `superseded`**。

### PATCH /api/v1/recommendations/{recId}

用户想换一个位置时，**先 PATCH 再 accept**：

```json
{ "chosen_slot_id": "uuid", "note": "想放低一点" }
```

PATCH 之后 `status` 仍是 `pending` —— 直到 accept 才落 `ItemPlacement`。
接受的 placement 记为 `source = user_manual`。

> ❌ **`POST /recommendations/{recId}/adjust` 不存在，已废弃。**
> 早期设计用 `status = 'adjusted'` 表示"用户手动选别的位置"，该状态值已从 CHECK 约束中移除
> （迁移 `0003_update_recommendation_status`，把历史 `adjusted` 回填为 `accepted`）。
> 现行状态只有 `pending` / `accepted` / `rejected` / `superseded`。

### POST /api/v1/recommendations/{recId}/reject

```json
{ "note": "这个柜子太满了" }
```

`status = rejected`；`note` 记录拒绝原因（`P0.4` 会把这条反馈回灌到偏好）。

---

## 8. 摆放（Placement）

> ⏳ **本节整节未实现（P0.3「反向录入」）。** 这两个端点是 §8 的全部内容，目前都不存在。
>
> 现在唯一能写 `ItemPlacement` 的路径是**接受推荐**（§7 的 accept）—— 也就是说，
> 用户无法把一个**已知去向**的物品直接放进某个格子，必须走一遍
> 「拍照 → 识别 → 推荐 → 接受」。这正是 P0.3 要修的问题（见 `docs/PRD.md` §2.2 旅程 B）。
>
> `placement_service` 里写 placement 的逻辑已经存在（accept 就在用），P0.3 主要是把它
> 接出一个**不经 LLM** 的 HTTP 入口。

### POST /api/v1/placements ⏳ P0.3

```json
{ "item_id": "uuid", "slot_id": "uuid", "recommendation_id": "uuid?", "note": "string?" }
```

- 如果物品已有 active placement，自动 `removed_at = now()`。
- 创建新 active placement，`source = user_manual`。
- **不调用任何 LLM**（这是它与 §7 recommend 的本质区别）。

### DELETE /api/v1/placements/{placementId} ⏳ P0.3

仅结束（`removed_at = now()`），不物理删除。

---

## 9. 规则与偏好

> 📋 **本节整节是设计稿，一条路由都没实现。**
>
> 规则（`HomeRule`）与偏好（`UserPreference`）**在推荐 pipeline 里是真实生效的** ——
> 规则参与硬过滤（`app/verification/rule_engine.py`），偏好参与打分。
> 但目前没有任何接口能读写它们：规则来自 `python -m app.db.seed`，
> 用户改不了，也看不到自己家有哪些规则。`P0.4`「讲理由」会需要读规则，
> 因此本节至少要在 P0.4 之前实现「读」的一半。

### GET /api/v1/homes/{homeId}/rules

### POST /api/v1/homes/{homeId}/rules

```json
{
  "name": "药品必须上锁",
  "description": "所有药品必须放在带锁的柜子或抽屉中",
  "rule_type": "hard",
  "scope": {
    "item_categories": ["药品", "保健品"]
  }
}
```

### PATCH /api/v1/rules/{ruleId}

### DELETE /api/v1/rules/{ruleId}

### GET /api/v1/preferences

### PUT /api/v1/preferences/{key}

```json
{ "value": { "default_tool_location": "garage" } }
```

---

## 10. 错误码字典

| code | HTTP | 含义 | 状态 |
| --- | --- | --- | --- |
| `validation_error` | **422** | 请求体 Pydantic 校验失败（FastAPI 默认，**不是 400**） | ✅ |
| `bad_request` | 400 | 语义错误：`X-Home-Id` 不是合法 UUID；`image_object_key` 前缀不属于本 home | ✅ |
| `unauthenticated` | 401 | 缺少 / 失效 token | ✅ |
| `not_found` | 404 | 资源不存在，**或已认证但不是该 home 的成员** | ✅ |
| `forbidden` | 403 | 权限不足 | ⚠️ **当前没有路由返回它** |
| `conflict` | 409 | 唯一约束冲突 / 状态冲突（邮箱已注册） | ✅ |
| `presign_unsupported` | 409 | `STORAGE_BACKEND=local` 下调 `POST /uploads/presign` | ✅ |
| `rule_violation` | 422 | 硬规则被违反 | ✅（经 `verifier_failed`） |
| `slot_in_use` | 409 | 删除 slot 时有 active placement | ⏳ P0.2 |
| `item_in_use` | 409 | 删除 item 时有 active placement | ⏳ 未排期 |
| `verifier_failed` | 422 | AI 推荐无法满足硬规则 | ✅ |
| `ai_provider_unavailable` | 503 | AI 服务不可用 | ✅ |
| `ai_output_parse_error` | 503 | LLM 输出无法解析（重试后） | ✅ |
| `rate_limited` | 429 | 触发限流 | 📋 未实现 |
| `internal_error` | 500 | 兜底 | ✅ |

> **`forbidden` / 403 与跨 home 访问无关。** 访问别人的 home 返回 **404**（§1.2）——
> 403 等于承认"这个家存在但不属于你"，这本身就是不该泄露的信息。
> 表里保留 `forbidden` 只是为了未来的"同一 home 内权限细分"（如 member 改规则），当前无人返回它。

---

## 11. 请求示例

### 11.1 录入新物品完整流程（✅ 全部已实现）

> 所有请求都要带 `Authorization: Bearer <token>` + `X-Home-Id: <home_uuid>`（§1.2）。

```http
# 1. 申请上传 URL（STORAGE_BACKEND=minio）
POST /api/v1/uploads/presign
{ "file_name": "tea.png", "content_type": "image/png" }
→ { "upload_url": "...", "object_key": "home/<home_uuid>/2026/09/17/abc.png" }

# 2. 浏览器 PUT 到 upload_url（不经过 API，必须原样带上签过名的 Content-Type）

# 3. 创建物品（key 必须以 home/<你的 home_uuid>/ 开头，否则 400）
POST /api/v1/items
{
  "name": "未知物品",
  "image_object_keys": ["home/<home_uuid>/2026/09/17/abc.png"],
  "primary_image_object_key": "home/<home_uuid>/2026/09/17/abc.png"
}
→ { "id": "uuid", ... }

# 4. 触发识别（结果写回该物品）
POST /api/v1/items/{itemId}/vision
→ { "vision": { "name": "龙井茶叶", "category": "food", "confidence": null, ... },
    "trace_id": "uuid" }

# 5. 触发推荐 —— 注意路径挂在 /recommendations 下
POST /api/v1/recommendations/items/{itemId}/recommend
→ {
    "recommendation_id": "uuid",
    "candidates": [
      { "slot_id": "uuid", "slot_path": "厨房/橱柜 A/第一层/A-1-1", ... }
    ],
    "verifier_passed": true
  }

# 6. 用户接受（请求体是空的 —— slot 由 chosen_slot_id 承载，不在 body 里传）
POST /api/v1/recommendations/{recId}/accept
{}
```

**`STORAGE_BACKEND=local` 时**（无 MinIO 的环境，例如本地沙箱）第 1–2 步换成一条 multipart：

```http
POST /api/v1/assets/upload          # multipart/form-data，字段名 file
→ { "asset_id": "uuid", "object_key": "home/<home_uuid>/.../abc.png", ... }
```

**没有照片时**（用户只打了一行字）：跳过 1–4，直接

```http
POST /api/v1/items/infer
{ "name": "雨伞", "description": "长柄的" }
→ { "vision": { "name": "雨伞", "category": "misc", ... }, "trace_id": "uuid" }
```

### 11.2 手动摆放（⏳ P0.3，尚不可用）

```http
POST /api/v1/placements
{ "item_id": "uuid", "slot_id": "uuid" }
```

当前唯一能给物品落位的路径是 §11.1 的第 6 步「接受推荐」。直接落位是 P0.3 的内容。

---

## 12. 版本与兼容

- 路径版本：`/api/v1`；破坏性变更才升 v2。
- 字段新增：非破坏，不升版本。
- 字段删除 / 改名 / 类型变更：在 v2 中处理；v1 至少维护 6 个月。
