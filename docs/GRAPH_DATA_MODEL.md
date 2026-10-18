# 节点—断言—时序：v4 数据模型

## 目标与边界

v4 是新建项目的主模型。旧 v3 项目、原始 Library 附件与历史报告原样保留；图项目使用独立 SQLite 文件，`Operations.open_graph_project` 在写入前检查版本并拒绝旧库。UI、CLI 和 agent 只经 `Operations` 返回的 `GraphOperations` 门面读写，不把待审候选或模型草稿混入正式图。

图的事实来源只有一处：**Assertion（断言）**。人物、物件、地点、事件、场景和故事线都是稳定身份的 Node；它们之间的参与、关系、地点、属性和先后均由类型化 PredicateDefinition + Assertion 表达。Node 本身只保存身份、别名、类型和必要的发生区间元信息，不塞有证据或可争议的属性。事件是一等节点，却仍可由谓词域、参与角色和时间规则施加专门约束。

## 逻辑结构

| 对象 | 关键字段 | 约束与语义 |
| --- | --- | --- |
| Node | `id`, `node_type`, `name`, `aliases`, `context_id`, `occurrence?` | ID 指个体，名称不是唯一键。同名可为不同节点，别名是同一节点的检索标签。`occurrence` 只供事件节点。 |
| PredicateDefinition | `id`, `label`, `semantic`, `subject_types`, `object_types`, `value_type`, `functional`, `temporal_relation?` | 受审核的谓词注册。semantic 为 attribute、relation、participation、temporal 或 containment；temporal 必须声明 before/after/same/overlaps/contains 中的关系，containment 则表达阅读组织。类型域和宾语范围参与预检。 |
| Assertion | `subject_id`, `predicate_id`, `object_id` 或 `value`, `scope`, `holder_id?`, `context_id`, `polarity`, `modality`, `certainty`, `validity`, `disclosure?`, `origin`, `assessment`, `derived_from`, `supersedes_id?`, `qualifiers`, `temporal_left/right?`, `order_index?` | 一条有立场、有时间、有出处的命题。claim/knowledge 必须有人物 holder，world 不得带 holder。`order_index` 仅供 containment 的阅读顺序。`supersedes` 只表示显式修订谱系，不能自动决定有效期。 |
| Evidence | `source_id`, `source_hash`, `start`, `end`, `quote` | 紧贴不可变原文的 Unicode 字符区间；哈希和切片在预检中精确核对。 |
| EvidenceLink | `assertion_id`, `evidence_id`, `role` | 多对多，支持 supports/refutes/mentions。一份原文证据可支撑多个断言；每个断言至少关联一份证据。 |
| Context | `id`, `context_type`, `parent_id?`, `holder_id?` | canon、branch、hypothesis、viewpoint。查询显式选择语境，不把不同分支和人物认知合并成世界事实。 |
| Timeline | `id`, `context_id`, `calendar?` | 故事时间轴，可无日历，不强造绝对日期。 |
| SourceOrder | `source_id`, `ordinal`, `context_id` | 经审核显式登记的叙述披露顺序；同语境序号不得重复。与 Assertion.disclosure 的原文位置组成阅读切点，导入顺序不参与推断。 |
| Candidate/Revision | `expected_revision`, `version`, `status`, `objects`, `audit` | 整批预检、待审、人工接受或拒绝。接受同一事务写入所有对象并保存前后审计。 |

物理库以 `objects(id,kind,data,accepted_revision)` 保存类型化对象，`evidence_links` 做独立多对多索引，`sources` 保存精确原文，`candidates` 和 `revisions` 保存审核与审计。断言的主语、宾语、持有人和谓词有 SQLite 表达式索引。这个实现保留关系型事务与审计，不依赖外部图数据库；UI 所见边由已接受断言投影生成，不另存第二套“真值边”。

## 统一断言的几种形态

事件 `e1=甲在码头送别乙`、人物 `p1=甲`、`p2=乙`、地点 `dock=码头` 是节点：

- `e1 —participates{role:送行者}→ p1` 和 `e1 —participates{role:离开者}→ p2` 是 semantic=participation 的两条断言。
- `e1 —occurs_at→ dock` 是 semantic=relation 的断言。
- `key —lost→ true`、`scope=claim`、`holder_id=p1` 表示“甲说钥匙丢了”。人审接受该记录，仅表示这句主张有依据；它不改变钥匙的世界持有状态。
- `e1 —before→ e2` 是 semantic=temporal 的断言，左右 TimeRef 可分别指定 `e1.end` 和 `e2.start`。它是证据化偏序，不将两个 Event ID 假装成瞬时 Timepoint。

关系或特点若是模型解读，必须 `origin=inference` 且 `derived_from` 指向依据断言。被引基础断言撤回，或在派生结论接受后又修订，查询标出 `invalid_dependency`/`stale_dependency`，不静默把派生结论当可靠事实。Node 的固有名亦可在修订候选中更正，旧值留在审计里。

## 时序与查询

`TimeRef` 有 number、datetime、event_boundary 三种；后者须明确 event_id 与 start/end。`TimeExtent` 显式分为 point 与 interval；点只带一个 `point` 引用，区间起止分别标记 `known(ref)`、`unknown` 或 `unbounded`，终点排除。时间关系本身仍是断言：`before/after/same/overlaps/contains` 由 predicate 注册，严格 before/after 的环会预检报错。只有已明确的关系参与偏序；未能比较的事件保留未知，不按文本顺序、录入顺序或节点 ID 强排。

查询同时接受故事切点 `story_cut`、文本披露切点 `disclosure_cut`、审核切点 `as_of_revision`、语境 `context_id`。未知有效期或披露位置在对应切点下以 `uncertainties` 报告，不冒充可知；隐藏或失效的断言不进入当时读图。当前实现对数值故事时点可给确定的 active/future/expired；事件锚与部分序若不足以定位切点会报告 unknown。文本切点要求 SourceOrder，未登记顺序不猜测。系统审核时间由不可覆写的 revision/audit 表示，不混成故事时间。

例如第 2 天甲交钥匙给乙，第 5 章才披露：若证据明确，`key—holder→甲` 的有效期可在第 2 天结束，`key—holder→乙` 自交接结束生效。第 3 天的故事切点与第 1 章的披露切点得出不同可见范围。关闭旧区间必须提交带证据的修订；新值不会“最新写入自动胜出”。不同 scope、holder、context 的命题不会彼此终止；同语境的 functional 谓词在已知重叠区间取不同值时提示人工冲突。

`Operations.graph_timeline(candidate_id=None, context_id='canon', story_cut=None, disclosure_cut=None, as_of_revision=None, limit=2000)` 复用正式查询的可见性和审核边界，返回 `events`、`known_time`、`relative`、`unknown`、`lanes`、`temporal_edges`、`organization_edges` 与 `uncertainties`。显式候选预览遵循 `graph_hierarchy` 的预检、状态和版本约束；默认不混入待审对象。带故事或披露切点时只使用查询实际暴露的身份，不从无切点快照补未来节点名。

`known_time` 仅表示事件 `occurrence` 中真实 number/datetime 坐标，`lanes` 按 `timeline_id` 与 `form` 分轴；不同时间轴、日期坐标与数值坐标不能混排。区间仅一端可定位、含事件锚或跨坐标轴时标记 `time_status=partial`，未知或无界端点原样保留，不补结束或死亡时点。`relative` 表示没有绝对坐标但有显式关系的事件，`unknown` 保留其余时间未明事件。组织 `order_index` 与层级 `depth` 只服务阅读和显示，不补故事坐标或全局先后排名。

`temporal_edges` 保留原始断言、证据、`temporal_relation` 与 `temporal_left/right`。`display_relation`、`display_left/right` 和 `display_subject/object_id` 仅为显示归一化：after 可以反向显示为 before，不能改写原始命题。before/after 比较的是所给左右时间引用或事件边界；例如“甲事件开始早于乙事件结束”不等于“甲事件整体先于乙事件”。边界中的事件身份与主宾不一致时报告不确定性，不误连主宾事件。只有 `world/positive/actual`、未撤回且依赖有效的关系进入确定关系投影；认知、否定、假设、撤回或依赖失效保留在 `uncertainties`，不参加排序。一般时序断言的 `supersedes_id` 仍只记录修订谱系，不自动让旧时序失效；组织包含的显式父边替换是下述专门约束。

`relative_axes` 仅将 event_boundary 对 event_boundary 的显式 before/after/same 约束投影为分组相对数轴；number/datetime 不改写成序号，仍由 `lanes` 使用真实刻度。各连通组独立，序号仅供排列，先后以显式连线为准：不同序号不必可比较，同序号不代表同时，只有 same 约束才合点。同事件已出现的开始与结束可通过 `layout_connections` 作“开始不晚于结束”的弱布局连接，不增加已审核事实或正时长。overlaps/contains 保留原关系，不强排；冲突组报告不确定性。分组按可读性排序，不代表组间先后，不继承父子事件时间。

`graph_timeline` 还返回 `nested_axes={root_axis_id:'root', layers, unplaced_event_ids, truncated}`。根层只含当前可见组织结构中没有事件祖先的事件；故事线、场景是透明组织容器，不成为时间节点。事件增加 `parent_event_id`（最近事件祖先）、`axis_id`、`child_event_ids` 和 `child_axis_id`；无下一层时后者为 null。层 ID 为 `root` 或 `event:<owner_event_id>`，层带 `owner_event_id`、`parent_axis_id`、`event_ids/events` 及各自的 `lanes/known_time/relative_axes/relative/unknown/relations/uncertainties`，点击事件可沿 `child_axis_id` 逐层展开。层级随同一语境、阅读切点、审核版本或显式候选预览重新投影；“关键事件”仅指当前层事件，不作无证据的重要性评分。

各层仅用两端均在该层的原始时序断言定位；跨层原边保存在顶层 `relations`，并在相关层的 `cross_level_relations/cross_level_relation_count` 提示，不汇总成父事件时序。坐标、相对约束和未定事件继续分别呈现；父子关系、稳定 ID 顺序、阅读顺序均不提供时间坐标或可比性。若数量截断使祖先缺失，子事件列入 `unplaced_event_ids` 并标 `truncated`，不提升成根事件；原有平面 `events` 等字段仍保留。

原生画布的折叠与筛选都是显示状态：收拢只移除该次展开贡献且未被其他展开节点引用的节点和边，保留初始锚点及其时序边、其他展开引用和用户坐标。显示筛选不改业务图；类型或关系筛选缺省/None 表示全部，显式空列表表示不显示。事件优先初始图可展示两个可见锚点之间的原始时序边；`temporal_certain` 区分确定世界时序，其余关系仍需保留认知和依赖状态标记。

## 公开操作与审核

入口：`Operations.create_graph_project(path)` / `open_graph_project(path)`；项目清单 `Operations.list_projects(directory)` 只读目录中的 SQLite 头与项目元信息，不打开旧库。图门面提供 `import_source`, `sources`, `source`, `locate`, `graph_schema`, `graph_snapshot`, `graph_query`, `graph_neighborhood`, `graph_hierarchy`, `graph_timeline`, `graph_precheck`, `graph_propose`, `graph_candidates`, `graph_candidate`, `graph_edit_candidate`, `graph_select_operations`, `graph_accept`, `graph_reject`。若只保留候选子集，必须重新预检且 version 增加；批内引用被移除会报错，不能部分接受到悬挂状态。模型 agent 使用同一入口，只有待审候选由人工接受后进入正式图。

`graph_neighborhood(center_id, depth=1..3, node_types?, context_id?, story_cut?, disclosure_cut?, as_of_revision?)` 在与 `graph_query` 相同的已审核、语境和时间切片上做双向遍历。结果 `nodes` 保留事件等中间节点；`endpoints` 才按类型过滤；`edges` 始终是原始有向直接断言，`paths` 给出按序断言 ID、经过节点和每步正向/反向遍历。两跳以上路径标成 `derived=true,direct=false`，只供探索，**不产生人物到地点等新直接事实**。若中心在阅读切点下尚不可见，返回 `center_status=not_visible_or_truncated` 和空邻域；数量上限命中时标 `truncated`，不能把结果当全图枚举。

图分析 agent 的工作集仅为私有草稿。一次 `stage` 可暂存多个对象；准备提交时，模型须单独调用 `graph_precheck({})`，观察整批诊断、正式修订号与工作集哈希，再单独调用 `graph_propose({})` 生成待人工审核的候选。`done` 或覆盖说明不会暗中触发预检、提交或接受。暂存对象变化、正式修订变化后旧预检失效，必须重新预检。运行器以工作集、独立证据、有效工具观察、预检诊断和候选变化判断实质进展；反复无效动作或空等待即使持续改写覆盖文字也会有界停止，并保留原始响应与诊断供人审视。
`plan`、`coverage` 只用于前台展示；模型给出文字、列表或嵌套摘要时会整理为可显示的文字，原始响应仍原样留存，合法工具照常执行。工具参数、图对象和预检合同继续严格校验，不从展示文字猜测或修补事实。

## 阅读组织层级（`graph-nesting/2`）

存储仍是 v4：用 `semantic=containment` 的谓词和有原文证据的断言表达父子节点，不增添物理表、不迁移旧平面图。它与时序谓词 `temporal_relation=contains` 完全不同。`subject_id` 为父节点，`object_id` 为子节点。`order_index` 是可选非负整数，只能用于组织包含，表示同父子节点的阅读顺序；其后按已登记的原文披露位置、断言 ID 稳定排序。这个顺序不推出故事发生时间。

允许故事线包含故事线、场景或事件；场景包含场景或事件；事件包含事件。人物、物品、地点仍是共享实体，由参与或关系断言引用，不能成为组织子节点。有效组织边须是 `positive/world/actual`、同一语境、未撤回，且有关联 `supports` 原文证据；`origin=original` 或有有效 `derived_from` 依据的 `inference` 均可，`creative/production` 不作为正式组织边。推断依赖失效或陈旧时，查询不投影该边。预检禁止自包含、任意深度环、重复父子边和一个子节点有多个父节点。显式 `supersedes_id` 只可替换同语境、同子节点的旧组织边（可更换父节点）；将既有断言修订为 `assessment=retracted` 可撤回其组织效力。组织边不会继承参与人、结果或时间。

`Operations.graph_hierarchy(candidate_id=None, context_id='canon', story_cut=None, disclosure_cut=None, as_of_revision=None, limit=2000)` 返回 `{revision, candidate_id, pending_excluded, roots, nodes, edges, truncated, uncertainties}`。节点带 `parent_id`、`child_ids` 和 `depth`；边保留完整有效断言与证据。默认只看正式图，显式待审候选须先通过预检才合并预览。`graph_candidate_preview` 也提供 `hierarchy`。带故事或披露切点时只使用对应查询实际可见的节点和断言，绝不拿无切点总览补未来身份；未归属实体保留为根，界面可按类型筛选。旧平面图可照常查询，节点全为根。

事件没有永久“主事件/子事件”等级。重组批次不可同时改写既有事件 ID、已接受名称、类型、语境及发生区间；文本推进时可新增上层事件，再以新组织断言 `supersedes_id` 重挂既有事件。旧关系不删除。修订须同语境、同子节点，新修订显式 `disclosure={source_id,start}` 且来源已登记 `source_order`；双方已知时披露不可倒退；禁止替代链分叉和循环。已接受组织边的父子、谓词、语境、披露、前驱与排序字段不可原地修改；已登记来源顺序不可改写。预检在每个披露边界和最终结构分别检查单父、重复和无环。历史无披露组织仍可无切点查看；允许一次衔接至有明确披露的新修订并给出 warning：此前阅读位置未知，不能还原早期归属，可查审核历史。不猜补旧披露；无关候选不会被未改动旧链的缺披露阻断，新修订缺披露仍为 error。未知旧边不进入已知阅读切片。

`graph_query.assertions` 对组织边标注 `organization_status=active/superseded/unresolved`，保留历史断言及证据；`edges`、邻域与画布只使用 active 组织边。替代状态根据完整可见切片计算，不受查询筛选或 limit 影响。非组织边的 supersedes 行为保持原状。阅读切点在后继披露前显示旧关系，披露后显示有效后继；若后继撤回或依赖失效，旧关系不会自动复活，当前未归属并给出不确定提示。要恢复旧父，须新增有证据的修订明确指回旧父。`as_of_revision` 独立选择审核版本。纯名称等事实纠错仍沿既有审核版本机制回看；对象名称尚未按阅读位置版本化，不可宣称所有字段都支持阅读回看。

`graph_hierarchy(..., focus_id=None)` 保留全树并返回 `focus_id`，节点增加 `parent_edge_id`，选定视点时 `relative_depth=depth-focus.depth`；它是相对当前视点的深度，不是永久事件等级。视点不可见时报错。`graph_event_index(context_id='canon', disclosure_cut=None, as_of_revision=None, focus_id=None, limit=2000)` 为增量模型提供紧凑 `events`，包含 `id/name/parent_id/parent_edge_id/active_parent_edge_id/child_ids/depth/relative_depth/disclosure/origin/derived_from/chain_tip_id/history`，以及 `revision/truncated/containment_predicates/source_orders/uncertainties`。`history` 仅列当前切点可见的组织边摘要，`chain_tip_id` 包括已撤回或失效的最新修订，供显式修复；不以完整快照替代索引，截断时必须继续读取或报告范围不足。

## 当前实施的明确边界

- 原文是新项目内按标题+内容哈希固定的快照；不直接覆盖，修正文稿应导入新来源。跨项目或 Library 附件的自动迁移未实现。
- 谓词定义受候选审核，已采纳后不可原地改写；若语义变化，新建带版本的谓词 ID，避免旧断言被静默重新解释。版本谱系可用命名/说明维护，尚无专用继承机制或完整本体推理引擎。
- 时间可以表达事件边界与证据化部分序，查询对无法数值定位的事件锚诚实返回 unknown；没有自动将任意约束求解成绝对时间，也不支持并行宇宙的复杂合并规则。
- 查询选择分支及其祖先，仍保留所有可见相冲断言及各自来源，不做默认“分支覆盖正篇”或最新值胜出。跨分支同名节点不会自动判定同一身份。
- 没有隐式 `persist_until_changed`：新状态不会自动关闭旧状态；只有可审核且有证据的有效期修订或显式时间关系能让查询判定交接前后。无依据时保持未知或提示冲突。
- 带披露切点的 `graph_query` 只返回可见断言及其引用节点，避免未来节点名经图总览或姓名搜索泄漏。`graph_snapshot` 和 `graph_display_map` 是无切点的正式全集/审核投影，界面不能拿它们补全阅读切点下的图。
- 候选可以整批编辑或保留子集后重预检；v4 没有把旧库资料自动导入候选。真实模型输出与审核后的正式图应分别报告。
