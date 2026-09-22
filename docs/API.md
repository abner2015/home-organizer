# API — REST API 设计

> FastAPI，OpenAPI 自动生成。所有路径以 `/api/v1` 开头。
>
> 最后更新：2026-09-22 —— §8 摆放接口随 P0.3 落地（`POST /placements` / `DELETE /placements/{id}`）。
> （同日上一版：§3–§5 的写接口随 P0.2 落地，§7 补 `POST /structures/propose`。
> 2026-09-21：补实现状态总览（§0），订正 4 处与代码不符的事实
> —— recommend 路径、accept 请求体、adjust 端点已废弃、object_key 前缀。）

---

## 0. 实现状态总览

**不要假设本文档里出现的路径都已经存在。** 图例：

- ✅ **已实现** —— 路由在 `app/api/v1/` 里真实存在，可直接调用
- ⏳ **未实现（已排期）** —— 设计已定，对应 `docs/DEVELOPMENT_PLAN.md` 里的某个 P0.x
- 📋 **设计稿（未排期）** —— 只是设想，不要照做

| 节 | 内容 | 状态 |
| --- | --- | --- |
| §2 认证 | signup / login / refresh / me | ✅ |
| §3 家庭 | `GET /homes`、`GET /homes/{id}`、**`PATCH /homes/{id}`（改名，仅 owner）** | ✅ |
| | 创建 home、加 / 移除成员 | ⏳ 未排期（成员管理） |
| §4 房间 | `GET /homes/{id}/rooms`、`GET /rooms/{id}/storage-units` | ✅ 读 |
| | **`POST /homes/{id}/rooms`、`PATCH`/`DELETE /rooms/{id}`** | ✅ 写（P0.2） |
| §5 存储 | `GET /homes/{id}/space-tree`、`GET /homes/{id}/slots` | ✅ 读 |
| | **`POST`/`PATCH`/`DELETE` unit / section / slot** | ✅ 写（P0.2） |
| §6 物品 | presign、assets、files、items（创建 / 查询 / 改 / placements / candidates）、recognize | ✅ |
| §7 AI | vision、infer、recommend、accept、reject、PATCH、search、**`POST /structures/propose`** | ✅ |
| | ~~`/recommendations/{recId}/adjust`~~ | ❌ **已废弃**，见 §7 |
| §8 摆放 | **`POST /placements`（直接落位，不经 LLM）、`DELETE /placements/{id}`（软关闭）** | ✅ 写（P0.3） |
| §9 规则与偏好 | 规则 / 偏好的 CRUD | 📋 设计稿 |
| §10 错误码 | —— | ✅ |
| §11 请求示例 | —— | ✅ 已订正 |
| §12 版本与兼容 | —— | —— |

**§3–§5 的写接口已于 2026-09-22 随 P0.2 落地**（`app/api/v1/structure.py` +
`app/services/structure_service.py`）。在此之前新账号的家是一棵空树、推荐永远候选为空
（详见 `docs/PRD.md` §2.2 旅程 A）；现在用户可以在浏览器里从零搭出第一个 slot。

**§8 的摆放接口已于 2026-09-22 随 P0.3 落地**（`app/api/v1/placements.py` +
`app/agents/placement_service.py`）。在此之前，**给物品落位的唯一路径是接受 AI 推荐** ——
一件已经知道该放哪的东西被迫走一遍「拍照 → 识别 → 推荐 → 接受」（`docs/PRD.md` §2.2 旅程 B）。

**仍未实现的 §3 写接口**：`POST /homes` 与成员管理。注册时自动 provision 一个「我的家」
（见 §2），所以当前没有任何接口需要创建 home。

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

> **已实现（2026-09-21 读 / 2026-09-22 写）**：`GET /homes`、`GET /homes/{homeId}`、
> `GET /homes/{homeId}/rooms`、`GET /homes/{homeId}/space-tree`、
> `GET /homes/{homeId}/slots`、`GET /rooms/{roomId}/storage-units`（读，`app/api/v1/homes.py`），
> 加上 P0.2 的 `PATCH /homes/{homeId}`、`POST /homes/{homeId}/rooms`、
> `PATCH`/`DELETE /rooms/{roomId}`（写，`app/api/v1/structure.py`）。
> **仍未实现**：`POST /homes`、成员管理 —— 分别见下方标注。

### GET /api/v1/homes

返回当前用户所属的 Home 列表。**不需要 `X-Home-Id`** —— 它的职责恰恰是告诉你该选哪个 home。

### POST /api/v1/homes ⏳

> **尚未实现。** 注册时自动 provision 一个 home（见 §2），当前没有创建 home 的路径。

```json
{ "name": "我的家", "timezone": "Asia/Shanghai" }
```

创建并自动赋予 owner 角色。

### GET /api/v1/homes/{homeId}

详情 + 当前用户的角色 + 房间数 + 物品数 + 规则数。

### PATCH /api/v1/homes/{homeId} ✅

```json
// request（仅 name，extra="forbid"）
{ "name": "新名字" }
// response 200
{ "id": "uuid", "name": "新名字", "timezone": "Asia/Shanghai", "owner_id": "uuid" }
```

**仅 owner**。这是全代码库里唯一返回 **403** 的业务接口：非成员仍由 `get_actor` 拦成 404
（见 §1.2），403 留给「确实在这个家里，但不是管理员」。现实中注册者即 owner、
且成员管理未实现，所以这条分支目前只在测试里走到。

### POST /api/v1/homes/{homeId}/members ⏳

> **尚未实现（未排期）。**

```json
{ "email": "bob@example.com", "role": "member" }
```

仅 owner；自动给对方账号发邀请（第一版：如果对方无账号，返回"待注册链接"）。

### DELETE /api/v1/homes/{homeId}/members/{userId} ⏳

> **尚未实现（未排期）。**

仅 owner；不能移除自己。

---

## 4. 房间

### GET /api/v1/homes/{homeId}/rooms ✅

```json
[
  { "id": "uuid", "name": "主卧", "room_type": "bedroom", "sort_order": 0 }
]
```

### POST /api/v1/homes/{homeId}/rooms ✅

```json
// request（extra="forbid"）
{ "name": "厨房", "room_type": "kitchen", "sort_order": null }
// response 201
{ "id": "uuid", "name": "厨房", "room_type": "kitchen", "sort_order": 0, "unit_count": 0 }
```

**新家第一个房间就走这个接口** —— P0.2 之前没有任何路径能创建 room。
`sort_order` 省略时取同家 `max + 1`（不是 0，否则树序退化成按名字排）。
`room_type` 取值见 `app/db/enums.py:RoomType`（`bedroom`/`kitchen`/`bathroom`/`study`/
`living`/`storage`/`other`）；传中文（`"厨房"`）→ **422**，不是 500。

### PATCH /api/v1/rooms/{roomId} ✅

稀疏 PATCH：只改请求里出现的字段。可改 `name` / `room_type` / `sort_order`。
响应是带真实 `unit_count` 的 `RoomView`。

### DELETE /api/v1/rooms/{roomId} ✅

成功 **204**；**房间下还有 storage unit → 409**：

```json
{ "error": { "code": "conflict", "message": "该房间下还有 2 件收纳家具，不能删除",
             "details": { "unit_count": 2 } } }
```

逐级删除，不级联 —— 每一级用自己那句话拒绝，而不是默默扔掉整棵子树。

---

## 5. 存储（Unit / Section / Slot）

四级 create 都返回 **201** + 对应的 Phase 10 view（`RoomView` / `StorageUnitView` /
`StorageSectionView` / `StorageSlotView`），形状与 `GET /space-tree` 里的节点一致。
父级不存在或**属于别的家 → 404**（不是 403，见 §1.2）。

### GET /api/v1/rooms/{roomId}/storage-units ✅

### POST /api/v1/rooms/{roomId}/storage-units ✅

```json
// request（extra="forbid"）
{ "name": "衣柜 A", "unit_type": "cabinet", "description": null, "sort_order": null }
// response 201
{ "id": "uuid", "name": "衣柜 A", "unit_type": "cabinet",
  "description": null, "sort_order": 0, "sections": [] }
```

`unit_type` ∈ `cabinet` / `shelf` / `drawer_cabinet` / `box` / `other`（`app/db/enums.py`）。

### GET /api/v1/storage-units/{unitId} ⏳

> **尚未实现。** 需要详情时用 `GET /homes/{homeId}/space-tree`（一次拉全）。

详情（含 sections）。

### PATCH /api/v1/storage-units/{unitId} ✅

可改 `name` / `unit_type` / `description` / `sort_order`。响应含真实 `sections`。

### DELETE /api/v1/storage-units/{unitId} ✅

成功 **204**；**unit 下还有 section → 409**（`details.section_count`）。

### POST /api/v1/storage-units/{unitId}/sections ✅

```json
// request（extra="forbid"）
{ "name": "第二层", "section_type": "layer", "sort_order": null }
```

`section_type` ∈ `layer` / `drawer` / `box` / `compartment` / `other`。

### GET /api/v1/sections/{sectionId} ⏳

> **尚未实现。** 同上，用 `GET /homes/{homeId}/space-tree`。

详情（含 slots）。

### PATCH /api/v1/sections/{sectionId} ✅

可改 `name` / `section_type` / `sort_order`。响应含真实 `slots`。

### DELETE /api/v1/sections/{sectionId} ✅

成功 **204**；**section 下还有 slot → 409**（`details.slot_count`）。

### POST /api/v1/sections/{sectionId}/slots ✅

```json
// request（extra="forbid"）
{
  "code": "A-2-1",
  "label": "左侧",
  "capacity_hint": "small / 衣物",
  "allowed_categories": ["衣物"],
  "sort_order": null
}
// response 201
{ "id": "uuid", "code": "A-2-1", "label": "左侧",
  "capacity_hint": "small / 衣物", "allowed_categories": ["衣物"],
  "sort_order": 0, "active_count": 0 }
```

- **`capacity_hint` 是自由文本，不是枚举。** `app/verification/checks.py:_parse_capacity` 与
  `app/agents/candidate_gen.py:_parse_capacity` 都刻意解析中文（小/中/大/少量/中等/大量）
  **和裸数字**（数字直接当容量个数用），所以 `"6"` 是合法且被支持的值。收成
  `Literal["small","medium","large"]` 会让 API 表达不出这个能力。
  （只有 **AI 侧** 的 `ProposedSlot.capacity_hint` 用 Literal —— 那是模型的输出契约。）
- `(section_id, code)` 唯一；**同分区内重复 code → 409**（预检，不靠捕获唯一索引的
  `IntegrityError`）。`details.code` 回带冲突的 code。
- `code` ≤50、`name`/`description`/`capacity_hint` ≤100、`label` ≤200。

### PATCH /api/v1/slots/{slotId} ✅

可改 `code` / `label` / `capacity_hint` / `allowed_categories` / `sort_order`。
改 `code` 时会重新做唯一性预检。

### DELETE /api/v1/slots/{slotId} ✅

成功 **204**；**只要该 slot 上有任何 placement 记录（含已 removed）→ 409**：

```json
{ "error": { "code": "conflict", "message": "该收纳位上还有物品记录，不能删除",
             "details": { "active_count": 1, "historical_count": 2 } } }
```

**为什么不只拦 active**：`item_placements.slot_id` 是 `ondelete="RESTRICT"`
（`app/models/placement.py`），数据库约束不管那条 placement 后来是否 removed。
只数 active 的话应用层检查会通过、`DELETE` 再抛 `IntegrityError` → **500**。
历史也是一种保留位置的理由，所以两个数目分开给，UI 才能说清「该位置有 2 条历史记录」。

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

> 落位前会先软关闭该物品**已有的 active placement**（同一物品恒只有一条 active），
> 与 §8 的手动落位共用同一个原语 `_create_placement`。
> P0.3 之前这里不关旧行，只靠部分唯一索引兜底：PG 上 `IntegrityError`（500），
> SQLite 上静默留下两条 active。

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

### POST /api/v1/structures/propose ✅ P0.2

「拍照即建模」的入口：把一张照片 / 一句话交给 AI，拿回一份**结构提议**，
用户确认后由 §3–§5 的普通写接口逐节点落库。

```json
// request（extra="forbid"；两个字段都可以不传）
{ "asset_id": "uuid?", "description": "我家厨房有个三层吊柜" }
```

| 传入 | `source` | 行为 |
| --- | --- | --- |
| `asset_id`（+ 可选 `description` 当提示） | `photo` | 图片内联成 `data:` URI 随同一次调用送给 **vision model** |
| 只有 `description` | `text` | 纯文本一次 LLM 调用 |
| 都不传 | `template` | **完全不调 LLM**，返回服务端模板提议（零成本兜底） |

```json
// response 200
{
  "proposal": {
    "rooms": [
      { "name": "厨房", "room_type": "kitchen",
        "units": [
          { "name": "吊柜", "unit_type": "cabinet",
            "sections": [
              { "name": "第1层", "section_type": "layer",
                "slots": [ { "code": "K1", "label": "左侧",
                             "allowed_categories": ["utensil"],
                             "capacity_hint": "medium" } ] } ] } ] } ],
    "rationale": "描述里提到厨房的三层吊柜",
    "confidence": 0.8
  },
  "warnings": [],
  "source": "text",
  "trace_id": "uuid"
}
```

- **不落库。** 四张存储表（`rooms` / `storage_units` / `storage_sections` / `storage_slots`）
  一行都不写；唯一副作用是一行 `AgentTrace`（`template` 支连这个也没有，`trace_id = null`）。
  这条有测试断言四表行数 + `agent_traces` 恰好 +1（`tests/api/test_structure_proposal_api.py`）。
- **`warnings` 不是装饰。** Step 4（`app/agents/structure/validate.py`）会**静默改写**模型输出
  —— 超限截断、丢掉同分区内重复的 `code`、清掉不在用户词表里的 `allowed_categories`
  —— 不告诉用户，就等于让他确认一份和模型说的不一样的东西。`kind` 是封闭集合：

  | `kind` | 含义 |
  | --- | --- |
  | `truncated` | 某个列表超产品上限，已截断（6 房间 / 12 柜 / 12 层 / 20 格） |
  | `duplicate_code` | 同一分区内 `code` 重复，保留先出现的、丢掉后者 |
  | `category_cleared` | 该类别不在用户家的词表里，已从该格清掉 |
  | `possible_duplicate` | 与新家既有的 room / unit **重名**（不拒绝，只提示） |
  | `empty_vocabulary` | 用户家还没有任何类别词表，所以所有 `allowed_categories` 都会被清空 |

- **`empty_vocabulary` 是对的，不是 bug。** 词表 = `item.category` ∪ `slot.allowed_categories`
  （`app/agents/search/context.py:home_category_vocabulary`）。全新账号两者皆空 ⇒
  每个格的 `allowed_categories` 都被清空 —— 而 `candidate_gen` 把空列表当「不限制」，
  所以首轮推荐反而有候选（这正是 P0.2 验收要的 `pre_filter_count > 0`）。
  若哪天有人「优化」成给新家塞一份默认词表，推荐会在新家上立刻坏掉。
- 只有**枚举违法**（比如 `room_type` 传中文）才值得重试：那是 `AIOutputParseError`，
  按 `vision_service` 的策略 parse ×2 / transport ×1；其余一律在这里修掉，不退回给 LLM。
- **`asset_id` 属于别的家 → 404**（与 §6 的 vision 路径同口径）；provider 失败 → 503。
- 路径用 `/structures/propose` 而非 `/structure-proposals`：后者暗示存在一张
  `structure_proposals` 表，而这个设计**明确拒绝了持久化提议**（`docs/AGENT.md` §14.2）。

---

## 8. 摆放（Placement）✅ 已实现（P0.3，2026-09-22）

「反向录入」：物品**已经存在**、用户**已经知道该放哪**时，直接落位，不进推荐流程。
这条路径与 §7 的 accept 是**同一个写原语**（`app/tools/write_tools.py:_create_placement`）——
「关掉旧 active，插入新 active」只有一份实现，区别只在 `source` 与 `recommendation_id`。

> **不碰任何 `Recommendation` 行。** 一条手动落位并不「解决」一条 AI 建议，用户之后仍可
> 接受 / 拒绝它（届时 accept 会正常关掉这条手动记录）。手动落位的
> `recommendation_id = null`、`source = 'user_manual'`。
>
> 非本家成员由 `get_actor` 拦成 **404**（不是 403），与全库口径一致。

### POST /api/v1/placements ✅

请求（`extra="forbid"`，未知字段 → 422）：

```json
{ "item_id": "uuid", "slot_id": "uuid", "note": "string?" }
```

- 若物品已有 active placement → 自动 `removed_at = now()`（同一物品恒只有一条 active）。
- 创建新 active placement，`source = "user_manual"`、`recommendation_id = null`。
- **不调用任何 LLM**（这是它与 §7 recommend 的本质区别）；副作用只有一行
  `item_placements`，`agent_traces` 行数不变。
- 响应 **201** + `ItemPlacementView`（含 `slot_path` / `source` / `placed_at` / `removed_at`）。
- `item_id` 或 `slot_id` 不属于本 home / 不存在 → **404**；无凭证 → 401。

```json
{
  "id": "uuid",
  "item_id": "uuid",
  "slot_id": "uuid",
  "slot_path": "厨房 / 吊柜 / 上层 / 左侧",
  "source": "user_manual",
  "note": null,
  "placed_at": "2026-09-22T10:00:00+00:00",
  "removed_at": null
}
```

### DELETE /api/v1/placements/{placementId} ✅

仅**结束**（`removed_at = now()`），**永不物理删除** —— 摆放历史仍是可读的
（见 §6 的 `GET /items/{itemId}/placements`）。

- 响应 **200** + 软关闭后的 `ItemPlacementView`（`removed_at` 已置）；
  返回整行而不是 204，前端不必再发一次 GET。
- 对已结束的记录重复调用 → **409**（`conflict`）。
- 跨 home / 未知 id → **404**；无凭证 → 401。

> **关于并发**：单用户 UI 下不会发生，但同一物品的两个落位请求真的同时到达时，
> 两边可能都读到「无 active」而双双插入，PG 的部分唯一索引
> `uq_item_placements_one_active_per_item` 会抛 `IntegrityError`（500）。
> **本批只记录，未在路由捕获**（见 `docs/DEVELOPMENT_PLAN.md` 风险登记）。

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
| `forbidden` | 403 | 权限不足（**仅** `PATCH /homes/{id}` 非 owner） | ✅ P0.2 |
| `conflict` | 409 | 状态冲突：邮箱已注册、删节点时有子级、slot 上有 placement 记录、同分区重复 `code` | ✅ |
| `presign_unsupported` | 409 | `STORAGE_BACKEND=local` 下调 `POST /uploads/presign` | ✅ |
| `rule_violation` | 422 | 硬规则被违反 | ✅（经 `verifier_failed`） |
| `item_in_use` | 409 | 删除 item 时有 active placement | ⏳ 未排期 |
| `verifier_failed` | 422 | AI 推荐无法满足硬规则 | ✅ |
| `ai_provider_unavailable` | 503 | AI 服务不可用 | ✅ |
| `ai_output_parse_error` | 503 | LLM 输出无法解析（重试后） | ✅ |
| `rate_limited` | 429 | 触发限流 | 📋 未实现 |
| `internal_error` | 500 | 兜底 | ✅ |

> **`forbidden` / 403 与跨 home 访问无关。** 访问别人的 home 返回 **404**（§1.2）——
> 403 等于承认"这个家存在但不属于你"，这本身就是不该泄露的信息。
> 403 只用于「**确实在这个家里，但角色不够**」：目前唯一一处是 `PATCH /homes/{id}`
> 改名（成员非 owner）。删除 slot 的 409 用的是 `conflict`（`details` 里给
> `active_count` / `historical_count`），不是历史上的 `slot_in_use`。

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

### 11.2 手动摆放（✅ 可用，P0.3）

```http
POST /api/v1/placements
Authorization: Bearer <token>
X-Home-Id: <uuid>

{ "item_id": "uuid", "slot_id": "uuid" }
```

`201`，`source = "user_manual"`，**一个模型都不调**。物品原有 active placement 被自动软关闭。

移出（软关闭，不删行）：

```http
DELETE /api/v1/placements/{placementId}
```

`200` + `removed_at` 已置；重复调用 `409`。

在 §11.1 的推荐流程里，落位只发生在第 6 步「接受推荐」。§8 让**已经知道该放哪**的
物品跳过前 5 步。

---

## 12. 版本与兼容

- 路径版本：`/api/v1`；破坏性变更才升 v2。
- 字段新增：非破坏，不升版本。
- 字段删除 / 改名 / 类型变更：在 v2 中处理；v1 至少维护 6 个月。
