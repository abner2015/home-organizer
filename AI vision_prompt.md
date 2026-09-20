现在实现AI Vision模块。

目标：

图片
→ 多模态模型
→ Structured ItemRecognition
→ Pydantic Validation
→ Item

要求：

建立统一接口：

AIProvider

至少支持：

vision()
chat()
structured_output()

不得在业务代码中直接调用具体厂商SDK。

Vision Recognition必须输出结构化JSON：

{
"name": "...",
"category": "...",
"subcategory": "...",
"usage_scene": "...",
"usage_frequency": "high|medium|low",
"size_class": "small|medium|large",
"fragility": "low|medium|high",
"notes": "..."
}

必须：

1. Pydantic验证。
2. JSON解析失败自动重试。
3. 模型调用超时。
4. 网络异常处理。
5. API错误处理。
6. Token/耗时日志。
7. 不记录API Key。
8. 图片URL不得泄露给日志。
9. AI trace必须可关联。

实现：

POST /api/v1/items/recognize

输入：

asset_id
description（可选）

输出：

structured recognition result

不要在这一阶段实现“放哪里”。

先只实现可靠的物品识别。

增加：

unit tests
mock AI provider
integration tests

真实API测试不要放入默认pytest流程。

完成后运行测试。
