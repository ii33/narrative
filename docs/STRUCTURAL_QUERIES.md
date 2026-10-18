# 结构查询（graph-view/1）

`Operations.open_graph_project(path).graph_view(spec)` 是导图、森林、时间轴和分类表的统一只读入口。四种投影读取同一套已审核节点和断言，不增加第二套事实表，不接受 SQL 或表达式。写入仍须经过候选预检与人工审核。

事件使用稳定节点 ID。包含断言组织父子关系，先后断言表达明确的事件边界约束；两者互不推导。森林可以有多个独立根，先后关系可以分支或汇合。`before` 不等于“紧邻下一个”，视图布局不能回写为新断言。

## 查询

查询字段及完整类型可从 `graph_schema()` 返回的 `query_schema` 读取；查询版本为 `query_schema_version=graph-view/1`。存储合同仍为 `graph-nesting/2`。

| 字段 | 默认值及取值 |
| --- | --- |
| `view` | `graph`；也可为 `forest`、`timeline`、`table` |
| `focus_id` | `null`；任意可见人物、物品、事件等节点 ID |
| `depth` | `2`；整数 1–3 |
| `direction` | `both`；也可为 `in`、`out`，按断言主语→宾语方向遍历 |
| `node_types` | `null` 为全部；类型数组为关联端点筛选；`[]` 为空 |
| `relation_semantics` | `null` 为全部；可选 `attribute/relation/participation/temporal/containment`；`[]` 为空 |
| `predicate_ids` | `null` 为全部；按谓词 ID 精确筛选；`[]` 为空 |
| `context_id` | `canon`；仅选定语境及其祖先可见，不读取子语境或旁支 |
| `story_cut` | `null`；显式故事时间引用，无法确定有效期的断言不充当已知事实 |
| `disclosure_cut` | `null`；`{"source_id":"...","start":123}`，依已登记原文披露顺序过滤 |
| `as_of_revision` | `null` 为当前正式修订；可指定历史修订，跨视图查询应固定同一个值 |
| `candidate_id` | `null`；可单独预览预检通过的当前待审候选，或已接受候选的结果修订；拒绝候选不可查询 |
| `limit` / `offset` | `200` / `0`；端点分页，`limit` 为整数 1–2000，焦点单独保留 |
| `group_by` | `node_type`；分类表也可按 `predicate` 或 `subject` 分组 |

未知字段、无效枚举、布尔型整数及超界参数均拒绝。任何空筛选数组均没有命中端点，显式焦点可作为定位保留，标记 `focus_only=true`。

## 结果与边界

所有结果包含正规化 `query`、`revision`、`nodes`、`edges`、`paths`、`focus`、`uncertainties`、`truncated`、`total_matching`。`edges` 仅为筛选后两端可见的直接断言，保留原始断言 ID、关系、时间边界和证据。

类型筛选作用于端点。多跳路径可以经过其他类型节点，但禁止的关系不能参与遍历。`paths` 保留真实中间节点 ID、断言 ID、方向和步数，多跳标记 `derived=true`；中间节点不会自动进入导图或分类表的 `nodes`，路径也不是一条新直接事实。

`forest` 另含 `roots`、节点 `parent_id/parent_edge_id/child_ids/depth/root_id`；`timeline` 另含 `events`、`temporal_edges`、`nested_axes` 和已知／相对／未知时间投影。筛选后的事件仍保留必要祖先，祖先节点标记 `structural_only`。`organization_edges` 单独保留组织结构，并标记未命中关系筛选的结构边；这些边不能成为关联查询的穿越路径。

端点先分页，再补结构祖先；结构节点有 2000 的投影上限，焦点可单独保留。超界返回 `truncated=true`。遗漏父节点不会使子节点成为新的森林根；时间轴通过 `unplaced_event_ids` 表明无法完整放置的事件，不伪造顶层事件。

`table` 的 `rows` 与同查询导图节点一一对应。每行含真实 `node_type`、经审核的明确 `attributes`、当前筛选下的直接 `relations` 及去重 `evidence_count`。`predicate` 分组只读取有效、正向、现实世界的属性断言；人物主张、假设、撤回或依赖失效属性不会成为确定分类依据。不自动推断“法宝”“门派”等语义类别。

`graph_event_index()` 提供紧凑事件组织与修订链；`roots` 明示独立根，每事件有 `root_id` 及 `temporal_links`。后者保留原断言 ID、`temporal_left/right` 和方向，不把“某事件开始早于另一事件结束”转为整事件先后。指定焦点时先保留焦点，再读取其祖先与后代，排除其他独立树及兄弟分支，最后应用数量限制。

时序一致性按事件边界校验：同一事件 `start<=end` 是弱约束，交叉区间可以合法共存，真正的严格闭环、倒置边界或与已知坐标相矛盾的世界断言会拒绝。包含关系不继承发生时间。失效／陈旧推断、否定和非现实关系不参与确定时序校验；人物错误主张可以与世界发生坐标不同。

现有节点 `occurrence` 没有独立文本披露字段；对同一已知事件较晚直接修订发生坐标后，应使用 `as_of_revision` 回看修订前坐标。文本披露切片不会使用尚未披露的时序断言或不可见事件坐标来补全故事时间顺序。

## CLI 例子

在 UTF-8 文件 `query.json` 中保存查询：

```json
{
  "view": "graph",
  "focus_id": "event-id",
  "depth": 2,
  "direction": "both",
  "node_types": ["person", "event"],
  "relation_semantics": ["participation", "temporal"],
  "limit": 100
}
```

运行：

```powershell
uv run narrative --db data/graph-projects/my-novel.sqlite3 graph-view --spec-file query.json
```

也可传入内联 JSON：

```powershell
uv run narrative --db data/graph-projects/my-novel.sqlite3 graph-view --spec '{"view":"table","node_types":["item"],"group_by":"node_type"}'
```

CLI 与界面使用相同 `Operations.graph_view` 校验与执行，不读取原始数据库表或绕过审核。
