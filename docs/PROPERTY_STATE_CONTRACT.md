# 属性状态合同（schema 10）

完整存储合同见 [数据模型](DATA_MODEL.md)。属性只存当前定义和取值自身的有效区间，不存撤回记录或可重放的节点修订。

| 记录 | 字段 |
| --- | --- |
| `property_definition` | `id`、`value_type`、`cardinality`，可选 `target_node_types`、`semantic`、`symmetric` |
| `property_value` | `id`、`node_id`、`property_id`、`value`、`event_id`、`valid_from`、`valid_to`、作用域与逐字证据 |

有效边界为 `{source_id, start, event_id?}`，按正式来源顺序和 Unicode 偏移比较。区间为 `[valid_from, valid_to)`；空终点表示继续有效。正常状态变化关闭旧区间并追加新值，补录早先披露仍按其原文位置排序。同一作用域、认知持有人和属性分组内，单值状态按时间衔接；人物关系的不同对象独立有效，不跨对象自动替换。

录入错误使用 `correct_property` 直接编辑错误取值，或 `delete_property_records` 删除错误行；不把录错伪装成剧情变化。`expire_properties` 原子设置已选取值的终点，不追加依赖目标 ID 的失效行。定义更名、合并与分类修改直接改当前定义和取值引用。属性合并必须保持定义兼容：方向性 `symmetric`、值类型、基数、`semantic` 和目标节点类型范围一致；不能因指向同一人就把不同关系性质当作同义项。不同定义须先经明确查证修正，合并不会自动继承目标差异。

`property_state`、`property_disclosure_state` 依据原文时间点读取区间。`property_history` 是仍保留的实际取值区间序列，不是数据库修订回放。节点资料只保留当前版本，`revision` 仅用于并发检查。

所有写入经 `Operations` 静态验证完整拟写状态后原子提交；不设置人工审批队列。活动事件须属于活动场景；活动物品须有当前 world 持有者节点或明确的“无”“未知”。认知 `holder_id` 不能替代物品持有者。

按原文时间点撤销使用 `rollback_preview` / `rollback_to`：保留边界点，删除后续事实、清理引用并重新开放被截断的旧区间。原文、附件与原始任务输出不删除；节点旧名称等旧编辑版本无法恢复，因为不存储这些副本。
