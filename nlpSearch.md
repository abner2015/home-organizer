现在实现自然语言家庭物品查询。

用户可以直接输入：

“我的数据线在哪里？”
“空气炸锅放哪里？”
“客厅有哪些儿童用品？”
“我家还有没有备用电池？”

Agent必须：

1. 识别用户Intent。
2. 调用数据库Tool。
3. 查询真实数据。
4. 不允许凭空回答。
5. 如果数据库不存在对应物品，明确说明。
6. 如果存在多个匹配，列出候选并询问用户。
7. 回答必须包含具体位置。
8. 位置来自真实StorageSlot。

支持Intent：

FIND_ITEM
FIND_ITEMS
FIND_LOCATION
CHECK_EXISTENCE
LIST_CATEGORY
UNKNOWN

实现完整测试。
