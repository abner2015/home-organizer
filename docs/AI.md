# AI — AI 层设计

> 定义 AIProvider 抽象、Vision 与 Ranking 的输入/输出、Pydantic Schema、错误处理。
> 核心约束：**LLM 只在少数几个独立调用点出现**（`vision` / `chat` / `structured_output` /
> `rank_candidates`），候选生成 / 约束过滤 / Verifier 全部是确定性代码（见 `docs/AGENT.md`）。
>
> 最后更新：2026-09-21 —— 本文档此前与代码偏差较大，本次按 `app/ai/provider.py` 重写
> §2（Protocol）、§4（Schema）、§6（Prompt）、§7（上下文）、§12、§14。三处订正值得单独说明：
>
> 1. **删除了 `strict=True` 处方。** 这是本文档最有害的一条：`strict=True` 会让 Pydantic 拒绝
>    JSON 反序列化出来的**字符串 UUID**，`RankingOutput` 每次校验都失败，真实 LLM 的 Rank 步骤
>    整条链路静默失效、Agent 只能返回 `state=failed`。**现行约定：AI 输出 schema 一律
>    `extra="forbid"`，一律不用 `strict=True`。**
> 2. **§2 的方法名改为真实的四个**（`vision` / `chat` / `structured_output` / `rank_candidates`），
>    原稿写的 `vision_recognize` 不存在。
> 3. **§7 的「Vision 不发家庭空间」已作废** —— 现在 Vision 必须收到接地块，否则模型会编造
>    `category`（见 §6.3 / §6.4）。

---

## 1. 设计目标

1. **Provider 无关**：业务代码只依赖 `AIProvider` 协议；不写死 OpenAI / Claude / 国内厂商。
2. **结构化输出强约束**：所有 LLM 输出必须能解析为 Pydantic Schema，解析失败视为错误。
3. **LLM 调用面最小化**：整条推荐 pipeline 中 LLM 调用 ≤ 2 次（Vision + Rank），其余步骤零 LLM。
4. **可观测**：每次 LLM 调用记录到 `AgentTrace`。
5. **可降级**：Provider 不可用时返回明确错误，而非崩溃。
6. **可替换**：第二版可新增 / 替换 Provider，业务代码 0 修改。

---

## 2. AIProvider Protocol

定义在 **`app/ai/provider.py`**（不是 `app/ai/schemas.py` —— 该模块不存在；Protocol 与
输出 schema 同住一个文件，是这个边界的**单一入口**）：

```python
from typing import Any, Protocol
from pydantic import BaseModel

class AIProvider(Protocol):
    name: str

    async def vision(
        self,
        image_url: str,
        *,
        hint: str | None = None,
        context: str = "",          # 接地块：调用者真实的类别词表 + 位置名
        timeout_s: float = 30.0,
    ) -> VisionOutput: ...

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout_s: float = 30.0,
    ) -> str: ...

    async def structured_output(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        timeout_s: float = 30.0,
    ) -> BaseModel: ...

    async def rank_candidates(
        self,
        *,
        item: dict[str, Any],           # 已通过 Vision 得到的物品
        candidates: list[dict[str, Any]],  # Candidate Generation + Filtering 后的 ≤ 20 个
        rules: list[dict[str, Any]],    # hard 规则仅供 prompt 提示
        preferences: list[dict[str, Any]],
        history: list[dict[str, Any]],
        last_failure: str | None = None,
        timeout_s: float = 30.0,
    ) -> RankingOutput: ...
```

**注意**：

- **没有 `recommend()` 这种一手包办的方法。** `rank_candidates` 只对**已筛候选**排序 + 写理由。
- `vision` 的 `image_url` 对 Provider 是**不透明**的：可以是公网 URL、MinIO 预签名 URL，
  也可以是 `data:` URI。当前生产路径用 `data:` URI（见 §6.4），因为远端模型 fetch 不到家里的对象存储。
- `structured_output` 是通用逃生口：`search`、`infer`、结构提议都走它，而不是给每个任务
  在 Protocol 上加一个方法。
- 候选列表保证是**白名单**：LLM 只能从中选，不能编造新 slot_id。
- Provider 选择由 `AI_PROVIDER` 环境变量绑定，`app/ai/factory.py:get_provider()` 是 `lru_cache` 单例
  （测试用 `reset_provider()` 清缓存）。

业务代码只通过 `get_provider()` 拿接口，不 import 任何具体实现。

---

## 3. 已有 Provider 实现（第一版）

| 实现 | 适用 | 备注 |
| --- | --- | --- |
| `providers/openai_compatible.py` | OpenAI、DeepSeek、智谱、通义等兼容 OpenAI Chat Completions API 的服务 | 通过 `base_url` 切换。**本地 demo 用它打 DeepSeek** |
| `providers/anthropic.py` | Anthropic Claude（多模态支持） | |
| `providers/mock.py` | 测试 / 评估 | 脚本化响应，**不是**给生产用的 |

> 顺带记一条反直觉的事实：**多模态能力不要按供应商假设。** 曾判断当前配置的
> `deepseek-flash` 大概没有视觉能力，实测三次（红圆 + 蓝方、以及一次「提示词说马克杯
> 但图上不是」的矛盾识别）都正确 —— 它是多模态的。**先测，再决定要不要换模型。**

每个 Provider 必须：

- 实现 `name` 属性。
- 实现 `vision_recognize` / `rank_candidates`。
- 内部将 LLM 原始响应先解析为对应 Pydantic Schema；解析失败抛 `AIOutputParseError`。
- 抛 `AIProviderError` 表示 Provider 不可用 / 5xx / 超时。
- **不在异常中泄露 prompt 内容**到日志（只记 SHA256 哈希或前 100 字符）。
- **不允许在 prompt 中接受指令性内容**：用户描述一律当数据处理。

---

## 4. 输出 Schema（Pydantic）

### 4.1 VisionOutput

```python
class VisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")       # 刻意 NOT strict —— 见下方警告

    name: str = Field(min_length=1, max_length=128)
    category: str = Field(default="", max_length=64)          # 空是合法答案
    subcategory: str = Field(default="", max_length=64)
    usage_scene: str = Field(default="", max_length=128)
    usage_frequency: Literal["high", "medium", "low"] = "medium"
    size_class: Literal["small", "medium", "large"] = "medium"
    fragility: Literal["low", "medium", "high"] = "low"
    notes: str = Field(default="", max_length=512)
    is_sensitive: bool = False
    needs_lock: bool = False
```

- **`category` 为空是合法答案。** `vision.v2.md` 明确要求「无法对应时留空」，而在一个还没有
  任何收纳位置的新家的上下文里，模型被**直接指示**留空。曾用 `min_length=1` 使这条服从变成
  `AIOutputIncompleteError` —— 新用户的第一张照片白烧两次 retry 然后失败。
  `app/schemas/recognition.py:RecognitionResult` 是它的手写镜像，**改一处必须改两处**。
- 注意实际字段与 `docs/API.md` §7 的 `ItemVisionView`（`description` / `estimated_size` /
  `confidence` / `attributes`）**并不是一一对应**：`ItemVisionView` 是适配层
  （`notes`→`description`、`size_class`→`estimated_size`），且 `confidence` 恒为 `null`
  —— 底层 schema 根本不产出 confidence，`attributes` 同理。**不要为了填满 UI 字段而编造。**

> ⚠️ **代码现状与本文档不一致：`app/ai/provider.py` 的 `VisionOutput` 目前仍写着
> `strict=True`（`ItemInferenceOutput` 与 `RankingOutput` 已经是正确的写法）。**
> 它当下没爆，是因为 `VisionOutput` 由 Provider 在**内部构造**、不是从 JSON 直接校验。
> 但它同属 §4.2 那个 bug 的类别，应在下一次动 `provider.py` 时顺手删掉
> （删掉 `strict=True` 后 `RecognitionResult` 手写镜像那侧不受影响）。
> **新写 schema 一律不要加 `strict=True`。**

### 4.2 RankingOutput

```python
class CandidateSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")       # 刻意 NOT strict
    slot_id: UUID
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=512)
    matched_rules: list[str] = []
    evidence_item_ids: list[UUID] = []

class RankingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")       # 刻意 NOT strict
    candidates: list[CandidateSlot] = Field(min_length=1, max_length=3)
```

**关键约束**：

- `candidates[*].slot_id` **必须 ∈** 调用 `rank_candidates` 时传入的 `candidates[*].slot_id` 集合。Verifier 强制校验。
- `extra="forbid"`：LLM 输出多余字段直接抛错。
- **不要 `strict=True`。** 这个 schema 是拿 **JSON 解析出来的文本**去校验的，其中 `slot_id` /
  `evidence_item_ids` 是**字符串**。strict 模式要求真正的 `UUID` 实例，会拒绝它们 —— 结果不是
  明确的类型错误，而是一句 "Structured output missing required fields"，让人误以为是模型
  没输出字段。**2026-09-20 就是这样把真实 LLM 的整个 Rank 步骤静默打死的**：JSON 完全正确，
  校验却每次失败，Agent 永远只能返回 `state=failed`。

  规则：**凡是校验「外部来的 JSON」的 schema，都用 `extra="forbid"` 而不用 `strict=True`。**

---

## 5. 结构化输出的实现方式

`structured_output(prompt, schema)` 是**所有非 Vision 的结构化调用**（search 意图抽取、
infer 补全、结构提议、Rank）的统一入口。Provider 内部按自身能力强制 JSON：

| Provider | 强制 JSON 方式 |
| --- | --- |
| OpenAI 兼容 | `response_format={"type":"json_object"}` + 把 Pydantic schema 注入 prompt |
| Anthropic | Tool Use：参数 schema 与 Pydantic 对齐 |

无论哪种，**最终都解析为 `schema` 指定的 Pydantic 模型**；解析失败抛 `AIOutputParseError`。

> **`rank_candidates` 由 Provider 自己拼 prompt。** `docs/AGENT.md` §7.1 给的 Protocol 签名里
> **没有 `prompt` 参数**（只有 item / candidates / rules / preferences / history / last_failure），
> 所以每个 Provider 内部做一个**延迟 import**（`from app.agents.prompt_render import build_rank_prompt`
> —— 延迟是为了避开 `app.agents` ↔ `app.ai` 的循环依赖），渲染后委托给自己的
> `structured_output(prompt, RankingOutput)`。pipeline 的 DECIDE 步骤**不**构造 prompt。
>
> 另有 `MockAIProvider`，脚本化响应（`structured_output_responses` / `ranking_responses`），
> 供测试与评估使用。Mock 必须把 `pydantic.ValidationError` 包成 `AIOutputParseError`，
> 否则调用方无法区分「LLM 不可达」与「LLM 返回垃圾」。

---

## 6. Prompt 模板管理

- 路径：`app/agent/prompts/`
- 命名：`<task>.v<n>.md`（如 `vision.v2.md`、`rank.v3.md`）
- 加载：服务启动时全部读入内存；调用时按版本取。
- 变量：**`str.format_map`（不是 jinja2）**，写作 `{var}`；字面花括号写 `{{` `}}`。
  模板里只放骨架，不放具体数据。
- 任何 prompt 改动在 PR 中说明影响并跑 golden case。

### 6.1 vision.v1.md（**历史版本，已被 v2 取代**）

> v1 保留在仓库里仅为「只增不改」的审计需要，**不要照它写新 prompt**。
> 它有两个已被证伪的点：`category` 取自一个内置固定集合（会与库内真实词表脱节），
> 以及把图片地址作为变量传进 prompt（现在图片是内联 `data:` URI，且地址不应进 prompt）。

```
SYSTEM:
你是一个家庭物品识别助手。... 规则：1. name 必须是中文。2. category 取自固定集合 ...
category 列表: {{categories}}
USER:
{{image_url}}
{{hint or ""}}
```

### 6.2 recommend.v1.md → v2（当前版本）

> ⚠️ 原稿写的文件名 `rank.v1.md` **不存在**。Rank 步骤的 prompt 是
> **`recommend.v2.md`**（v1 保留）。骨架见 `docs/AGENT.md` §7.4。
>
> v2 相对 v1 的实质变化：**禁止 `reason` 里出现 code / 英文**。
> 早期版本会让推荐理由写成 `厨房/橱柜A/L1S1 有空位`，对用户毫无意义 —— 用户要的是
> 「厨房因为离得近、又常放这类东西」。这条约束与 `full_path` 改用中文 `label` 是同一批修复。

### 6.3 search.v2.md（自然语言检索意图抽取，骨架）

`search.v1.md` 保留（只增不改）。v2 相比 v1 多了一个变量 `{home_context}`，
即由 `app.agents.search.context.build_home_context` 渲染的**真实家庭数据块**：

```
【家中真实数据 — 以下名称只能原样引用，禁止编造】
可选 category（物品大类，只能取下列值之一；无法对应时留空）：
appliance, books, clothes, decor, electronic, food, misc, medicine, utensil
房间（location_hint 可以直接填房间名）：厨房、客厅
可选 location_hint（更具体的位置名称，只能引用下列之一）：
- 客厅/客厅装饰柜/左玻璃柜/L1
...
（共 132 个位置，此处仅列出前 60 个）
家中已有物品示例（最多 25 个）：马克杯、玻璃花瓶、…
```

**为什么必须接地**：`search_items` 对 `Item.category` 是**精确匹配**，而 v1 的
category 示例（`kitchen`/`living`/`study`）是**房间类型**不是物品类别，模型照抄后
恒返回 0 行、并自信地回答「家里没有「kitchen」的物品」。接地块是唯一能保证
词表与库内真实数据一致的做法（仓库里四份类别词表互相不一致）。
类别列表取「所有 item.category ∪ 所有 slot.allowed_categories」的排序并集。

- 变量：`{user_query}` + `{home_context}`；仍是 `str.format_map`，字面花括号写 `{{` `}}`。
- 块内为可读文本，直接作为**值**插入，内部花括号无需转义。
- 渲染是纯函数：同一 `(slots, items)` 逐字节稳定（`prompt_hash` 可比）。

### 6.4 vision.v2.md（物品识别，当前版本）

`vision.v1.md` 保留（只增不改）。v2 修的是与 §6.3 同一类 bug：
v1 把 `category` 的例子写成**房间类型**（厨房 / 卧室 / 浴室 / 工具），
而真实 `Item.category` 词表是 `decor/books/misc/food/utensil/clothes/medicine/electronic/appliance`；
每个 seed slot 的 `allowed_categories` 都非空，`candidate_gen._category_matches` 会丢掉
不包含该 category 的 slot ⇒ `pre_filter_count == 0` ⇒ `state=failed`。

v2 因此：
- 用同一个 `{home_context}` 接地块（`app.services.item_inference_service.build_home_context_for`），
  `category` 只能原样选自列表，对不上就留空；
- 新增 `is_sensitive` / `needs_lock` 两个布尔字段，并写死「**不确定时一律填 false**」——
  把普通物品误判成敏感会让 `check_hard_safety` 把候选全部砍光。

另外 v2 起 prompt 里**不再出现图片地址**：图片以 `data:` URI 内联进请求体
（`app/services/image_payload.py:to_data_uri`，缩到 `MAX_EDGE=1024` 再 JPEG q85），
远端模型不需要、也不可能 fetch 到家里的 MinIO 地址。
因此 `prompt_hash` 用 `scrub_image_url` 后的 `img_<sha256[:12]>`，
base64 长度变化不会让 hash 每次都变。

### 6.5 infer.v1.md（按名字补全属性）

用户跳过照片时，只给名字让模型猜 `category` / `subcategory` / `description` /
`estimated_size` / `is_sensitive` / `needs_lock`，用户只做确认。

- 变量：`{item_name}`、`{item_description}`、`{home_context}`；同样 `str.format_map`。
- 输出 schema 是 `ItemInferenceOutput`（`app/ai/provider.py`）：`extra="forbid"`、
  **不加** `strict`、除 `name` 外全部可空——prompt 要求「无法判断时留 null」，
  非 Optional 字段会把模型合法的「不知道」变成 `AIOutputParseError`。
- 只写一行 `AgentTrace`，不创建 `Item`（见 `app/services/item_inference_service.py`）。

---

## 7. 输入的"上下文压缩"

LLM 的输入 token 越少越好、越相关越好。在调用 LLM 前由 Service / Agent 做压缩：

- **Vision**：图片（内联 `data:` URI）+ 可选 hint + **接地块 `context`**。
  > ⚠️ 原稿写的是「只发 image_url + 可选 hint，**不发家庭空间**」—— 这条已作废，而且正是
  > §6.3 / §6.4 那两个 bug 的成因：不给模型真实词表，它就会照 prompt 里编的例子输出
  > `category`，而 `candidate_gen` 会丢掉所有不包含该 category 的 slot ⇒ `pre_filter_count == 0`。
  > 现在 `context` 是**必需**的（默认 `""` 只是为了不破坏既有调用点），由
  > `item_inference_service.build_home_context_for()` 渲染。
- **Rank 阶段**：
  - Candidate 上下文：每个 candidate 只含 `slot_id` + `path` + `code` + `capacity_hint` + `allowed_categories` + `score` + `score_breakdown`（不发明细）。
  - 历史：只发最近 20 条同 category 的 placement。
  - 规则：只发 `enabled=true` 且 `rule_type=hard` 的规则，标 `(hard)`；soft 规则不进入 Rank prompt（已在 Step 4 过滤）。
  - 偏好：只发显式引用的 key（第一版：全部发，但量小）。

**关键**：`rank_candidates` 接收的 `candidates` 已经是 Step 3 + Step 4 后的 ≤ 20 个，**不会**让 LLM 看到全量 Slot。

---

## 8. 错误分类

| 错误 | 类型 | 处理 |
| --- | --- | --- |
| Provider 5xx / 网络 / 超时 | `AIProviderError` | 不重试，返回 503 给用户 |
| 401 / 403（API Key 问题） | `AIProviderAuthError` | 立即报警（飞书 / 邮件），返回 503 |
| 输出非 JSON / 不符 schema | `AIOutputParseError` | Agent 触发 Retry（限 Vision 或 Rank 自身），最多 2 次 |
| schema 部分通过（缺字段） | `AIOutputIncompleteError` | 同上，Retry 时 prompt 加"上次缺字段：…" |
| 输出 slot_id ∉ 候选白名单 | `AIOutputConstraintError` | 同上，Retry 时 prompt 加"上次选了不在列表中的 slot：…" |
| 4xx 配额耗尽 | `AIProviderQuotaError` | 返回 503，建议稍后重试 |
| 内容被 Provider 拒 | `AIProviderRefusedError` | 不 Retry，记录 trace，UI 让用户重试或换图 |

每种错误都写入 `AgentTrace.error`，不暴露到用户文案中。

> **区分「LLM 返回垃圾」与「LLM 不可达」是硬性要求。** 前者（`AIOutputParseError` 家族）
> 触发 Retry；后者（`AIProviderError` 家族）直接失败。混为一谈会让一次网络故障被重试三次，
> 也会让一次格式错误被误报成服务不可用。
> 注意 `strict=True` 引发的 UUID 校验失败（§4.2）在这张表里**长得像**「缺字段」
> （`AIOutputIncompleteError`），这是它当年难以定位的原因 —— 报错信息指向的方向是错的。

---

## 9. 成本与限流

> ⏳ **本节除最后一条外均未实现。** `app/cache/redis_client.py` 只有一个 `get_redis()`
> 单例（供健康检查用），**没有任何限流计数器，也没有成本累计**。
> `AgentTrace.llm_cost_usd` 字段存在但从未被写入。当前无成本保护 —— 一个循环调用的
> 客户端可以把额度烧完。

- 第一版：每用户每天推荐 50 次上限（Redis 计数）。
- 单次推荐含 **≤ 2 次 LLM 调用**（Vision + Rank）。
- Provider 限流：客户端按 Provider 返回的 `Retry-After` 退避；服务侧记录到 trace。
- 成本：在 Provider 客户端中根据 token 数估算，写入 `AgentTrace.llm_cost_usd`。

---

## 10. 缓存策略

> ⏳ **本节整节是设计稿，一条都没实现。** 每次推荐都重新查库；没有空间快照，
> 也没有 Vision 结果复用（**双击上传会真的调两次 LLM**）。

- **空间快照**：Home 空间变更时失效；TTL 5 min。供 Step 3 检索。
- **推荐结果**：相同 `(item_hash, space_version, rules_version)` 在 10 min 内复用（命中率低，先观察）。
- **Vision 结果**：上传图 object_key 一致时复用 VisionOutput 5 min（防双击）。

---

## 11. 可观测性

每条 `AgentTrace` 包含两次可能的 LLM 调用（Vision + Rank），各自记录：

- `steps[]` 中以 `step_type="vision"` 和 `step_type="llm_decision"` 区分。
- payload 字段：
  - `provider`（provider name）
  - `model`（具体模型）
  - `prompt_hash`（SHA256）
  - `response_tokens_in/out`
  - `duration_ms`
  - `parse_ok: bool`
  - `parse_error: str | None`
- 顶层 `final_status` / `error` / `total_duration_ms` / `llm_tokens_in/out` / `llm_cost_usd`。

可观测性查询示例（第二版）：

- "过去 7 天 verifier_failed 占比"
- "Vision 平均 confidence < 0.6 的物品类别"
- "Rank LLM 一次通过率 vs Retry 后通过率"
- "Candidate Generation 过滤后剩余候选数分布"
- "冷启动（新用户）推荐接受率"

---

## 12. 安全

- API Key 永远只在后端 `.env`，不在前端。
- 每次 LLM 调用记录 `prompt_hash`（不存原文），但**prompt 模板**作为代码可审计。
- 用户上传的图片：Vision 阶段只发给 Provider，**不**做 OCR 提取地址 / 电话 / 身份证号等敏感信息。
- `VisionOutput.notes` 长度上限 512，超长截断，防滥用。
- 日志脱敏集中在 **`app/ai/observability.py`**（不存在 `pii_scrubber` 模块）：
  `hash_prompt`（只留 SHA256，不存原文）、`scrub_image_url`（把 `data:` URI 换成
  `img_<sha256[:12]>`）、`redact_api_keys`、`scrub_pii`、`safe_log_payload`。
  **`prompt_hash` 必须用 `scrub_image_url` 之后的结果** —— 直接哈希整个 `data:` URI 会让
  每次调用 hash 都变（§11 的可观测性随之失效）。

---

## 13. 测试

- **单元**：每个 Provider 用 mock HTTP 响应，断言 Pydantic 解析正确、错误分类正确、白名单校验正确。
- **契约**：所有 Provider 对同一份 fixture 输入返回等价 Pydantic 对象（具体值可差异）。
- **白名单**：`rank_candidates` 单测：LLM 输出含候选列表外的 slot_id → Verifier 拦截 → Retry。
- **Golden Vision**：20~50 张真实物品图 + 标注。
- **Golden Recommend**：20~50 个"物品 + 家庭空间 + 期望 Top-1"组合。
- **Fuzz**：构造畸形 JSON、缺字段、多字段、错类型 → 抛对应异常不崩溃。
- **集成**：推荐服务跑完整 9 步 pipeline，对比 Verifier 通过前后落库数据。

---

## 14. 与 Agent 的接口

- `AIProvider` 暴露 **4 个方法**：`vision` / `chat` / `structured_output` / `rank_candidates`。
  （原稿写「只暴露 `vision_recognize` 和 `rank_candidates`」——方法名与数量都不对。）
- Agent 负责串联 pipeline 全部 9 步；Provider 不感知 Agent 状态机。
- 这保证：换 Provider 不影响 Agent 流程；改 Agent 流程不影响 Provider 实现。
- **两个具体实现 + 一个 mock**：`providers/openai_compatible.py`（OpenAI / DeepSeek / 智谱 / 通义）、
  `providers/anthropic.py`、`providers/mock.py`。业务代码不 import 它们中的任何一个。
- `chat()` 目前只被助手使用（把确定性草稿改写成人话）。**它的返回值不是结构化数据** ——
  调用方必须容忍它返回空串（`MockAIProvider.chat_response` 默认就是 `""`，
  搜索服务把空回复当作「没有脚本化措辞」，回退到确定性草稿）。
