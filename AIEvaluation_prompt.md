现在实现AI Evaluation体系。

目标不是测试代码，而是测试AI决策质量。

建立evaluation/dataset。

定义测试Case：

{
"id": "...",
"item": {...},
"home": {...},
"expected_slots": [...],
"forbidden_slots": [...],
"reason": "..."
}

至少建立50个测试案例。

覆盖：

厨房电器
茶叶
酒
药品
儿童用品
玩具
数据线
充电器
书籍
衣物
清洁用品
食品
工具
露营用品
易碎品

指标：

1. Item Recognition Accuracy
2. Valid Slot Rate
3. Hard Constraint Violation Rate
4. Recommendation Accuracy
5. Top-3 Recommendation Recall
6. Verifier Catch Rate
7. Retry Success Rate
8. Hallucinated Slot Rate

尤其关注：

Hallucinated Slot Rate必须趋近于0。

实现：

evaluation runner

支持：

Mock AI
真实AI

默认使用Mock。

真实AI测试需要显式开启。

输出：

JSON
CSV
Markdown report

完成后运行Evaluation。
