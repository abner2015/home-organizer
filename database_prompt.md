现在实现Phase 2：数据库。

严格按照docs/DATABASE.md实现。

使用：

PostgreSQL
SQLAlchemy 2.x
Alembic

实现实体：

User
Home
Room
StorageUnit
StorageSection
StorageSlot
Item
ItemPlacement
Recommendation
UserPreference
HomeRule
Conversation
AgentTrace

要求：

1. 所有表使用UUID作为主键。
2. 所有表必须有created_at和updated_at（适用时）。
3. 外键关系必须明确。
4. 删除策略必须明确。
5. 建立必要索引。
6. StorageSlot必须能够表达：

   * 柜子
   * 层
   * 格
   * 抽屉
   * 收纳盒
7. 必须支持StorageUnit内部多层级结构。
8. ItemPlacement必须保存历史。
9. Recommendation必须保存推荐原因。
10. Recommendation必须引用真实StorageSlot。
11. AgentTrace必须保存Agent执行过程。
12. 不允许数据库逻辑依赖LLM自由文本。

创建：

Alembic migrations
Seed数据
Database tests

添加一个测试家庭：

家庭：
我的家

房间：
客厅
厨房
主卧
儿童房

创建至少两个柜子。

其中一个柜子必须测试复杂结构：

左玻璃柜：
3层

中间：
开放区

右玻璃柜：
3层

下柜：
3格

确保数据库模型可以正确表达这种结构。

完成：

migration
seed
tests

运行全部测试。
