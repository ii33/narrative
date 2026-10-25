# 架构审计：数据核心、模块耦合与界面适配

审计日期：2026-10-22  
范围：`src/narrative/`、Qt Quick 桥接与 QML、CLI、测试跳过策略；不含外部服务与真实模型质量。  
方法：静态导入图、模块规模、入口路径、UI/CLI 能力对照、测试与跳过清单；未做运行时性能剖析。

## 结论摘要

项目的数据核心本身是清楚的：`graph_models`、`graph_store`、`graph_service`、`property_core`、`property_store`、`dependency_core`、`change_service` 之间没有 import-time 循环，核心模块不依赖 Qt/QML。

但外围存在三类高优先级问题：

1. **旧 `Operations` 是一个 101 方法的兼容型上帝门面**，UI/CLI 为了“列项目/开项目”导入它，连带加载旧 v3 Store、Service、extraction、analysis 等 20 个内部模块。
2. **新核心能力没有进入界面**：统一变更候选（`change_*`）和披露状态（`property_disclosure_state`）在 CLI、Qt Bridge、QML 中都没有入口；UI 仍只查故事时间属性。
3. **每次打开图项目都会执行写事务**：`GraphOperations.__init__` 构造 `ChangeService`，后者用 `BEGIN IMMEDIATE` 建 `change_candidates` 表。UI 的每个后台任务都新建一个 `GraphOperations`，读查询也会拿写锁。

## 分层现状

| 层 | 模块 | 现状 |
| --- | --- | --- |
| 领域模型 | `graph_models`, `models` | 两套并存：v5 Pydantic 图模型 + 旧 dataclass 模型 |
| 存储 | `graph_store` (v5), `store` (v3) | 两套 schema 并存；旧 v3 仍被 `Operations` 使用 |
| 核心服务 | `graph_service`, `property_store`, `property_core`, `dependency_core` | 依赖方向基本单向；`graph_service` 反向依赖 `property_store` 常量 |
| 编排 | `change_service` | 依赖 graph + property，合理 |
| 公共门面 | `graph_operations` (66 方法), `operations` (101 方法) | 两个门面，职责过宽 |
| UI 适配 | `native_bridge`, `native_models` | 异步、无直接 SQL；但依赖两个门面 |
| QML | `Main.qml` 2264 行 | 单文件承载多页面，含过滤/排序/JSON 逻辑 |
| 旧 UI | `ui.py` 3000 行, `graph_ui.py` 1079 行 | Streamlit 已停用，测试跳过，代码仍在包内 |

## 高优先级发现

### H1. `Operations` 是旧栈的上帝门面，UI/CLI 为少量静态方法付出全量导入

证据：

- `operations.py` 顶层导入 20 个内部模块，包括 `extraction`、`analysis_*`、`production`、`review`、`service`、`store`、`timeline`（`src/narrative/operations.py:16-47`）。
- `Operations` 有 101 个方法（`grep -c "    def "`）。
- `native_bridge.py:18` 导入 `Operations`；`cli.py:6` 同样导入。
- 实测导入 `narrative.operations` 会加载 24 个 `narrative.*` 模块；只导入 `narrative.graph_operations` 是 12 个。
- 旧 `Store` schema 是 v3（`store.py:49`），新 `GraphStore` 是 v5（`graph_store.py:70`）；两套数据模型/存储同时存在。

影响：

- UI 启动和 CLI 启动被旧栈拖重；旧模块的副作用/依赖会影响新图项目。
- 两套 schema/模型容易产生行为分叉；开发者无法从导入关系判断“当前产品到底用哪套”。
- `Operations` 同时承担旧叙事分析、旧图、v5 图项目静态入口，边界不清晰。

建议：

- 新建 `project_catalog.py`，只提供 `list_projects/create_graph_project/open_graph_project`，直接依赖 `GraphStore/GraphOperations`。
- `native_bridge`、`cli` 的图命令改为依赖 `GraphOperations` + `project_catalog`，不再导入 `Operations`。
- 旧 `Operations` 移到 `legacy/` 或独立可选包；CLI 旧命令保留时显式标注 legacy。

验收：`python -c "import narrative.native_bridge"` 不再加载 `extraction/service/store/analysis_*`；图 UI 的启动模块数降到接近 `graph_operations`。

### H2. 新核心能力没有界面/CLI 入口

证据：

- `grep change_precheck/change_propose/change_accept` 在 `cli.py`、`native_bridge.py`、`Main.qml` 中无匹配。
- `grep property_disclosure_state` 同样无匹配。
- `native_bridge.queryProperty` 只调用 `ops.property_state(node_id, property_id, at)`，没有 `scope`、`holder_id`、`disclosure_cut` 或披露投影（`native_bridge.py:321-333`）。
- QML 属性面板只展示 `propertyResult.state`、历史、world state（`Main.qml:2126-2169`）。

影响：

- 最近实现的统一候选、认知作用域、披露优先记录，用户只能通过 Python/脚本使用，界面无法完成“按阅读位置查看披露记录”。
- 数据核心与界面能力脱节；测试覆盖核心，但用户路径没有验收。
- 作者在 UI 中仍可能用旧的两阶段 graph/property 候选，绕过统一事务。

建议：

- 在 Bridge 增加：
  - `changePrecheck/changePropose/changeAccept/changeReject`
  - `propertyDisclosureState(node, property, cut, scope, holder)`
  - 属性面板增加 scope/holder/披露切点选择。
- 在 CLI 增加 `change-*` 与 `property-disclosure-state` 命令。
- QML 候选页区分 graph/property/change 三类，默认走 change。

验收：从 UI 完成“选择披露切点 → 查看披露记录 → 接受统一候选 → 查询结果”的闭环；不要求一次覆盖全部旧页面。

### H3. 打开项目即写事务，读路径也拿写锁

证据：

- `GraphOperations.__init__` 构造 `ChangeService`（`graph_operations.py:31-35`）。
- `ChangeService.__init__` 调用 `_ensure_table`（`change_service.py:31-35`）。
- `_ensure_table` 使用 `store.connect(write=True)`，即 `BEGIN IMMEDIATE`，执行 `CREATE TABLE IF NOT EXISTS change_candidates`（`change_service.py:37-52`）。
- `native_bridge._project_task` 每个任务新建 `Operations.open_graph_project(path)`（`native_bridge.py:131-133`），线程池 4 个 worker（`native_bridge.py:39`）。

影响：

- 每次 UI 查询/刷新都尝试写 schema；只读项目、只读文件系统、并发查询会被不必要地阻塞。
- 多个 worker 同时 `BEGIN IMMEDIATE`，SQLite 写锁竞争，表现为偶发等待/超时。
- “打开项目”不再是纯读操作，和审计/备份工具的行为不一致。

建议：

- 把 `change_candidates` 建表移到项目创建/显式迁移；打开时用只读探测判断表是否存在，不存在再走一次性迁移。
- `ChangeService` 构造不再执行 DDL；`accept` 时才在写事务内确认表。
- 给 `GraphOperations` 增加 `read_only=True` 路径，UI 查询使用只读连接。

验收：打开已有 v5 项目不产生写事务；4 个并发只读查询无 `database is locked`；只读文件系统可打开查询。

## 中优先级发现

### M1. `GraphOperations` 职责过宽

66 个方法覆盖：图 CRUD/查询、属性、统一变更、时间轴/层级/视图、GraphRAG、模型 provider 配置、分析 run 控制。  
顶层依赖 `harness`；运行时懒加载 `graph_agent`、`analysis_control`、`graph_display`、`ms_graphrag_adapter`（`graph_operations.py:14, 302, 698, 744`）。

影响：数据门面同时是展示适配器、模型配置存储和 run 控制器；任何一类改动都触碰同一个类。

建议：拆成 `GraphReviewFacade`、`GraphQueryFacade`、`GraphTimeFacade`、`ModelProfileFacade`、`AnalysisRunFacade`，`GraphOperations` 只做组合根。内部仍共享 `GraphStore`。

### M2. 新图代理依赖旧代理私有函数

`graph_agent.py:22-31` 从 `narrative_agent` 导入 `_code_hash/_compact/_hash/_json/_read/_save/_usage_totals` 等私有函数；`narrative_agent` 又依赖旧 `models.Operation`。

影响：新 v5 分析链无法独立于旧代理演进；私有函数改名会破坏新链。

建议：抽出 `agent_runtime.py`（Budget、hash、JSON、compaction、usage），两代代理共同依赖；不要跨模块导入下划线函数。

### M3. 旧 Streamlit UI 仍留在包内

`ui.py` 3000 行、`graph_ui.py` 1079 行；`tests/conftest.py:12-30` 明确跳过 9 个 Streamlit 测试文件。入口已改为 `narrative.native_app`（`pyproject.toml:24`）。

影响：包体积、静态检查、依赖安装和安全扫描仍覆盖死代码；新人容易误用旧入口。

建议：移到 `legacy/streamlit/` 或独立分支；默认安装不包含 Streamlit 依赖；保留一个归档说明和恢复方式。

### M4. QML 单文件承载多页面与视图逻辑

`Main.qml` 2264 行、80 个函数、188 个 property；包含筛选/排序/JSON 比较（例如 `Main.qml:321, 466, 501, 850, 1322`）。仅 3 个轴组件拆出。

影响：页面无法独立测试；QML 逻辑与 `native_models` 的投影逻辑重叠；改一个面板容易影响全局。

建议：按页面拆 `GraphPage.qml`、`TimelinePage.qml`、`PropertyPage.qml`、`CandidatePage.qml`；把排序/过滤/状态派生移到 `native_models`，QML 只绑定和发事件。

### M5. `analysis_control` 依赖未声明的鸭子接口

`analysis_control._path(operations, run_id)` 直接访问 `operations.project_info().path`（`analysis_control.py:14-21`），没有 Protocol/类型约束；`GraphOperations` 和旧 `Operations` 都能传入。

影响：接口靠约定，重构 `project_info` 返回值会静默破坏 run 控制。

建议：定义 `ProjectPathProvider` Protocol，或直接传 `project_path`，不要传整个门面。

### M6. 模型 provider 配置写进图项目数据库

`graph_operations.harness_settings/save_harness_settings` 直接读写 `meta` 表的 `harness_settings/harness_profiles`（`graph_operations.py:633-680`）。

影响：图数据导出/备份会携带模型配置；图核心与模型配置生命周期耦合；只读图查询也会触碰 provider 配置。

建议：迁到应用级设置（用户配置目录）或独立表；图项目只保存“使用哪个 profile 名称”，不保存密钥环境变量和完整配置。

## 低优先级发现

- `graph_service` 为能力 schema 导入 `property_store` 的常量（`graph_service.py:20-25`）；常量可移到 `graph_contract.py`，避免图服务反向依赖属性存储。
- `graph_operations.graph_query` 每次加载全部属性记录并计算 eligibility（`graph_operations.py:598-605`）；章节规模可接受，全书规模需要缓存/索引。
- `native_bridge._project` 有一行重复的 `raise ValueError("请先打开图项目")`（`native_bridge.py:113-116`）。
- `graph_display` 是展示辅助，却被 `graph_operations.graph_display_map` 暴露为门面方法（`graph_operations.py:301-308`）；展示逻辑应留在 adapter。

## 界面适配矩阵

| 能力 | Python API | CLI | Qt Bridge | QML |
| --- | --- | --- | --- | --- |
| 图/属性查询 | 有 | 有 | 有 | 有 |
| 故事时间状态 | 有 | 有 | 有 | 有 |
| 披露状态 `property_disclosure_state` | 有 | **无** | **无** | **无** |
| 认知 scope/holder 查询 | 有 | 部分（property-state 参数） | **无** | **无** |
| 统一候选 `change_*` | 有 | **无** | **无** | **无** |
| GraphRAG 预览 | 有 | 部分 | 无 | 无 |
| 模型 provider 配置 | 有 | 无 | 有 | 有 |
| 分析 run 控制 | 有 | 有 | 有 | 有 |

## 健康项

- Qt Bridge 明确使用 `ThreadPoolExecutor`，数据库/模型调用不在 GUI 线程（`native_bridge.py:1-5, 39`）。
- QML 没有 `XMLHttpRequest`、`File`、`Process`、SQL 等直接 IO；所有数据经 bridge。
- `graph_models`、`graph_store`、`dependency_core`、`property_core` 不依赖 Qt/QML。
- 顶层导入图无环；`graph_operations ↔ graph_agent` 的互相引用是函数内懒加载，不是 import-time 循环。
- `native_models` 是 Qt-independent 投影层，`CanvasState` 明确是 view cache。
- 旧 Streamlit 测试显式跳过，决策可追踪。

## 建议顺序

**P0（先做，低风险）**

1. 去掉打开项目时的 DDL 写事务；把建表移到创建/迁移。
2. 增加 `project_catalog.py`，让 `native_bridge`/CLI 图命令不再导入旧 `Operations`。

**P1（界面适配）**

3. Bridge/CLI 暴露 `property_disclosure_state`、`change_precheck/propose/accept/reject`。
4. QML 属性页增加 scope/holder/披露切点；候选页默认统一候选。

**P1（结构）**

5. 拆分 `GraphOperations` 为多个 facade；保留组合根。
6. 抽 `agent_runtime.py`，移除对 `narrative_agent` 私有函数的依赖。

**P2（清理）**

7. 归档旧 Streamlit/旧 v3 Store/Service；默认安装不加载。
8. 拆 `Main.qml` 页面，排序/过滤移入 `native_models`。
9. 定义 `analysis_control` 的 Protocol 或直接传路径。
10. 模型 provider 配置迁出图数据库。

## 审计边界

- 本报告是静态审计，没有跑完整 UI 交互或性能基准。
- 真实模型质量、GraphRAG 检索质量不在本次范围。
- 行号基于 2026-10-22 工作区；重构后需重新核对。


## 附：本次测量

- 模块规模：`ui.py` 3000 行、`Main.qml` 2264 行、`operations.py` 1362 行、`graph_operations.py` 772 行、`native_bridge.py` 663 行。
- 方法数：`Operations` 101、`GraphOperations` 66。
- 导入模块数：`narrative.operations` 24 个 `narrative.*`，`narrative.graph_operations` 12 个。
- 顶层导入环：0；函数内懒加载的相互引用：`graph_operations` 与 `graph_agent`。
- 现有定向回归：88 passed（2026-10-22 审计时）。

## 整改结果（2026-10-22）

| 项 | 状态 | 结果 |
| --- | --- | --- |
| P0.1 打开项目写事务 | 完成 | `change_candidates` 加入新项目 schema；`ChangeService` 构造不再建表；首次 `propose` 懒迁移；新增回归 `test_opening_project_does_not_create_change_table` |
| P0.2 项目目录 | 完成 | 新增 `project_catalog.py`；`native_bridge` 不再导入旧 `Operations`（导入模块 24→15，旧栈 0）；CLI 图命令走 catalog，旧 `Operations` 用惰性代理 |
| P1.3 披露/统一变更接口 | 完成 | CLI 增加 `property-disclosure-state`、`change-precheck/propose/candidates/candidate/accept/reject`；Bridge 增加 `queryPropertyDisclosure`、`loadChangeCandidates`、`changeCandidateDetail`、`accept/rejectChangeCandidate`、`proposeChange`；QML 属性页增加披露切点/scope/holder 面板 |
| P1.5 拆 GraphOperations | 完成 | 实现下沉到 `graph_read_ops.py`、`graph_timeline_ops.py`、`graph_profile_ops.py`、`graph_analysis_ops.py`、`graphrag_ops.py`；`graph_facades.py` 只依赖各自服务；`GraphOperations` 815→459 行，全部方法为单行委托，新增 `tests/test_graph_facades.py` 守住边界 |
| P1.6 共享代理运行时 | 完成 | 新增 `agent_runtime.py`（Budget/哈希/JSON/journal/compact/usage）；`graph_agent` 不再导入 `narrative_agent` 私有函数 |
| P2.7 归档旧 UI/v3 | 完成 | Streamlit UI、旧 CLI 命令、v3 `store/service/models/extraction/analysis_*/jobs` 等模块与对应旧测试已删除；新图路径使用 `base_model.py`/`leases.py`；CLI 仅保留图/属性/披露/变更命令 |
| P2.8 拆 QML | 完成 | 六个页面组件：`HomePage`/`GraphPage`/`CandidatePage`/`SourcePage`/`AgentPage`/`PropertyPage`，加 `PropertyDisclosurePanel`；`Main.qml` 2203→1201 行，只保留窗口外壳、共享模型与导航 |
| P2.9 analysis_control Protocol | 完成 | `ProjectPathProvider` / `ProjectPathLike` Protocol，`_path` 不再接受任意门面 |
| P2.10 provider 配置 | 完成 | 新增应用级 `provider_store.py`（`~/.narrative/provider-settings.sqlite3`，按 project_id 隔离）；图 DB 只保留旧数据只读回退 |

验证：

- `tests/test_operations.py`、`test_context_entrypoints.py`、`test_native_bridge.py`、`test_native_app.py`、`test_change_candidate.py`：30 passed。
- 核心图/属性/依赖/认知：70 passed。
- `test_native_models.py`、`test_native_property.py`、`test_graph_agent.py`：19 passed。
- `test_analysis_control_trace.py`：5 passed。
- `test_native_app.py`（offscreen）：7 passed。
- 清理旧栈后全量测试：**180 passed**（约 5 分钟）。
- Ruff/Pyright：通过。

P1.5 与 P2.8 收尾（2026-10-22 第二轮）：

| 层 | 结果 |
| --- | --- |
| 实现 | 5 个服务模块承载读路径、故事时间、模型配置、分析运行、GraphRAG 预览 |
| 门面 | 8 个 facade 只依赖各自服务，不再持有 `GraphOperations` |
| 兼容层 | `GraphOperations` 每个方法一条 `return self.<facade>.<method>(...)`，815→459 行 |
| 界面 | 5 个页面拆为独立组件，`Main.qml` 只留窗口外壳、共享模型、导航和跨页辅助函数 |
| 跨页引用 | 反向（页面 → 窗口）：`GraphPage` 在 `Component.onCompleted` 把 viewport/scene/阅读进度/搜索框发布到窗口属性，窗口级 `fitGraph`/`centerQueryFocus`/`search` 通过属性访问。正向（页面 → 共享模型）：`graphNodesModel` 等 `ListModel` 仍在窗口层，页面沿用 QML 创建上下文的 id 解析，与拆分前行为一致；未改成 `root.`，因为 `root` 上并没有这些属性 |


验证：全量 224 passed（含新增 `tests/test_graph_facades.py` 44 项）；Ruff/Pyright 通过。

仍未完成（可选）：

1. 排序/过滤等纯数据逻辑仍在 QML，可迁入 `native_models`。
2. `GraphPage.qml` 约 560 行仍偏大，可再拆图/时间轴/表格三个子视图。
3. 共享 `ListModel` 仍在窗口层；若要彻底解耦，可把它们提升为 `native_models` 暴露的模型对象，页面通过 `root.models` 访问。
