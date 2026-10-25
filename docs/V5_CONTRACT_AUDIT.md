# v5 合同审计：图与属性是不是一套“变化合同”？

审计日期：2026-10-21  
方法：在隔离项目 `data/verify-contract-20261021/graph.sqlite3` 上直接调用 `Operations`，看行为，不比较术语。

## 结论

**不是“只是类名不同”。在候选、预检、接受和查询入口这一层，v5 已经分成两套合同；它们只共享时间、证据和全局修订号这些底座。**

| 层 | 是否统一 | 证据 |
| --- | --- | --- |
| 全局修订号 | 统一 | 图接受后 rev 2，属性接受后 rev 3，同一个 `meta.revision` |
| `TimeRef` / 事件边界 | 统一 | 两类记录都用 `event_boundary`，属性查询用同一偏序 |
| 证据精确引文 | 统一 | 两类记录都要求 `source_id + 唯一 quote` |
| `as_of_revision` | 统一 | 两类查询都接受审核切点 |
| **候选 / 预检 / 接受事务** | **分裂** | `graph_precheck` 拒绝 property kind；`property_precheck` 拒绝 node kind；两个候选表、两个 accept |
| **记录字段与语义** | **分裂** | Assertion 有 `scope/holder/validity/disclosure`；PropertyRecord 有 `at/effects/expected`，没有 disclosure |
| **查询入口** | **分裂** | `graph_query` vs `property_state/world_state` |
| **撤回机制** | **分裂** | `property_retraction` vs 断言的 `assessment=retracted/supersedes_id` |
| **派生依赖** | **分裂** | `derived_from` 只校验 assertion，不能指向 property record |

## 行为证据

### 1. 一个候选不能同时装图和属性

```text
graph_precheck([node, property_definition, property_initial, event_change], rev)
→ FORMAT: Input tag 'property_definition' ... does not match expected tags:
  node, predicate, context, timeline, evidence, evidence_link, assertion, source_order

property_precheck([node, property_definition, property_initial, event_change], rev)
→ KIND: unknown property record kind (p1/p2/letter/e1)
```

`graph_service.py` 的规则写得更直接：

> 属性记录“不与图记录合并成一个候选”。

### 2. 两阶段接受，共用修订号但不共用事务

```text
graph_propose(nodes) → accept → rev 2
property_propose([definition, initial, event_change]) → accept → rev 3
property_state(letter, holder) → p2
```

这说明：**修订号统一，提交事务不统一**。节点、事件和属性变化不能在一次接受里原子提交。

### 3. 属性撤回可用，但不跨族失效

```text
property_retraction(target=handoff) → accept → rev 4
property_state(letter, holder) → p1
property_history → 保留 initial、event_change、retraction
```

属性历史保留、投影重建正确。但 `derived_from` 只允许指向 `assertion`；**属性记录不能成为断言的依赖**，所以“撤回一条属性记录 → 自动失效依赖它的断言”这条链不存在。断言的撤回/失效是另一套 `assessment/supersedes_id` 机制。

### 4. `claim/knowledge` 合同存在，但认知内容基本不可表达

```text
predicate(semantic=relation) → LEGACY_PREDICATE:
  v5 已停用普通 attribute/relation/participation 断言
predicate(semantic=temporal) → 通过
```

`scope=claim|knowledge + holder_id` 的字段校验存在，但能承载它的谓词只剩 `temporal` 和 `containment`。像“甲相信信在自己口袋里”这种**关于属性值的认知**，没有可用的 relation/attribute 谓词，因此当前无法表达。

这不是“认知模型还没定”，而是**现有合同把可用的表达路径关掉了**。

### 5. GraphRAG 仍然只到预览

`ms_graphrag_adapter.preview_export` 返回：

```text
formal_candidates_created: 0
graph_revision_changed: False
```

没有任何从 GraphRAG 实体/关系/claims 到 `graph_propose` / `property_propose` 的映射。抽取结果不能形成候选，用户仍要重新录入。

### 6. 规则与结构化值

- `node_type` 允许 `other`，规则可以当节点存身份、属性和来源；但没有规则语言、条件求值器或推演执行链。
- `property_definition.value_type` 只有 `string/number/boolean/node`；多值是同类型列表，不支持任意嵌套 JSON。

### 7. 主线 / 离群事件

没有 `mainline` 实体，但 `graph_timeline` 用 `lanes`、`relative_axes`、`nested_axes`、`uncertainties` 和偏序连通组表达；不可比组件保留，不按序号强排。**这一条不构成偏离**。

## 判断

这次拆分看起来是**有意的两阶段门槛**：先让图候选被人工接受，属性记录才能引用稳定节点/事件，避免属性引用未审图 ID。这个理由成立。

但代价必须写清楚：

1. 没有“节点 + 事件 + 属性变化”的单次原子候选；
2. 没有跨族派生失效；
3. 认知内容没有可用表达；
4. GraphRAG 抽取到正式资料的路径未接通；
5. “一项变化有统一有效范围和溯源”只在**底座**成立，在**记录合同**不成立。

## 建议

### 如果保留两阶段拆分

- 文档明确写成“两套变更合同共享时间/证据/修订底座”，不要再写“断言统一记录属性变化”。
- 给属性记录补 `disclosure`（如果阅读切点需要按属性记录回看）；当前属性查询没有披露切点。
- 定义跨族失效：属性撤回后，哪些断言/推演需要重新检查。至少建立显式 `derived_from_property` 或统一依赖表。

### 如果要求统一上层合同

- 增加一个**变更信封**：一个候选可以同时包含 graph objects 和 property records，一次 precheck、一次 accept、一个事务；内部仍可分表存储。
- 统一 `derived_from` 允许引用 assertion 或 property record，撤回时统一计算失效闭包。
- 统一查询入口：`state_at` 返回属性 + 相关断言/认知，而不是两个独立入口。

### 认知

- 增加专用的 `belief`/`claim` 记录，字段至少：holder、认知对象、命题内容、时间锚、证据、语境；或者放开 `claim/knowledge` 可用的谓词语义，不再只允许 temporal/containment。

### GraphRAG

- 写一个 mapper：GraphRAG entity/relationship/claim → Node/Assertion/PropertyRecord 候选，带 evidence 映射和预检；否则它永远只是预览。

## 对上一轮反馈的更正

上一轮我说“主干一致，主要是命名与分层没讲清楚”。**这句在候选/变更层是错的**：行为已经分裂，不是命名问题。概念模型可以继续用“断言”做统一叙述，但实现文档必须承认两套合同，并说明为什么拆、拆的代价是什么。

## 修复进展（2026-10-21）

已实现**统一变更候选**，对应四项交付中的第 1 项：

- `Operations.change_precheck(graph_objects, property_records, expected_revision)`
- `Operations.change_propose(...)`
- `Operations.change_candidates(...)` / `change_candidate(...)`
- `Operations.change_accept(id, version)` / `change_reject(id, version)`

行为：

- 一个候选可以同时包含新节点、事件和属性记录；属性记录可引用本批新建节点/事件。
- 预检合并“已接受快照 + 本批图对象”，属性记录不再要求节点先被接受。
- 接受在**一个 SQLite 事务**内写入 `objects`/`evidence_links` 与 `property_records`，只递增一次 revision。
- 接受时重新检查 `expected_revision`；过期候选整批拒绝，不产生部分写入。
- `graph-*` / `property-*` 两套旧入口保留，作为单族调用路径。

验证：

- `tests/test_change_candidate.py` 7 passed：混合接受、批内引用、非法属性记录不写入、过期候选不写入、graph-only/property-only、旧两阶段 API 回归。
- 新增事务中途失败测试：在 `property_records` 上装一个只对 `id='boom'` 生效的 SQLite 触发器，让图对象写入后属性插入失败；验证 `objects`、`evidence_links`、`property_records` 全部回滚，revision 不递增，候选仍是 pending。
- 新增基础修订测试：候选建立后另一个变更先被接受，传入更大的 `version` 也不能让旧候选通过。
- 图与属性回归 68 passed；Ruff 与 Pyright 0 errors。

## 第 2 项：属性披露与认知（2026-10-21）

- `property_initial` / `property_observation` / `event_change` 新增 `scope`、`holder_id`、`disclosure`。
- `scope` 为 `world/belief/claim/knowledge`；`world` 不得带 holder，其他三种必须是人物 holder。
- 世界变化不自动改写认知；E0–E3 用例验证“世界受伤、乙仍信健康、乙得知后信受伤、世界康复后乙仍信受伤”。
- `property_state(..., scope=..., holder_id=...)` 分别求值；无记录的丙返回 `unrecorded`，不是“不知道”。
- `disclosure_cut` 隐藏晚于切点的记录；无披露位置的记录在切点下是 `unknown`，不默认可见；返回 `hidden`/`unknown` 记录 ID。
- `claim` 表示人物说了什么，不自动等于其信念；`knowledge` 不自动等于世界真值。
- `tests/test_property_cognition.py` 4 passed（并存、过时认知、披露切点、历史修订）。
- 图与属性回归 69 passed；Ruff 与 Pyright 0 errors。

## 第 3 项：跨族依赖与派生资格（2026-10-21）

- `derived_from` / `support_groups` 使用类型化引用 `{kind, id}`（`assertion` 或 `property_record`）；旧字符串按 assertion 解释。
- `support_groups` 为“组内全部成立、组间任一成立”；没有支持组的记录默认有效（除非撤回）。
- 预检拒绝悬空引用和推导环；跨族引用只能通过 `change_precheck`，单独 `graph_precheck` 报 `CROSS_FAMILY_REF`。
- `property_state` 和 `graph_query` 排除失去支持的派生记录；断言带 `derived_status`。
- 撤回与资格变化在同一修订中生效；当前查询一致，旧 `as_of_revision` 仍还原旧状态。
- “失去支持”不等于撤回，也不等于内容为假；历史记录保留。
- `tests/test_dependency_eligibility.py` 2 passed（跨族传播、独立支持、认知与纠错分离、历史还原、循环/悬空引用）；阶段 3 回归 80 passed。

## 第 4 项：GraphRAG mapper（2026-10-21）

- 新增 `src/narrative/graphrag_mapper.py` 与 `scripts/graphrag_import.py`。
- GraphRAG `entities` 映射为节点：只有持久化外部 ID 映射或唯一确认别名才复用；仅名称/类型相同会保留独立节点并记入 `identity_candidates`，不自动合并。`relationships` 和自由文本 `covariates` 保留为 `unresolved`。
- 人工样例支持 `before`、`holder`、`belief`，证据必须是原文唯一子串；世界事实与认知分别落点。
- 转换后走 `change_precheck` → `change_propose`，mapper 不自动接受。
- `mapping.json` + `imports.json` 支持同一指纹重跑复用，不重复建节点、不空增修订。
- 真实三章导入：75 实体 → 75 节点；114 relationships + 118 claims 未解决；候选 `064f49aa6ef84320bc9a0a710eb86059`，接受修订 3→4；重跑复用，修订保持 4。
- 真实 claim `809b...` 经人工判读 + 程序校验转为铜钥匙持有者变化：候选 `ae64cf49dde24428b94eb045fe6c6ba9`，接受 4→5；查询 holder=林舟；重跑复用。`entity_map` + `facts` 是模型辅助转换应输出的合同，模型调用尚未接入。
- 人工样例：交付前 holder=甲、交付后 holder=乙、乙认知 content=账单、世界 content=unrecorded；候选 `f3811598a83a4d629465891e2ac20f04`，接受修订 2；重跑复用。
- **真实模型调用**：`scripts/model_claim_convert.py` 使用 `deepseek-chat`、prompt `claim-conversion/1`；claim `809b...` 输出 holder + 3 条 before，2 条 unresolved；候选 `e8d2c2bdf1854deeb961b78738667b66` 接受修订 6，查询 `铜钥匙.holder=林舟`；重跑复用，修订 6→6。
- 模型边界 `start` 与人工 `end` 的差异、初始持有未输出，均记录为待修订/部分转换，没有静默修改。
- 校准：rev7 撤回模型 `start` 记录，rev8 撤回重复观察，rev10/11 补事件顺序；当前 `wear_tanbo.start=老周`、`end=林舟`，`as_of_revision=6` 仍还原模型当时的记录与冲突。
- 第二条真实 claim（信念）：“林舟知道老周赢了”；模型输出 1 条 belief + 8 条无事件锚点的 holder，整批候选拒绝；只保留 belief，修正候选 `d5f584...` 接受修订 9，查询 belief=赢了、world=unrecorded，重跑保持 9。
- 进度：232 条中 2 条 `partial_accepted`、230 条 `untreated`；台账 `data/graphrag-import-20261021/import-status.json`。
- **Mapper 合同修复**：`holder`/`belief` 必须有 `at_event` 或 `at="initial"`；只有 `boundary` 没有事件时进 `unresolved`，不生成初值；诊断带 `fact_index`。回归 `test_boundary_without_event_is_not_converted_to_initial`。
- 用 `claim-conversion/2` 重跑第二条 claim：无事件锚 holder 从 8 降到 0，但目标 belief 变成 0、无关 holder 仍有 5；候选 `1338...` 整批拒绝，正式修订不变。合同错误被程序挡住，模型语义范围仍未收敛。
- 目标限定 v3（260 字上下文、仅林舟/老周）：0 条无关 holder，目标 belief 被识别，但把“老周获胜”事件同时当作认知对象和时间锚；程序要求同事件作时间锚时提供出现在证据中的 `time_basis`，本样例没有，目标进入 unresolved，候选 `2943...` 拒绝，`property_records` 为空。同事件 + `time_basis` 的正例可生成观察记录。
- 完整报告见 `docs/MODEL_CLAIM_CONVERSION.md`。
- **披露优先路径**：`property_observation` 可只用 `disclosure`、不带故事时间；新增 `property_disclosure_state` 按来源顺序求值；mapper 的 `disclosure_only` 忽略模型事件锚，按证据位置登记。v3 信念候选 `5743...` 接受修订 13，披露切点后可见、故事时间仍 unrecorded。
- `tests/test_graphrag_mapper.py` 8 passed（含同名不自动合并、无事件锚不转初值、同事件时间依据正反例、披露回退）；阶段 3/4 回归 88 passed。

**仍有限制**：

1. 自由文本 relationship/claim 没有可靠的谓词/属性映射，当前全部进 `unresolved`；
2. GraphRAG 事件没有边界信息，只建事件节点，不建事件顺序；
3. 实体没有节点级证据/披露位置；
4. 没有导入差异报告。

进度表述：**四项返修的基础机制已落地；GraphRAG 已接通候选与修订，真实材料的状态语义转换已完成一个片段的链路验证，但自动/模型辅助的批量语义提取尚未完成。** 上述限制是 mapper 的语义覆盖边界，不是合同缺口。


## 阶段调整：披露优先（2026-10-22）

当前主线从“推断故事时间/认知时间”转为“按原文披露顺序整理可回查数据”。

- 新增 `scripts/chapter_disclosure_index.py` 与 `docs/CHAPTER_DISCLOSURE.md`。
- 三章结果：75 实体、620 次实体提及、118 claim（精确 60 / 部分 49 / 未定位 9）、114 relationship（共现定位 30 / 未定位 84）。
- `report.html` 提供章节阅读、原文高亮、条目表和未定位清单；`index.json` 提供结构化数据。
- 该结果是只读披露清单，不写入正式图，也不推断故事时间；未定位项保留原因，不猜位置。
- 既有正式图、统一候选、认知/披露查询和 88 项定向回归不变。

后续以“一章/一段连续原文的可用结果”为推进单位；故事时间只在原文明确时补充。

**第一章正式登记（2026-10-22）**：独立项目 `data/chapter1-disclosure-20261022/`，接受修订 5；登记 9 条披露观察（年龄/身材/肤色、伤情/施动者、老周 statement/plan、第二章肤色），8 个属性定义。披露查询可随阅读位置从“黝黑”切到“白腻细嫩”；故事时间查询仍为 `unrecorded`。第一章 26 条精确 claim 中剩余 17 条仍是工作清单。
