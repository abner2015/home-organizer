# EVALUATION — 评估指标

> 定义产品、AI、系统的关键指标、口径、采集方式。

---

## 1. 评估分层

| 层 | 关注问题 | 关键指标 |
| --- | --- | --- |
| 业务层 | 用户是否接受了推荐？ | 推荐接受率、调整率、否决率 |
| AI 层 | 识别和推荐是否正确？ | 识别准确率、推荐 Top-K 准确率、Verifier 拦截率 |
| 系统层 | 服务是否稳定快速？ | P50/P95 耗时、错误率、可用性 |

---

## 2. 业务指标

### 2.1 推荐接受率

> 接受率 = 接受 / (接受 + 调整 + 否决)
> 调整率 = 调整 / (接受 + 调整 + 否决)
> 否决率 = 否决 / (接受 + 调整 + 否决)

口径：分母 = Recommendation 中 `status ∈ {accepted, adjusted, rejected}` 的总数；不包含 `pending`（未反馈）。

- 第一版目标：接受率 ≥ 60%，调整率 25~35%，否决率 ≤ 10%。
- 数据源：`recommendations` 表 + `placements.source`。

### 2.2 平均接受时间

- 用户从看到推荐到点击"接受 / 调整 / 否决"的中位数 / P95。
- 目标：中位数 ≤ 10s。

### 2.3 物品平均回找次数

- 一个物品从入库到下次被查找的平均次数（衡量"AI 推荐是否真的让人记得住位置"）。
- 数据源：第二版埋点（item view events）。

---

## 3. AI 层指标

### 3.1 识别准确率（Vision）

> Top-1 准确率 = name 完全匹配 ground truth 的占比
> Top-1 类别准确率 = category 匹配的占比
> confidence 与实际准确率的相关性（calibration）

- 评估集：20～50 条 golden case（含图片 + 人工标注的 ground truth）。
- 目标：Top-1 名称准确率 ≥ 75%（第一版）。
- 采集：每次 Vision 落库时把 `vision` 结果写入评估样本（可匿名化），定期对账。

### 3.2 推荐准确率

- **Top-K 准确率**：ground truth slot 出现在 Top-K 候选中的比例。
- **Top-1 准确率**：ground truth slot 出现在 Top-1 的比例。
- **MRR**：候选中 ground truth 排名的倒数平均值。
- **Pre-filter Top-1 命中率**：ground truth slot 出现在 Step 3 候选生成 Top-1 中的比例。这是 **pipeline 健康度** 的核心指标——若该值低，说明 Candidate Generation 有问题，而非 LLM。
- ground truth 来源：用户最终"接受"或"调整到"的 slot（视为"对的"位置）。

目标（第一版，500 用户级别数据）：

- Top-3 准确率 ≥ 80%
- Top-1 准确率 ≥ 55%
- MRR ≥ 0.65
- **Pre-filter Top-1 命中率 ≥ 70%**（强制）——若 < 70% 则 LLM 难有作为，应优先优化 Candidate Generation。

### 3.2.1 Pipeline 各步骤的命中率

- Step 3 (Candidate Generation) Top-1 命中率
- Step 5 (Constraint Filtering) 召回率（ground truth 是否仍在 surviving 集合）
- Step 7 (LLM Decision) Top-1 命中率（在 surviving 子集内）

每一步独立可测，定位瓶颈在哪一步。

### 3.3 Verifier 拦截率

> 拦截率 = Verifier 拒绝的推荐 / 总推荐

口径：分母 = 触发了 Verifier 的推荐数；分子 = Verifier 拒绝的次数。

- 拦截率过低：Verifier 太宽松，漏掉违例。
- 拦截率过高：Verifier 太严苛，浪费 LLM。

目标：Verifier 拒绝后用户**手动**接受的 slot 占比 ≤ 5%（说明拦截基本正确）。

### 3.4 Retry 率 / 重试成功率

- 总推荐中"触发过 ≥1 次 Retry"的比例。
- Retry 后最终通过 Verifier 的比例。
- 目标：单次通过率 ≥ 70%；整体（≤2 次 Retry 内）通过率 ≥ 90%。

### 3.5 LLM 输出解析失败率

> parse_error / total_ai_calls

- 目标：≤ 2%。
- 升高说明：prompt 不稳 / Provider 行为漂移 / Pydantic schema 漂移。

### 3.6 成本

- 单次推荐平均 token、平均成本 USD。
- 目标：单次推荐 ≤ 0.05 USD（视 Provider 调整）。
- 单次推荐 **LLM 调用次数 ≤ 2**（Vision + Rank）。

### 3.7 冷启动指标

> 适用于新用户（`history_count=0` 且 `preference_count=0`）的推荐

- 冷启动推荐接受率 vs 老用户接受率（差距 ≤ 15 pp）。
- 冷启动时 `default_room_map` 命中率：ground truth slot 所在 room_type ∈ 默认映射的比例 ≥ 80%。

---

## 4. 系统层指标

| 指标 | 目标 |
| --- | --- |
| API P50 | ≤ 200ms（不含 AI 推荐） |
| API P95 | ≤ 800ms（不含 AI 推荐） |
| 推荐端到端 P95 | ≤ 8s |
| 推荐端到端 P50 | ≤ 4s |
| API 错误率（5xx） | ≤ 0.5% |
| 可用性 | ≥ 99.5% |
| 单条 docker compose up 启动成功率 | ≥ 95% |

采集：FastAPI middleware 记录请求耗时、状态码；Agent Trace 记录推荐端到端耗时。

---

## 5. 数据采集

### 5.1 来源

- `agent_traces.steps`：每次 LLM 调用、Verifier 调用。
- `recommendations`：用户反馈。
- `placements`：用户最终落点。
- API access log：HTTP 层。
- 浏览器埋点（第二版）：停留时间、点击流。

### 5.2 评估脚本

`apps/api/scripts/eval/`

- `eval_vision.py`：跑 golden case，比对 VisionOutput。
- `eval_candidate_gen.py`：跑 deterministic pre-filter，计算 pre-filter Top-1 命中率 + pre/post 数量分布。
- `eval_recommend.py`：从 `recommendations` + `placements` 计算 Top-K、MRR。
- `eval_verifier.py`：统计拦截率、误拦截率（误拦截 = Verifier 拒绝但用户其实接受了的 ground truth）。
- `eval_cold_start.py`：用新用户 fixture 跑冷启动 case，比对接受率 vs 老用户。
- `eval_system.py`：从 access log 算 P50/P95。

CI 上每次 main merge 跑一次，作为 PR 状态门禁（阈值不符则告警，可不强制 fail）。

---

## 6. 评估集管理

- **Golden Vision Set**：20～50 张真实物品图 + 标注。
  - 路径：`apps/api/tests/fixtures/vision/`
  - 格式：`{id}.jpg` + `{id}.json`（ground truth）
- **Golden Recommend Set**：50 个"物品 + 家庭空间 + 期望 slot"组合。
  - 路径：`apps/api/tests/fixtures/recommend/`
  - 由测试夹具构建临时家庭。

新 prompt / 新 Provider 必须通过 golden set 才合并。

---

## 7. A/B 实验（第二版）

- 启用条件：日活 ≥ 1000。
- 实验单元：用户。
- 流量分配：哈希(`user_id`) % 100 → bucket。
- 评估窗口：≥ 7 天。
- 第一版仅支持 prompt 模板版本对比（如 recommend.v2 vs v3）。

---

## 8. 仪表盘（第二版）

- Grafana 面板：
  - 业务：接受率、调整率、否决率
  - AI：识别准确率、推荐 Top-K、Verifier 拦截率、Retry 率、parse_error
  - 系统：API P50/P95、推荐 P50/P95、5xx 率、成本
  - Provider：每个 Provider 的 token / cost / 错误率

数据源（第二版）：PostgreSQL（业务 + AI）+ Prometheus（系统 + Provider）。

---

## 9. 告警

| 条件 | 严重度 | 通知 |
| --- | --- | --- |
| AI provider 5xx > 5% / 5 min | P2 | 飞书 / 邮件 |
| Verifier 拦截率 > 30% / 1h | P3 | 飞书 |
| parse_error > 5% / 1h | P2 | 飞书 / 邮件 |
| 推荐 P95 > 12s | P3 | 飞书 |
| API 5xx > 1% / 5 min | P2 | 飞书 |
| 用户日推荐限流频繁触发 | P3 | 飞书 |

---

## 10. 复盘节奏

- 周：业务 + AI 指标复盘，识别 drift。
- 月：golden set 扩充 / 更新；prompt 与 Pydantic schema 一致性 review。
- 季：评估方法论 review；考虑是否引入 pgvector / 评估更复杂场景。
