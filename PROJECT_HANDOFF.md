# Narrative 项目交接与目录盘点

盘点时间：2026-10-19  
仓库根目录：C:\a\wk\narrative  
本文件是本轮唯一新增文件。经用户确认后，仅删除下方清单中的 80 个忽略的测试/缓存目录；未删除清单外文件，未移动、暂存或提交项目文件；未运行测试、未调用模型、未推送。清理前的所有未提交工作均保留，清理后 Git 状态与清理前完全一致。

## 项目与版本

- 包名/版本：narrative-core 0.1.0；要求 Python 3.12+。
- Git 根目录：C:\a\wk\narrative；当前分支 main；HEAD 为 8c2c043（2026-10-18，Initial public source baseline）。
- 远端：origin = git@github.com:ii33/narrative.git；cnb = https://cnb.cool/nonereason/narrative.git。main 与 origin/main 无领先/落后提交。
- Git 工作区：写入本报告前有 28 个已跟踪文件修改、37 个未跟踪文件、0 个已暂存（共 65 项）；本报告新增后当前为 28 个已跟踪修改、38 个未跟踪、0 个已暂存（共 66 项）。除本报告外，其余未提交源码、测试、文档、脚本和样例均是既有工作，后续清理不得触碰。
- 当前实现版本：src/narrative/graph_store.py 中 GRAPH_SCHEMA_VERSION = 5；graph_operations.py、graph_service.py 及属性状态契约也标为 v5。README.md 与 GRAPH_DATA_MODEL.md 仍称主工作台/数据模型为 Schema v4，文档标记和当前代码版本不一致。包版本 0.1.0、图数据库 Schema 5、产品路线 v0.x 是不同层次，不应混作一个版本号。
- 主要依赖/架构：Python、Pydantic、SQLite；UI、CLI、模型 Agent 通过 Operations/GraphOperations 门面访问业务；property_core 是纯状态计算核心，不负责 SQLite、Qt 或模型调用。

## 目标与设计原则

项目从“节点—断言—时序”图模型发展到 v5 的节点属性状态模型：节点保持人物、物品、地点、事件等身份；属性定义、初始值、观察、事件变更和撤回记录构成权威状态历史；动作通过事件在 start/end 边界改变属性。事件顺序允许部分顺序、并行分支和未定时间，无法证明先后的冲突必须保留为冲突，不能用提交顺序或 ID 猜赢家。

模型输出只形成待审候选；正式图和属性记录须通过人工审核后才能接受。属性状态是从权威记录重建的投影，物品 holder 等反向查询从一个权威属性推导，不复制成第二份事实。旧 v4 项目不能作为 v5 项目打开或自动迁移；代码显式拒绝旧库。项目保留原始文本证据与历史记录，要求错误、截断、未知或失败均如实记录。

GraphRAG 是面向全量抽取与检索的目标方向；当前源码只提供 Microsoft GraphRAG 导出/暂存/预览适配器，模型生成结果与人工审核的正式叙事图分离。三章试验不代表全书实现。Qt Quick 是当前原生桌面入口；Streamlit 入口仍在代码中，但近期属性验收记录称 Streamlit 工作/测试已暂缓。

## 已实现

- v5 隔离图项目和旧格式拒绝门禁；GraphOperations 暴露图和属性的操作边界。
- 节点属性定义、初始状态、时间点观察、事件变更与撤回；状态区分 known、unknown、absent、unrecorded 与已知空集合。
- 事件边界偏序与时间引用比较；属性状态支持故事时间查询、历史、世界状态投影、原子批次预检和并行分支冲突。
- CLI 属性命令与 Qt 原生属性页；Agent 只提案，须人工接受。
- Microsoft GraphRAG 3.3.0 本地独立 sidecar：GraphOperations 可导出、暂存和预览 GraphRAG 结果，并将模型主张映射回来源。sidecar 不直接写成已接受事实。
- 测试/合成数据、属性状态验收契约、GraphRAG 标准与验收记录已保留在 docs、tests、examples、scripts 和 data 中。

## 未完成与真实验收边界

### 属性/事件真实模型验收

docs/PROPERTY_ACCEPTANCE.md 记录的一次三章 DeepSeek Harness 运行共 18 个请求。模型产生一个通过图预检的待审候选：23 个事件节点、21 条无环 before 断言、104 条定位证据；并尝试 18 条属性记录，但 property_precheck 返回 48 条诊断、没有属性候选。该图候选没有被接受，正式图/属性状态没有因此改变。之后将提示版本改为 graph-agent/11；记录明确说没有用该版本再次请求小说，因此不能宣称真实节点属性抽取已验收。

### Microsoft GraphRAG 三章 sidecar

docs/MS_GRAPHRAG_STANDARD.md 记录官方 standard 索引成功退出：15 个 text units、82 个实体、135 个关系、113 个 claims、23 个 communities/reports；LanceDB 有 82/23/15 个对应向量。三条本地检索查询均在 rank 1 命中目标实体。113 条 claims 中 41 条可精确定位短引文，另外 72 条仍是释义或未定位检索提示。正式修订数保持 3，正式候选数为 0，没有 GraphRAG 事实进入正式图。该结果证明一次有限本地索引和检索映射，不证明全量作品处理、全面召回或答案质量。

两份三章记录的样本长度分别为 16,371 字符（属性/Agent 验收记录）与 16,106 字符（Microsoft GraphRAG staged input），各自报告了边界/哈希；在跨流程比较前应核对它们是否是同一文本版本与计数口径。

### 计划与旧界面

docs/PLAN.md 中 v0.3 局部补全、v0.4 场景资产、v0.5 受控创作仍是后续路线，不应写成已交付。Streamlit 相关入口/测试仍存在，但当前验收范围暂缓其工作；README 仍描述浏览器入口。应后续明确它是暂缓维护还是待恢复。

## 实际测试证据与限制

以下是仓库现有记录，不是本轮新跑结果：

- docs/PROPERTY_ACCEPTANCE.md：v5 图/属性偏序组 65 passed；Qt 组 12 passed、0 skipped；graph-agent 组 23 passed。相关 Ruff 检查通过，Pyright 对改动路径报告 0 errors；之后又记录合成 property/agent 测试 50/50。
- 同一记录报告最终的非 Streamlit 文件级排除套件为 727 passed、0 failed，233.23 秒；并记录 Ruff、Pyright 和 diff 检查通过。15 个混合 Streamlit/UI 文件被排除，因此其中非 UI 测试也可能一并遗漏。文档中的明确复现命令对应较早的 714/0、191.46 秒运行；727/727 是后续结果，文档未在该段另列最终完整命令。
- 最近一次完整仓库运行记录为 806 passed、21 failed、0 skipped，247.47 秒。记录称 20 个失败来自已退役 Streamlit 页面的旧控件/指标断言；另一个 Operations 模型目录导入问题之后有修复，相关测试文件随后 4 passed，但完整套件未在该修复后重跑。因此不能说全量测试当前全绿。
- Microsoft GraphRAG 索引与真实模型结果分开于确定性单元测试。它们不能被 Ruff、Pyright、mock 或部分测试通过替代。
- 本轮是目录盘点和文档整理，没有重新运行自动化测试，也没有发起模型请求。

## 已知问题与风险

1. README/GRAPH_DATA_MODEL 仍用 v4 标签，而 graph_store 当前要求 v5。后续需要统一版本和迁移/拒绝说明。
2. 真实三章属性候选预检失败且 property 记录数为零；graph-agent/11 尚无后续小说请求结果。
3. 全量 GraphRAG 目标尚未实现/验证；当前只有三章的隔离 sidecar，绝大多数 claims 尚未精确映射到原文。
4. 完整 pytest 套件的最后一次记录未全绿；修复后没有最终全量重跑。Streamlit 测试范围与产品状态也未完全对齐。
5. 目录内有大量被忽略的测试基准/缓存；它们不属于 Git 跟踪源码，但测试临时库或记录可能是有价值的运行证据。下方记录本轮已确认并执行删除的精确目录清单及删除前库存快照。

## 启动与测试命令

在仓库根目录的 PowerShell 中：

~~~powershell
uv sync --dev
uv run narrative --db data/graph-projects/launch.sqlite3 ui
~~~

原生桌面入口：

~~~powershell
uv sync --extra desktop
uv run narrative-desktop --projects-dir data/graph-projects
~~~

也可启动 Windows 桌面脚本 start-desktop.cmd。项目 README 还列有 start.cmd、端口和数据库目录参数。

常规检查命令：

~~~powershell
uv run ruff check src tests
uv run pyright
uv run pytest -q
~~~

Streamlit 被排除的历史复现命令见 docs/PROPERTY_ACCEPTANCE.md；它不是全量套件，也不代表最新 727 结果在本轮重新验证。

## 后续优先级

1. 先统一 v5 的版本/项目入口/文档说明，并保留旧库拒绝、无静默迁移的安全边界。
2. 针对 graph-agent/11 完成一次有界、隔离的真实属性候选闭环：让属性证据、类型和事件引用通过预检；由人工决定是否接受；分开记录模型质量与审核后正式数据。
3. 将 GraphRAG 作为独立 sidecar 扩展到明确的小批样本，先核对文本哈希、边界、精确证据率、覆盖和成本，再讨论全量；未经审核不要写入正式图。
4. 决定 Streamlit 的维护范围，修复旧断言或明确停用；在最终代码上重跑与当前范围匹配的测试，并单独记录完整套件。
5. 在上述状态/证据基础稳定后，再推进计划里的 v0.3 局部补全等后续工作。

## 附录：待用户确认的目录清理候选

删除前以下均位于仓库根 C:\a\wk\narrative 下。大小是递归文件字节总数，未打开文件内容；文件数/大小为盘点时的测量值。全部通过当前 .gitignore 被 Git 忽略，不是 Git 跟踪文件，也不计入当前 66 项工作区状态（28 个已跟踪修改、38 个未跟踪，其中仅本报告是本轮新增）。候选目录的内容类别来自目录名、层级和 pytest/缓存用途。

### Pytest 运行目录

这些是按测试/版本命名的 pytest 隔离临时目录。可能含测试 SQLite 库、原始临时结果或故障诊断，虽然从测试可重建，某些单次运行的证据未必可重现；在确认前保留。

| 精确路径 | 文件数 | 大小（bytes） |
| --- | ---: | ---: |
| <code>C:\a\wk\narrative\.pytest_cache</code> | 5 | 103470 |
| <code>C:\a\wk\narrative\.pytest_hierarchy</code> | 0 | 0 |
| <code>C:\a\wk\narrative\.pytest-agent-inspect</code> | 4 | 185738 |
| <code>C:\a\wk\narrative\.pytest-analysis-ui-escalated</code> | 4 | 122881 |
| <code>C:\a\wk\narrative\.pytest-graphrag</code> | 3 | 368640 |
| <code>C:\a\wk\narrative\.pytest-tmp</code> | 268153 | 164243256 |
| <code>C:\a\wk\narrative\.pytest-tmp-graphrag</code> | 56 | 1193441 |
| <code>C:\a\wk\narrative\.pytest-tmp-graphrag-final</code> | 24 | 1647881 |
| <code>C:\a\wk\narrative\.pytest-tmp-graphrag-final2</code> | 24 | 1647881 |
| <code>C:\a\wk\narrative\.pytest-tmp-graphrag2</code> | 6 | 247045 |
| <code>C:\a\wk\narrative\.pytest-v5-agent-review</code> | 1727 | 3621635 |
| <code>C:\a\wk\narrative\.pytest-v5-analysis</code> | 6 | 761856 |
| <code>C:\a\wk\narrative\.pytest-v5-cancel-timeout</code> | 3322 | 1826931 |
| <code>C:\a\wk\narrative\.pytest-v5-full-baseline</code> | 88850 | 99106305 |
| <code>C:\a\wk\narrative\.pytest-v5-full-escalated</code> | 12095 | 39245960 |
| <code>C:\a\wk\narrative\.pytest-v5-full-final</code> | 88829 | 101604540 |
| <code>C:\a\wk\narrative\.pytest-v5-graph-final</code> | 49 | 7766057 |
| <code>C:\a\wk\narrative\.pytest-v5-graph-rewrite</code> | 49 | 7741481 |
| <code>C:\a\wk\narrative\.pytest-v5-non-streamlit</code> | 50329 | 64479208 |
| <code>C:\a\wk\narrative\.pytest-v5-non-streamlit-external</code> | 50493 | 72957429 |
| <code>C:\a\wk\narrative\.pytest-v5-ops-architecture</code> | 11 | 606212 |
| <code>C:\a\wk\narrative\.pytest-v5-ops-architecture-2</code> | 11 | 606212 |
| <code>C:\a\wk\narrative\.pytest-v5-ops-architecture-3</code> | 11 | 606212 |
| <code>C:\a\wk\narrative\.pytest-v5-post-ops</code> | 45 | 3354638 |
| <code>C:\a\wk\narrative\.pytest-v5-qt-final</code> | 13 | 1528329 |
| <code>C:\a\wk\narrative\.pytest-v5-runner-2</code> | 11707 | 8980054 |
| <code>C:\a\wk\narrative\.pytest-v5-runner-3</code> | 12 | 295941 |
| <code>C:\a\wk\narrative\.pytest-v5-runner-final</code> | 11707 | 8980049 |
| <code>C:\a\wk\narrative\.pytest-v5-two-stage</code> | 1751 | 5324245 |
| <code>C:\a\wk\narrative\.pytest-v5-two-stage-full</code> | 50509 | 73789864 |
| <code>C:\a\wk\narrative\.pytest-v5-ui-migration</code> | 34 | 2748426 |

### Dated test temporary directories

These are test temporary stores named for context, forest-query, relative-axis, and timeline runs. They appear generated; exact test files were not opened. Treat single-run output as potentially useful evidence until confirmed.

| 精确路径 | 文件数 | 大小（bytes） |
| --- | ---: | ---: |
| <code>C:\a\wk\narrative\.test-tmp-context-bridge-final-20261018</code> | 2 | 163840 |
| <code>C:\a\wk\narrative\.test-tmp-context-entries-20261018</code> | 1736 | 3288990 |
| <code>C:\a\wk\narrative\.test-tmp-context-entries2-20261018</code> | 73 | 2393597 |
| <code>C:\a\wk\narrative\.test-tmp-context-hierarchy-frame-20261018</code> | 1 | 86016 |
| <code>C:\a\wk\narrative\.test-tmp-context-hierarchy-race-20261018</code> | 1 | 86016 |
| <code>C:\a\wk\narrative\.test-tmp-context-metadata-final-20261018</code> | 9 | 443095 |
| <code>C:\a\wk\narrative\.test-tmp-context-native-20261018</code> | 1 | 81920 |
| <code>C:\a\wk\narrative\.test-tmp-context-native-final-20261018</code> | 7 | 610304 |
| <code>C:\a\wk\narrative\.test-tmp-context-native-frame-final-20261018</code> | 8 | 623152 |
| <code>C:\a\wk\narrative\.test-tmp-context-native-freeze-20261018</code> | 8 | 623097 |
| <code>C:\a\wk\narrative\.test-tmp-context-native-layout-final-20261018</code> | 2 | 94782 |
| <code>C:\a\wk\narrative\.test-tmp-context-native2-20261018</code> | 1 | 81920 |
| <code>C:\a\wk\narrative\.test-tmp-context-native3-20261018</code> | 1 | 81920 |
| <code>C:\a\wk\narrative\.test-tmp-context-native4-20261018</code> | 2 | 94362 |
| <code>C:\a\wk\narrative\.test-tmp-context-native5-20261018</code> | 2 | 94362 |
| <code>C:\a\wk\narrative\.test-tmp-context-native6-20261018</code> | 2 | 94713 |
| <code>C:\a\wk\narrative\.test-tmp-context-sdk-20261018</code> | 1663 | 930122 |
| <code>C:\a\wk\narrative\.test-tmp-context-trace-default-final-20261018</code> | 15 | 508300 |
| <code>C:\a\wk\narrative\.test-tmp-context-ui-20261018</code> | 3 | 172033 |
| <code>C:\a\wk\narrative\.test-tmp-context-ui2-20261018</code> | 3 | 90359 |
| <code>C:\a\wk\narrative\.test-tmp-context-ui3-20261018</code> | 3 | 90359 |
| <code>C:\a\wk\narrative\.test-tmp-context-ui4-20261018</code> | 3 | 90359 |
| <code>C:\a\wk\narrative\.test-tmp-context-ui5-20261018</code> | 5 | 172541 |
| <code>C:\a\wk\narrative\.test-tmp-context-web-defaults-20261018</code> | 5 | 172559 |
| <code>C:\a\wk\narrative\.test-tmp-context-web-final-20261018</code> | 13 | 832009 |
| <code>C:\a\wk\narrative\.test-tmp-forest-query-core-20261018</code> | 47 | 4243497 |
| <code>C:\a\wk\narrative\.test-tmp-forest-query-core-boundaries-20261018</code> | 51 | 4599849 |
| <code>C:\a\wk\narrative\.test-tmp-forest-query-core-final-20261018</code> | 55 | 4976681 |
| <code>C:\a\wk\narrative\.test-tmp-forest-query-core-focused-20261018</code> | 51 | 4599849 |
| <code>C:\a\wk\narrative\.test-tmp-forest-query-core-freeze-20261018</code> | 53 | 4804649 |
| <code>C:\a\wk\narrative\.test-tmp-forest-query-core-large-20261018</code> | 56 | 6459433 |
| <code>C:\a\wk\narrative\.test-tmp-forest-query-core-slices-20261018</code> | 51 | 4599849 |
| <code>C:\a\wk\narrative\.test-tmp-relative-axis-20261018</code> | 4 | 344064 |
| <code>C:\a\wk\narrative\.test-tmp-relative-axis-coordinates-20261018</code> | 4 | 344064 |
| <code>C:\a\wk\narrative\.test-tmp-relative-axis-final-20261018</code> | 4 | 344064 |
| <code>C:\a\wk\narrative\.test-tmp-timeline-20261018</code> | 8 | 704512 |
| <code>C:\a\wk\narrative\.test-tmp-timeline-20261018b</code> | 8 | 704512 |
| <code>C:\a\wk\narrative\.test-tmp-timeline-20261018c</code> | 8 | 708608 |
| <code>C:\a\wk\narrative\.test-tmp-timeline-20261018d</code> | 16 | 1335296 |
| <code>C:\a\wk\narrative\.test-tmp-timeline-final-20261018</code> | 18 | 1511424 |
| <code>C:\a\wk\narrative\.test-tmp-timeline-review-20261018</code> | 18 | 1507328 |
| <code>C:\a\wk\narrative\.test-tmp-timeline-review2-20261018</code> | 18 | 1511424 |

### 其他缓存/临时目录

这些路径同样被 Git 忽略。uv/ruff/tiktoken 目录属于可重建的工具缓存；.cache、.tmp 和 pytest-of-youai 是运行/测试临时目录，内部文件未打开，可能有单次运行记录。

| 精确路径 | 文件数 | 大小（bytes） | 内容类别 |
| --- | ---: | ---: | --- |
| <code>C:\a\wk\narrative\.cache</code> | 76871 | 68930481 | 名称为 analysis/agent/pytest 的运行缓存；可能含单次测试结果 |
| <code>C:\a\wk\narrative\.tmp</code> | 220485 | 187425616 | context/runtime 测试临时数据 |
| <code>C:\a\wk\narrative\.ruff_cache</code> | 31 | 14114 | Ruff 静态检查缓存，可重建 |
| <code>C:\a\wk\narrative\.tiktoken-cache-graphrag</code> | 1 | 3613922 | tokenizer 编码缓存，可重建 |
| <code>C:\a\wk\narrative\.uv-cache</code> | 6 | 1481 | uv 包缓存，可重建 |
| <code>C:\a\wk\narrative\.uv-cache-graphrag</code> | 21525 | 1012927231 | GraphRAG 环境包缓存；可重取但恢复成本/网络依赖较高 |
| <code>C:\a\wk\narrative\pytest-of-youai</code> | 16642 | 11115345 | pytest 会话临时根，可能含单次测试数据 |

清理前候选总量：80 个忽略目录，979,439 个文件，2,015,019,837 bytes（约 1.88 GiB）。删除前二次核验确认 80 条路径都是仓库根的互不重叠直接子目录，路径唯一且均存在；递归计数没有因候选目录嵌套而重复。清理前所有候选在 Git 中均为 ignored，不是 tracked/uncommitted source。文件名扫描没有找到 report.json、report.md、summary.json、qa.json、junit.xml、*results.xml 或 acceptance.json 等正式报告名；但测试临时目录包含大量隔离 SQLite/JSON 记录和 7 个 Parquet 输出，其中 GraphRAG smoke/export 样例含 community_reports.parquet。它们是生成的测试/运行产物，可能保存单次实验状态，不能说全部是纯可重建缓存。项目正式验收记录位于 docs/data/artifacts 等保留目录。清理执行结果：80/80 个清单目录已删除，暂停 0、失败 0；删除前清单文件逻辑大小 2,015,019,837 bytes。C: 卷可用空间从 256,293,322,752 增至 257,278,115,840 bytes，净增 984,793,088 bytes（约 939.17 MiB）。删除后清单目录均不存在；Git status 66 条目与清理前完全相同（28 个已跟踪修改、38 个未跟踪，其中本 handoff 报告是新增项），HEAD 及分支未变。

明确保留（不在候选清单）：.git、src、tests、docs、examples、scripts、data、artifacts、.venv、.venv-graphrag、.streamlit、.opencode-readonly，以及所有未提交源码/测试/配置。data 与 artifacts 含本地项目库、GraphRAG/模型运行记录和演示截图；.opencode-readonly 与 .streamlit 未打开内容，避免触碰可能的本地配置或凭据。
