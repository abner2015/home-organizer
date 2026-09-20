现在实现Storage Recommendation Agent。

不要把这个功能实现成简单的：

LLM → 推荐一个位置。

必须实现：

User Intent
→ Item Understanding
→ Retrieve Home State
→ Retrieve Storage Slots
→ Candidate Generation
→ Hard Constraint Filtering
→ Candidate Ranking
→ LLM Decision
→ Verifier
→ Retry
→ Final Recommendation

Agent必须只能推荐数据库中真实存在的StorageSlot。

实现Tools：

get_home
get_rooms
get_storage_units
get_storage_sections
get_storage_slots
get_items
search_items
get_item_placements
get_user_preferences
get_home_rules
create_recommendation
verify_recommendation
save_placement

Agent状态：

INTAKE
UNDERSTAND
RETRIEVE
CANDIDATE_GENERATION
FILTER
RANK
DECIDE
VERIFY
RETRY
ANSWER
FAILED

Verifier至少检查：

1. StorageSlot是否真实存在。
2. StorageSlot是否属于当前用户家庭。
3. 是否有足够容量。
4. 是否违反硬性安全规则。
5. 是否违反HomeRule。
6. 是否违反UserPreference。
7. 推荐理由是否和Item属性一致。
8. 是否存在更明显的冲突。
9. 是否出现模型幻觉位置。

Verifier失败：

最多Retry 2次。

最终仍失败：

返回：

无法可靠确定推荐位置，请用户补充信息。

禁止编造位置。

Recommendation必须保存：

item_id
storage_slot_id
reason
confidence
agent_trace_id
status

status：

pending
accepted
rejected
superseded

用户确认以后创建ItemPlacement。

增加完整Agent测试。

测试至少包括：

1. 正常推荐。
2. 不存在StorageSlot。
3. 容量不足。
4. 安全冲突。
5. HomeRule冲突。
6. UserPreference冲突。
7. Verifier失败。
8. Retry。
9. Retry两次仍失败。
10. 用户修改推荐。

使用Mock Tool和Mock AI Provider进行测试。

不要依赖真实LLM才能运行测试。
