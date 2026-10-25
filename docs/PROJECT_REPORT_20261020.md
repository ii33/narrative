# 项目报告：叙事 · 证据工作台（2026-10-20）

## 1. 项目目标

把长篇叙事文本变成一套**每条结论都有原文证据、每个状态都能按故事时点重建、模型只能提案、人负责薄弱与低置信环节**的本地资料系统，并在这套资料上做连续性检查、局部补全和受控创作。

三层目标：

| 层 | 目标 | 当前阶段 |
|---|---|---|
| 产品 | v0.1 证据工作台 → v0.2 状态与检查 → v0.3 局部补全 → v0.4 场景资产交付 → v0.5 受控创作 | v0.1 已交付；v0.2 进行中 |
| 工作方式 | 最终模型承担大部分阅读、抽取、校验、初审和常规接受；人工只处理薄弱、低置信、冲突、高风险 | 当前仍是「模型提案、人工接受」 |
| 数据／方法 | 节点身份 + 断言 + 属性记录；证据精确、未知不猜、可审计修订 | v5 属性模型已实现 |

## 2. 数据模型（v5）

**两类权威记录，SQLite 单一正式源**：

1. **Node（节点）**：人物、物品、地点、事件、场景、故事线。稳定 ID；名称不是唯一键；事件是一等节点。
2. **Assertion（断言）**：表达时序（`temporal`）、阅读组织（`containment`）和有立场的主张（`claim`/`knowledge`，带 `holder_id`）。`subject_id + predicate_id + object_id/value + scope + validity + evidence`。
3. **属性记录（v5 新增，替代 attribute/relation/participation）**：
   - `property_definition` 类型／基数／延续策略
   - `property_initial` 初始值
   - `property_observation` 时点基线观察
   - `event_change` 事件 start/end 边界的原子效果批（含 `expected` 前置校验）
   - `property_retraction` 系统更正
4. **Evidence / EvidenceLink**：不可变原文 + Unicode 字符区间 + 引文；多对多支撑。
5. **Candidate / Revision / Audit**：整批预检、待审、人工接受或拒绝；接受为原子事务。

**状态语义**：`known / unknown / absent / unrecorded` 四态区分；`unrecorded` 不是 `unknown`；已知空集合是第四态。无隐式 `persist_until_changed`；不可比较的并行更新报告 `conflict`，不用 ID 或提交顺序猜赢家。反查（人物持有哪些物品）从权威属性推导，不写第二份事实。

**时间**：故事切点、披露切点、审核切点（`as_of_revision`）分开；偏序用事件边界；无法证明先后保留未知。

**不是正式事实的部分**：Microsoft GraphRAG sidecar 的实体、关系、claims、社区报告只作检索提示，不写正式图；正式数据只来自人工接受的候选。

## 3. 本轮执行（用户三项决定）

### 3.1 停用 Streamlit（已完成）

- README / PLAN / PROPERTY_ACCEPTANCE 改为 **Qt Quick 唯一入口**；`narrative ui` 明确报错；`start.cmd` / `start.ps1` 转启原生窗口。
- `tests/conftest.py` 跳过 9 个 Streamlit UI 测试文件（可逆、有可见理由）。
- 代码保留但不再维护；`pyproject` 的 streamlit 依赖暂未移除。

### 3.2 整章属性验收（Ark，暂不设预算）——通过

- 复用 graph-agent/10 三章图候选（263 对象：23 事件、62 节点、47 断言、104 证据，0 诊断），人工接受为夹具，修订 1→2。
- **第一轮 Ark 运行**：15 请求、589,089 tokens、24 条属性记录、`property_precheck` **0 诊断**；运行 `incomplete`（提出候选后重复无进展）。
- **语义审查发现 3 条高风险**（计划当位置、“融合”当 holder、刀具位置当持有）、3 条中风险。`property_accept` 只能整批接受，**整批拒绝**。
- **第二轮加严格语义约束**：12 请求、322,497 tokens、12 条记录、**0 诊断**，3 条高风险全部消失，剩 2 条中风险（镯子引文未点名“纳戒”；老周位置依赖事件上下文）。
- **用户接受，修订 2→3**。接受后查询：`i_tanbo.holder = p_liqiang`；`p_liqiang.location` 在 `e_lake.start = l_sehu`、`e_confront.start = l_hotel`；`p_fushan.location` 在 `e_fushan_appear.start = l_sehu`。
- 这是**第一个通过预检并被人工接受的整章属性候选**（三章样本，不是质量基线）。运行状态仍是 `incomplete`，与候选有效分开记录。

### 3.3 GraphRAG 中文提示词重建——通过（第 5 次）

- 新 root `data/ms-graphrag-threechapters-zh-20261020/`：`extract_graph` / `extract_claims` / `summarize_descriptions` / `community_report_*` 改为简体中文输出、实体名保持原文写法，实体类型改为中文。
- 前四次失败都在输出截断：claims 或社区报告 `finish_reason=length`；预算网关按设计停机，社区报告因空结果 `KeyError`。根因是输出上限，不是中文解析。
- 第 5 次：claims 限 20 条/4000 字、社区报告限 5 findings/3000 字，输出上限 10000，**18 请求 / 87,746 tokens / exit 0 / complete**。
- 产物：3 documents / 15 text units / **75 entities** / **114 relationships** / **17 communities / 17 reports** / **118 claims**；**75/75 实体标题为中文**，社区报告摘要汉字占比 87.6%。
- claims 精确引文 **60/118（50.8%）**，高于英文索引的 41/113（36.3%）。查询套件 **4/4**，引用数 12/11/29/11（英文索引 6/6/11/5）。
- 交叉审核：31 matched / 20 unit_only / 9 missing / 2 no_evidence；人物 8/8、物品 15/17、地点 6/7；事件仍是 15 unit_only + 8 missing。GraphRAG 能查实体覆盖，不能查事件边界与顺序。

## 4. 环境与工程状态

- `.venv` / `.venv-graphrag` 因工作区 **Low 完整性**导致 venv launcher 报 exit 107，已用 `icacls ... /setintegritylevel (OI)(CI)Medium /T` 修复；`scripts/fix_venv_integrity.ps1`、`scripts/setup_graphrag_env.ps1`、`requirements-graphrag.lock.txt`（146 包，`graphrag==3.3.0`）固化。
- `uv run pytest / ruff / pyright / narrative` 全部恢复。
- 定向回归通过：属性与图代理 **49 passed**、图结构 **42 passed**、GraphRAG 适配器与查询工具 **4 passed**；Ruff 通过。
- **全量套件未确认**：跳过 UI 后一次全量运行为 487 passed / 33 skipped / **325 errors**，但 `tests/test_timeline.py` 单独跑 23 passed。全量运行时正在跑 GraphRAG 索引，怀疑资源/隔离干扰，需在空闲时复跑定位。

## 5. 阻塞与风险

1. **真实抽取质量仍不达标**：中文索引 claims 精确引文 60/118（50.8%），英文索引 41/113（36.3%）；GraphRAG drift 对三种模型全部失败（结构化输出不遵循）；事件覆盖仍是 15 unit_only + 8 missing，GraphRAG 无法逐事件对齐。
2. **全量测试状态未确认**：跳过 UI 后一次全量运行为 487 passed / 33 skipped / 325 errors；`test_timeline.py` 单独跑 23 passed，需空闲时复跑定位资源/隔离干扰。
3. **GraphRAG 产品化缺口**：无 UI/CLI 入口、无增量索引、索引失败需手工续跑；中文索引经过 5 次尝试才完成。
4. **“模型承担大部分”缺机制**：没有置信度校准、自动接受阈值、抽样复核、回滚；本轮整批拒绝就是证据（模型通过了预检但语义不达标）。
5. **规模未验证**：只有三章 16k 字样本；全书成本、显存、时间未知。
6. **环境仍可能复发**：工作区 Low 完整性问题未根治，新建 venv 可能再触发。

## 6. 建议下一步（按优先级）

1. 中文索引已完成（查询套件 4/4、claims 60/118 精确）；下一步把 20 条 `SUSPECTED` claims 接入候选审核，作为人工复核提示，并确认事件覆盖是否只能停在章节级。
2. 空闲时重跑全量测试，确认 325 errors 是否为资源干扰。
3. 定义“模型初审 + 人工低置信复核”的置信度契约（预检诊断、证据唯一性、模型自评、历史准确率），这是“模型承担大部分”的前提。
4. 用第二本三章样本复跑属性闭环，判断本轮结果是否可复现。
5. 全书成本评估（GraphRAG 与图属性各一份），再决定是否扩大样本。

## 7. 关键路径

| 主题 | 位置 |
|---|---|
| 数据模型 | `docs/GRAPH_DATA_MODEL.md`、`docs/PROPERTY_STATE_CONTRACT.md` |
| 属性验收记录 | `docs/PROPERTY_ACCEPTANCE.md` |
| 整章属性运行 | `data/example-run/property-acceptance-20261020/` |
| GraphRAG 部署 | `docs/GRAPHRAG_DEPLOYMENT.md`、`docs/MS_GRAPHRAG_STANDARD.md` |
| GraphRAG 中文索引（已建成） | `data/ms-graphrag-threechapters-zh-20261020/` |
| GraphRAG 交叉审核 | `docs/GRAPHRAG_CROSS_CHECK.md`、`scripts/graphrag_cross_check.py` |
| 验收脚本 | `scripts/run_property_acceptance_existing.py`、`scripts/graphrag_query_suite.py` |
| 项目约定 | `AGENTS.md`、`README.md` |
