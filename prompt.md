# Prompts
你现在负责开发一个生产级“AI家庭收纳管家”项目。

项目目标：

用户可以建立自己的家庭空间模型，包括：

* 家
* 房间
* 柜子
* 架子
* 抽屉
* 收纳盒
* 柜子的具体层和格

用户可以上传物品照片并补充文字描述。

系统通过多模态大模型：

1. 识别物品
2. 提取物品属性
3. 理解物品使用场景
4. 结合用户家庭空间模型
5. 找到候选存储位置
6. 根据收纳规则进行筛选
7. 由AI进行最终判断
8. 由Verifier验证推荐是否合法
9. 必要时Retry
10. 给用户返回具体到“哪个柜子、哪一层、哪一格”的建议

未来用户新增物品时，系统必须基于：

* 当前家庭空间
* 当前物品
* 已有物品
* 历史摆放记录
* 用户偏好
* 家庭收纳规则

重新进行推荐。

最终产品：
微信小程序 + 后端API + AI Agent。

但是第一阶段先开发Web版本，用于快速验证产品。

技术栈：

Frontend:
Next.js
TypeScript
Tailwind CSS

Backend:
Python
FastAPI
SQLAlchemy
Alembic
Pydantic

Database:
PostgreSQL

Cache:
Redis

Object Storage:
MinIO

Deployment:
Docker Compose
Nginx

AI:
通过统一AIProvider接口调用外部多模态大模型API。

重要原则：

1. AI Provider必须抽象。
2. 不允许把具体模型供应商写死在业务代码中。
3. 所有LLM输出必须使用结构化JSON。
4. 所有结构化输出必须经过Pydantic Schema验证。
5. 不允许AI直接创造数据库中不存在的房间、柜子、层或格子。
6. 推荐位置必须来自数据库中的真实StorageSlot。
7. 推荐必须经过Verifier。
8. Verifier失败时允许Retry，最多2次。
9. Agent必须采用Think → Act → Observe → Verify → Retry → Answer的工作模式。
10. 不要为了技术炫技引入复杂基础设施。
11. 第一版不使用Milvus、Elasticsearch、Neo4j。
12. PostgreSQL优先解决核心数据问题。
13. 后续需要语义搜索时再考虑pgvector。
14. 所有核心功能必须有测试。
15. 所有数据库修改必须通过Alembic migration。
16. 所有API必须有错误处理。
17. 所有AI调用必须记录trace。
18. API Key不能进入前端。
19. .env不能提交Git。
20. Docker环境必须能够启动完整系统。

产品核心实体：

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

请首先：

1. 阅读整个项目目录。
2. 判断当前项目是什么状态。
3. 创建AGENTS.md。
4. 创建完整docs目录。
5. 不写业务代码。
6. 不创建数据库。
7. 不创建前端页面。

需要生成：

docs/PRD.md
docs/DOMAIN.md
docs/ARCHITECTURE.md
docs/DATABASE.md
docs/AGENT.md
docs/AI.md
docs/API.md
docs/EVALUATION.md
docs/DEPLOYMENT.md
docs/DEVELOPMENT_PLAN.md

要求：

PRD：
定义用户、核心场景、用户流程、MVP边界。

DOMAIN：
定义完整领域模型和实体关系。

ARCHITECTURE：
定义前后端、数据库、AI Agent、对象存储和部署架构。

DATABASE：
给出完整表结构、字段、索引、约束、关系。

AGENT：
设计Agent状态机、Tool Calling、Retry、Verifier。

AI：
定义AI Provider、Vision Recognition、Recommendation Agent。

API：
定义REST API和请求响应Schema。

EVALUATION：
设计识别准确率、推荐准确率、Verifier拦截率等指标。

DEPLOYMENT：
设计Docker Compose、Nginx、环境变量、生产部署。

DEVELOPMENT_PLAN：
把开发拆成可以逐个验证的Phase。

完成后停止，不要写业务代码。

Store or draft prompts here.
