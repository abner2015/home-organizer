# API — REST API 设计

> FastAPI，OpenAPI 自动生成。所有路径以 `/api/v1` 开头。

---

## 1. 通用约定

### 1.1 路径前缀

```
/api/v1
```

### 1.2 认证

- 除 `POST /api/v1/auth/signup` 和 `POST /api/v1/auth/login` 外，所有接口需 `Authorization: Bearer <jwt>`。
- 401：未认证或 token 无效。
- 403：已认证但对该资源无 HomeMembership。

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

### GET /api/v1/homes

返回当前用户所属的 Home 列表。

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

### POST /api/v1/items

```json
{
  "name": "龙井茶叶",
  "description": "今年的新茶",
  "category": "食品",
  "subcategory": "茶叶",
  "image_object_keys": ["uploads/2026/09/17/abc.png"],
  "primary_image_object_key": "uploads/2026/09/17/abc.png"
}
```

返回创建好的 Item（带 `id`）。

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

### POST /api/v1/items/{itemId}/recommend

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

```json
{ "slot_id": "uuid" }
```

落 ItemPlacement，置 `Recommendation.status = accepted`，`chosen_slot_id = slot_id`。

### POST /api/v1/recommendations/{recId}/adjust

```json
{ "slot_id": "uuid" }
```

用户手动选了别的 slot 落库；`status = adjusted`。

### POST /api/v1/recommendations/{recId}/reject

`status = rejected`；记录反馈。

---

## 8. 摆放（Placement）

### POST /api/v1/placements

```json
{ "item_id": "uuid", "slot_id": "uuid", "recommendation_id": "uuid?", "note": "string?" }
```

- 如果物品已有 active placement，自动 `removed_at = now()`。
- 创建新 active placement。

### DELETE /api/v1/placements/{placementId}

仅结束（`removed_at = now()`），不物理删除。

---

## 9. 规则与偏好

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

| code | HTTP | 含义 |
| --- | --- | --- |
| `validation_error` | 400 | 请求体 Pydantic 校验失败 |
| `unauthenticated` | 401 | 缺少 / 失效 token |
| `forbidden` | 403 | 权限不足 |
| `not_found` | 404 | 资源不存在 |
| `conflict` | 409 | 唯一约束冲突 / 状态冲突 |
| `rule_violation` | 422 | 硬规则被违反 |
| `slot_in_use` | 409 | 删除 slot 时有 active placement |
| `item_in_use` | 409 | 删除 item 时有 active placement |
| `verifier_failed` | 422 | AI 推荐无法满足硬规则 |
| `ai_provider_unavailable` | 503 | AI 服务不可用 |
| `ai_output_parse_error` | 503 | LLM 输出无法解析（重试后） |
| `rate_limited` | 429 | 触发限流 |
| `internal_error` | 500 | 兜底 |

---

## 11. 请求示例

### 11.1 录入新物品完整流程

```http
# 1. 申请上传 URL
POST /api/v1/uploads/presign
{ "file_name": "tea.png", "content_type": "image/png" }
→ { "upload_url": "...", "object_key": "uploads/.../tea.png" }

# 2. 浏览器 PUT 到 upload_url（不经过 API）

# 3. 创建物品
POST /api/v1/items
{
  "name": "未知物品",
  "image_object_keys": ["uploads/.../tea.png"]
}
→ { "id": "uuid", ... }

# 4. 触发识别（也可与创建合并为单接口）
POST /api/v1/items/{itemId}/vision
→ { "vision": { "name": "龙井茶叶", "category": "食品", "confidence": 0.9 } }

# 5. 触发推荐
POST /api/v1/items/{itemId}/recommend
→ {
    "recommendation_id": "uuid",
    "candidates": [
      { "slot_id": "uuid", "slot_path": "厨房/橱柜 A/第一层/A-1-1", "confidence": 0.86, ... }
    ],
    "verifier_passed": true
  }

# 6. 用户接受
POST /api/v1/recommendations/{recId}/accept
{ "slot_id": "uuid" }
→ 201

# 也可以手动摆放（不走推荐）
POST /api/v1/placements
{ "item_id": "uuid", "slot_id": "uuid" }
```

---

## 12. 版本与兼容

- 路径版本：`/api/v1`；破坏性变更才升 v2。
- 字段新增：非破坏，不升版本。
- 字段删除 / 改名 / 类型变更：在 v2 中处理；v1 至少维护 6 个月。
