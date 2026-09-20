现在开始实现Phase 1：Backend基础工程。

严格按照docs中的最终架构实施。

目标：

建立生产级FastAPI后端。

要求：

目录：

services/api/

实现：

* FastAPI
* Pydantic
* SQLAlchemy 2.x
* Alembic
* PostgreSQL
* Redis
* pytest
* httpx
* ruff
* mypy（如果架构要求）

建立：

app/
├── api/
├── core/
├── domain/
├── models/
├── repositories/
├── schemas/
├── services/
├── agents/
├── tools/
├── ai/
├── verification/
└── main.py

要求：

1. 配置必须通过环境变量。
2. 创建.env.example。
3. 不允许写真实API Key。
4. 数据库连接必须可配置。
5. Redis连接必须可配置。
6. AI Provider必须预留接口。
7. 添加health endpoint：
   GET /health
8. 添加数据库health检查。
9. 添加统一异常处理。
10. 添加结构化日志。
11. 添加pytest测试。
12. 添加Dockerfile。
13. 添加开发启动说明。

完成后：

1. 安装依赖
2. 运行lint
3. 运行pytest
4. 启动FastAPI
5. 测试/health
6. 修复所有问题

不要实现业务功能。

完成后汇报测试结果。
