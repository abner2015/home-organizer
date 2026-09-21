# EVALUATION — 评估指标

> 定义产品、AI、系统的关键指标、口径与采集方式。
>
> 状态图例：✅ 已落地可跑 · ⏳ 已排期 · 📋 设计稿（还没代码）
>
> 最后更新：2026-09-21 —— 订正：`adjusted` 状态已不存在；评估脚本的真实位置是
> `services/api/app/evaluation/`（不是 `apps/api/scripts/eval/`）；补上 §3 的**真实实测数字**。
>
> ⚠️ **分层看**：**AI 层（§3）已经有一套能跑的离线 harness**；**业务层（§2）与系统层（§4）
> 还只是指标定义**，没有任何采集代码 —— 因为产品还没做过用户测试，也没有 Prometheus。

---

## 1. 评估分层

| 层 | 关注问题 | 关键指标 | 状态 |
| --- | --- | --- | --- |
| 业务层 | 用户是否接受了推荐？ | 推荐接受率 / 否决率 | ⏳ 无采集，未做用户测试 |
| AI 层 | 识别和推荐是否正确？ | 8 项离线指标（见 §3） | ✅ `python -m app.evaluation` |
| 系统层 | 服务是否稳定快速？ | P50/P95 耗时、错误率、可用性 | ⏳ 无实测 |

---

## 2. 业务指标（⏳ 设计稿）

### 2.1 推荐接受率

> 接受率 = 接受 / (接受 + 否决)
> 否决率 = 否决 / (接受 + 否决)

口径：分母 = `Recommendation.status ∈ {accepted, rejected}` 的总数。

- **不包含 `pending`**（用户还没反馈）。
- **不包含 `superseded`**（是被新推荐作废的审计态，不是用户的反馈）。
- 「用户改了位置再接受」**算 accepted** —— 它产生的 `ItemPlacement.source = user_manual`，
  而不是一个单独的 `adjusted` 状态（该状态已在迁移 `0003` 中删除，见 `docs/DOMAIN.md` §2.11）。

数据源：`recommendations` 表 + `item_placements.source`。

- 第一版目标（📋 未经用户测试验证）：接受率 ≥ 60%，否决率 ≤ 10%。
- ⚠️ `status` 没有 `updated_at`，所以**接受耗时无法从 DB 算出来**（见 2.2）。

### 2.2 平均接受时间（⏳）

- 用户从看到推荐到点击接受 / 否决的中位数 / P95。目标：中位数 ≤ 10s。
- **目前算不出来**：`recommendations` 只有 `created_at`，没有决策时间戳。要做需要在
  accept/reject 时写时间，或落一张事件表。

### 2.3 物品平均回找次数（📋 第二版）

- 一个物品从入库到下次被查找的平均次数（衡量「AI 推荐的位置是否真的让人记得住」）。
- 需要物品浏览埋点，当前没有。

---

## 3. AI 层指标 ✅（已落地）

跑法：`cd services/api && python -m app.evaluation`（Mock AI 默认；真模型需 `--use-real-ai`
**且** `EVAL_USE_REAL_AI=1`，双保险避免误烧额度）。报告写到 `evaluation/reports/`
（`report.json` / `report.csv` / `report.md`）。

### 3.1 指标定义（`app/evaluation/metrics.py:MetricReport`）

| # | 指标 | 口径 |
| --- | --- | --- |
| 1 | Item Recognition Accuracy | 视觉识别准确率。**当前恒为 N/A** —— 评测集只有文字用例，没有图片 |
| 2 | **Valid Slot Rate** | 推荐落到的 slot 真实存在且属于该 home 的比例 |
| 3 | **Hard Constraint Violation Rate** | 最终落点违反 hard 规则 / 安全约束的比例。**这是安全指标，必须 ≈ 0** |
| 4 | **Recommendation Accuracy** | 选中的 slot == ground truth 的比例 |
| 5 | **Top-3 Recommendation Recall** | ground truth 出现在 Top-3 里的比例 |
| 6 | Verifier Catch Rate | 拦截率。**当前恒为 N/A**（评测集里没有构造违例用例） |
| 7 | Retry Success Rate | 触发过 Retry 的用例最终通过的比例 |
| 8 | **Hallucinated Slot Rate** | 选中了不存在的 slot 的比例。**必须 ≈ 0，非零即重大安全 bug** |

另有 `per_category` 分类拆分与 `passed/failed` 计数。**`passed` 的精确口径是
`state == "answer" and verifier_passed`** —— 即 pipeline 走完了、没被 Verifier 拦下，
**不等于「推荐正确」**。所以会出现 `Passed: 61 / Failed: 0` 而 Accuracy 只有 68.85% 的组合：
61 条全都拿到了一份通过 Verifier 的推荐，其中 68.85% 与 gold 一致。

### 3.2 实测结果（2026-09-20，61 条用例）✅

| 指标 | Mock AI | Real LLM |
| --- | --- | --- |
| Total / Passed | 61 / 61 | 61 / 61 |
| Valid Slot Rate | 100% | **100%** |
| Hard Constraint Violation Rate | 1.64% | **0.00%** |
| Recommendation Accuracy | 59.02% | **68.85%** |
| Top-3 Recall | 70.49% | **70.49%**（与 Mock 逐字节相同） |
| Retry Success Rate | 100% | 100% |
| Hallucinated Slot Rate | 0.00% | 0.00% |
| 端到端耗时 | 秒级 | ~370s（61 条） |

> **两条必须理解的读数规则：**
>
> 1. **Top-3 Recall 测的是确定性 ranker，不是 LLM。** 它打分的是 `ctx.ranked_candidates`
>    （Step 6 RANK 的输出），而 DECIDE 无法重排。所以 Mock 与真模型给出**完全相同**的
>    70.49% —— 别把它当成模型质量信号，也别指望改 prompt 能推动它，只有改 ranker 才动。
> 2. **真模型只改善 `Recommendation Accuracy`**（59.02% → 68.85%），因为 Accuracy 看的是
>    DECIDE 选中的那一个 slot。

### 3.3 已知的 accuracy 缺口（真模型 17 条未命中）

- **7 条是 ranker 形状问题**：kids / toys 类用例，bedroom 的 tie-break 按 `full_path` 排序，
  主卧（U+4E3B）排在儿童房（U+513F）前面，导致儿童房的 slot 进不了给 LLM 看的 Top-3。
  → 要修的是 ranker，不是 prompt。
- **4 条是明知不可达的 food golden**：全员决策「所有食物必须放厨房」的硬规则下，
  `tea_002` / `alcohol_001` / `alcohol_004` 期望的客厅玻璃柜永远不可达。**算数据集问题，不算回归。**
- 其余分布在 alcohol / tea / ambiguous / no_clear_slot（各 50% 上下），多为 gold 标注本身偏主观。

> 📌 残余问题的结论就记在本节（仓库里没有单独的 findings 文档）。
> 每次改 ranker / prompt / verifier 后重跑，把新数字更新到 §3.2。

### 3.4 目标（📋 第一版设计值，尚无用户数据支撑）

- Top-3 准确率 ≥ 80%、Top-1 准确率 ≥ 55%、MRR ≥ 0.65。
- **Pre-filter Top-1 命中率 ≥ 70%（强制）** —— 若低于此值，说明 Candidate Generation 有问题，
  该优先优化它，而不是去调 LLM。
- ⚠️ MRR、Top-1 准确率、Pre-filter Top-1 命中率**当前 harness 都还没实现**，
  只实现了 §3.1 那 8 个。要加指标就加在 `app/evaluation/metrics.py`。

### 3.5 Pipeline 分步命中率（📋 未实现）

思路正确且仍然值得做：把 Step 4 Candidate Generation / Step 5 Filter / Step 7 DECIDE 的
Top-1 命中率分别算出来，定位瓶颈在确定性的前段还是 LLM。目前只能从
`pre_filter_count` / `post_filter_count` 两列间接观察。

### 3.6 冷启动指标（📋 未实现）

- 适用于新用户（`history_count = 0` 且 `preference_count = 0`）。
- 冷启动接受率 vs 老用户，差距 ≤ 15 pp。
- `default_room_map` 命中率 ≥ 80% —— 对应 `docs/AGENT.md` §4.2 的 `category → room_type` 表
  （实现在 `app/agents/ranking.py:_CATEGORY_ROOM_TYPES`）。

### 3.7 成本 / 解析失败率（📋 未实现采集）

- 目标：单次推荐 ≤ 0.05 USD；解析失败率（`AIOutputParseError` / 总调用）≤ 2%。
- ⏳ **`AgentTrace` 里 `llm_tokens_in` / `llm_tokens_out` / `llm_cost_usd` 三列存在但基本为
  NULL** —— 想算成本得先在 provider 里把 usage 填进去。
- ⚠️ 原稿写「单次推荐 LLM 调用次数 ≤ 2（Vision + Rank）」是错的。**推荐 pipeline 内 LLM 只调用
  1 次**（Step 7 DECIDE）；Vision 发生在物品录入期，不属于推荐。所以正确的口径是
  **单次推荐 ≤ 1 次 LLM 调用**（重试时 ≤ 3 次）。

---

## 4. 系统层指标（⏳ 未实测）

| 指标 | 目标（📋） |
| --- | --- |
| API P50（不含 AI 推荐） | ≤ 200ms |
| API P95（不含 AI 推荐） | ≤ 800ms |
| 推荐端到端 P50 | ≤ 4s |
| 推荐端到端 P95 | ≤ 8s |
| API 错误率（5xx） | ≤ 0.5% |
| 可用性 | ≥ 99.5% |

**一条都没有实测过。** `AgentTrace.total_duration_ms` 已经记了单次推荐耗时，所以推荐端到端的
P50/P95 是最容易先补上的；HTTP 层的耗时没有中间件记录。

> 📌 `docs/DEVELOPMENT_PLAN.md` 里出现过的「P95 ≤ 300ms」与本节目标不一致 —— **以本节为准**
> （300ms 那个是规划期随手写的数，未经任何测算）。

---

## 5. 数据采集

### 5.1 来源

| 来源 | 内容 | 状态 |
| --- | --- | --- |
| `agent_traces.steps` | 每次推荐 / 搜索 / vision 的分步 payload | ✅ 但 payload 只记数量与标识，**没有 score_breakdown / prompt_hash / token** |
| `agent_traces.total_duration_ms` | 端到端耗时 | ✅ |
| `recommendations` | 用户反馈（status） | ✅ |
| `item_placements` | 最终落点 + `source` | ✅ |
| API access log | HTTP 层耗时 / 状态码 | ⏳ 只有结构化日志，无耗时中间件 |
| 浏览器埋点 | 停留时间、点击流 | 📋 第二版 |

### 5.2 评估 harness 的真实结构（`services/api/app/evaluation/`）

| 文件 | 职责 |
| --- | --- |
| `__main__.py` | CLI：`--use-real-ai` / `--report-dir` / `--max-retries` / `--quiet` |
| `dataset.py` | 加载 `evaluation/dataset/*.json` |
| `runner.py` | 逐条跑 agent；`CandidateAwareMockProvider` 是 Mock 的 DECIDE 替身（只从拿到的候选里挑，遵循 ranker 顺序，重试时换掉上一次的领先者） |
| `metrics.py` | §3.1 的 8 项指标 + 分类拆分 |
| `reporters.py` | 写 `report.json` / `report.csv` / `report.md` |

单元测试：`tests/unit/test_eval_mock_provider.py`（8 条）。

> ⚠️ 原稿列的 `apps/api/scripts/eval/` 下的 6 个脚本（`eval_vision.py` / `eval_candidate_gen.py` /
> `eval_recommend.py` / `eval_verifier.py` / `eval_cold_start.py` / `eval_system.py`）**都不存在**。
> 现在是一个统一的 harness，不是 6 个脚本。

---

## 6. 评估集管理

- **数据集**：`services/api/evaluation/dataset/`
  - `appliances.json`（51 条）
  - `edge_cases.json`（10 条）
  - 合计 **61 条**，纯文字（无图片）。
- 每条用例 = 一个合成的家庭空间 + 一个物品 + 期望的 slot 标注 + 分类标签。
- **Golden Vision Set（📋 不存在）**：原稿设想的 `apps/api/tests/fixtures/vision/`
  （`{id}.jpg` + `{id}.json`）从未创建 —— 这也是 §3.1 里 Item Recognition Accuracy 恒为 N/A 的原因。

扩评估集时注意：**合成 slot 的 `code` / `sort_order` 必须唯一**，否则排序 tie 会让结果不可复现
（这个 bug 修过一次 —— 早先所有 slot 共用 `code="S-{i}"` + `sort_order=0`，
导致同一份代码跑出 27.87%~42.62% 的随机 recall）。

新 prompt / 新 Provider 必须通过评估集才合并（📋 约定，CI 门禁还没接）。

---

## 7. A/B 实验（📋 第二版）

- 启用条件：日活 ≥ 1000。实验单元：用户。流量：`hash(user_id) % 100`。窗口 ≥ 7 天。
- 第一版只支持 prompt 模板版本对比。当前各 prompt 的在线版本：
  `vision.v2` / `infer.v1` / `search.v4` / `recommend.v2` / `answer.v1`
  （模板 append-only，旧版本保留，切版本 = 改代码里的版本号）。

---

## 8. 仪表盘（📋 第二版，未实现）

Grafana 面板设想：

- 业务：接受率、否决率
- AI：识别准确率、Top-K、Verifier 拦截率、Retry 率、parse_error（当下由离线 harness 出数，
  不是实时面板）
- 系统：API P50/P95、推荐 P50/P95、5xx、成本
- Provider：每家的 token / cost / 错误率

数据源设想：PostgreSQL（业务 + AI）+ Prometheus（系统 + Provider）。

---

## 9. 告警（📋 第二版，未实现）

| 条件 | 严重度 | 通知 |
| --- | --- | --- |
| AI provider 5xx > 5% / 5 min | P2 | 飞书 / 邮件 |
| Verifier 拦截率 > 30% / 1h | P3 | 飞书 |
| parse_error > 5% / 1h | P2 | 飞书 / 邮件 |
| 推荐 P95 > 12s | P3 | 飞书 |
| API 5xx > 1% / 5 min | P2 | 飞书 |
| 日推荐限流频繁触发 | P3 | 飞书 |

> 应用层限流本身也还没实现（见 `docs/ARCHITECTURE.md` §6.3），最后一条暂时没有触发源。

---

## 10. 复盘节奏

- **周**：跑一次 `python -m app.evaluation`，对比上一版数字，识别 drift。
- **月**：扩 / 更新评估集；review prompt 与 Pydantic schema 的一致性。
- **季**：评估方法论 review（考虑引入 pgvector、更复杂的场景用例）。
