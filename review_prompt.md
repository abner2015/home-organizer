现在不要写业务代码。

请严格Review当前项目中的：

AGENTS.md
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

你现在同时扮演：

* Principal Software Architect
* AI Agent Architect
* Backend Architect
* Product Manager
* Database Architect
* Security Engineer
* QA Engineer
* DevOps Engineer

重点检查：

1. MVP是否过大。
2. 产品核心闭环是否成立。
3. Home / Room / StorageUnit / StorageSection / StorageSlot是否合理。
4. 是否支持复杂柜体。
5. 是否支持柜子多个层级。
6. 是否支持用户调整AI推荐。
7. 是否支持物品移动。
8. 是否记录历史。
9. 是否记录用户偏好。
10. AI是否可能创造不存在的位置。
11. Recommendation是否必须引用真实StorageSlot。
12. Verifier是否能够发现错误推荐。
13. Retry是否有明确条件。
14. AI Provider是否真正解耦。
15. LLM输出是否严格Schema化。
16. API是否合理。
17. 数据库是否存在冗余或缺失。
18. Docker架构是否过度复杂。
19. 安全设计是否充分。
20. 测试策略是否能够验证AI质量。

特别检查：

“AI识别物品”和“AI决定存放位置”是否被错误地设计成一次LLM调用。

正确架构应该倾向于：

Vision
→ Structured Item
→ Storage Retrieval
→ Candidate Generation
→ Constraint Filtering
→ Ranking
→ LLM Decision
→ Verifier
→ Retry
→ Recommendation

如果当前设计存在问题，请修改docs中的设计。

不要写业务代码。

完成后输出：
1.发现的问题
2.修改内容
3.最终架构
4.下一步开发顺序
