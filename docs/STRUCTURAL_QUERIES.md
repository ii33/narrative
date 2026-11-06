# 结构查询（schema 11）

`Operations.graph_view(spec)` 是导图、森林、时间轴和分类表的只读入口。视图读取当前对象与指定阅读位置的属性区间，不回放数据库旧修订，不接受 SQL 或表达式。

事件 ID 稳定。`events` 组织包含关系，`predecessors` / `successors` 表达事件先后关联；两者互不推导。所有活动事件必须经包含路径属于同语境活动场景，包含关系不得成环。视图布局不会回写新事实。

`focus_id`、节点类型、深度、方向及阅读位置用于筛选；完整字段由 `get_schema` / `graph_schema` 的查询 schema 发布。属性与人物关系来自事件变更的状态推导，读取早期位置不把后文事件的变更当作已知。

结构查询只读。修正用 `preview_changes` / `apply_changes`，复核已有重复用显式事件物理合并。轻量并发修订号不是历史查询轴；回退使用原文时间点预览与删除后缀。

完整合同见 [数据模型](DATA_MODEL.md) 与 [Agent API](AGENT_API.md)。
