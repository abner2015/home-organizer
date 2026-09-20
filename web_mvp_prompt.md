现在开始实现Web MVP。

技术：

Next.js
TypeScript
Tailwind CSS

页面：

/
首页

/home
我的家

/home/rooms
房间

/home/storage
收纳空间

/items
物品

/items/new
添加物品

/items/[id]
物品详情

/recommendations/[id]
AI推荐

/assistant
AI收纳助手

UI目标：

不是后台管理系统。

需要像消费级AI产品。

核心体验：

首页：

* 我的家
* 物品数量
* 空间数量
* 最近添加
* 快速添加物品
* AI助手

添加物品：

1. 上传照片
2. 图片预览
3. AI识别
4. 显示识别结果
5. 用户确认/修改
6. 请求AI推荐
7. 显示具体柜子/层/格
8. 显示推荐原因
9. 用户确认
10. 保存位置

推荐页面：

必须视觉化显示：

柜子
→ 区域
→ 层
→ 格

例如：

B柜
└── 下柜
├── 第1格
├── 第2格 ⭐ 推荐
└── 第3格

显示：

推荐位置
置信度
推荐理由
备选位置

所有API调用通过后端。

API Key绝对不能进入前端。

完成：

页面
组件
loading
error
empty state
mobile responsive

然后运行：

npm run lint
npm run build

修复所有错误。
