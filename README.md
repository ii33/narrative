# 叙事 · 证据工作台

当前主入口是 Qt Quick 原生桌面。统一对话中的 agent 按用户任务读取原文、提取节点与属性、核对关系和失效，再通过 `Operations` 自动入库。数据浏览、人物关系图与原文浏览共用同一份工程；没有独立提取、属性审核或入库记录工作台。

公开仓库只包含代码、文档、测试和合成样例；真实作品、原始附件、数据库和运行记录不随仓库分发。范围见[公开仓库约定](docs/PUBLIC_REPOSITORY.md)。

## 启动

Windows 双击 `start-desktop.cmd`，或执行：

```powershell
./start-desktop.ps1
./start-desktop.ps1 -Project D:/小说/作品.nproj
```

普通启动只使用工程内的 `.venv` 和 `.runtime/python`。首次构建准备：

```powershell
uv sync --extra desktop
.venv/Scripts/python.exe scripts/localize_python.py
```

本地化脚本复制受信任的本地 CPython 分发，不下载、不修改源目录。解释器、标准库、依赖与 Harness 运行时必须位于项目内；缺失时明确失败。启动也检查 Windows 进程完整性，异常 Low 标签会阻止正常目录和配置库写入。

升级依赖或移动目录后应重新核对本地运行时。代码修改后重启程序，已经导入的 Python 模块不会随界面刷新自动更新。

## 工程与原文

工程列表打开当前格式的 `.nproj` 信息文件；目录、裸数据库和旧版入口不是打开方式。信息文件与数据分开：

```text
作品.nproj
作品_data/
  graph.sqlite3
  material/
作品_extraction/   模型原始输出、工具调用与任务审计
作品_chats/        聊天记录及当前聊天指针
```

最近工程列表每项提供“创建副本”。选择新的 `.nproj` 文件名和位置后，程序复制原文、提取、改编、剧本、资料、资产及聊天／任务记录，建立独立工程并加入最近列表；当前工程和聊天不自动切换。副本用新文件名作为工程名，后续编辑互不影响。默认文件名自动避开已有工程；目标已有文件时不会覆盖。运行中的任务需先结束再复制。历史记录中的原始绝对路径按原样保留，包括直接引用工程外图片的链接；当前会话入口和正文依据会重新指向副本。

CLI 使用 `narrative project-copy 原工程.nproj 副本.nproj --json`，MCP 对应工作区工具 `duplicate_project(source,destination)`；均调用 `Operations.duplicate_graph_project`，不要求先打开工程。

原文导入后保持不变。切片只是按完整原文 Unicode 字符坐标保存的浏览索引，`end` 不包含；创建切片不会提取事实或修改正文。

规则章节优先按标题模式批处理：agent 读取少量样例确认模式，`preview_pattern_slices` 在本地扫描整份原文并返回计数及少量首尾样例，`create_pattern_slices` 按同一计划批量创建。切前三章时，第三章结束位置取第四个标题，不误延到书末。没有稳定标题格式时仍可按语义提供明确范围；完整原文始终可查。

图使用 Schema v10 的节点与属性记录表达人物、群体、物品、地点、主要故事、大事件、场景、事件及动作。属性带原文证据、披露位置和作用域；状态变化关闭旧值区间并追加新值；节点只保存当前对象，按阅读位置可删除后续数据回退。工程设置默认精细，可选标准／精细提取粒度并配置模型；精细档提取更密集的事件，两档均不默认提取动作。事件编辑器提供按需“动作提取”，动作归属选中的事件。语义说明见[数据模型](docs/DATA_MODEL.md)。

## 改编与资产

左侧“改编”默认复用数据浏览的分类、节点列表、详情与节点编辑器，从已提取数据导入独立副本。时间点列表条目可拖动改变改编顺序，节点编辑器可屏蔽时间点及其子树，也可新建任意类型节点。可插入、复制、改名、屏蔽或移动节点，指定父级、出场时点，并在时间点新增、更改或失效属性。改编使用独立 `sort_id` 排序，不按原节点编号排序；写入带修订检查、静态校验及审计，不修改提取节点和属性。

“从提取导入”按主要故事→大事件→场景→事件→动作展示可展开、折叠的多级列表。支持跨分支多选：勾选上级包含其全部下属，单独选择下属时上级显示半选，折叠不清除选择；动作只列出现有节点。只导入所选时间点（选择容器含下属）以及必要归属、参与实体和相关有效状态；补齐引用依赖与状态区间边界。重复导入保留本地改编，源与改编冲突显示诊断。改编数据保存在工程数据库的独立元数据中，使用自己的修订号。

左侧“资产”包含形象、物品、地点、材质。前三项共用改编时间点选择；角色可有多个形象，设定可指定生效和失效时点。材质不关联时间点。设定卡支持说明、设定图、可选参考图和提示词，参考图复制到工程资料目录，原始附件保持不变。资产及审计保存在 `material/assets/`，移动工程时须保留完整数据目录。

以上功能通过同一 `Operations` 提供给界面、CLI 与统一助手。

改编和资产的 CLI、MCP 入口使用同一工具注册表，界面也经相同 Operations。CLI 提供以下命令；全部命令可加 `--project 作品.nproj --json`：

| CLI 命令 | MCP 工具 |
| --- | --- |
| `adaptation` | `adaptation_snapshot` |
| `adaptation-check` | `check_adaptation` |
| `adaptation-import-candidates` | `import_adaptation_candidates` |
| `adaptation-import --moment E1 --moment E2` | `import_adaptation` |
| `adaptation-browse` | `adaptation_browse_view` |
| `adaptation-properties` / `adaptation-detail` / `adaptation-editor` / `adaptation-assets` | 对应 `adaptation_node_*` |
| `adaptation-edit` | `edit_adaptation_node` |
| `adaptation-copy` | `copy_adaptation_node` |
| `adaptation-move` | `move_adaptation_node` |
| `adaptation-suppress` | `suppress_adaptation_node` |
| `adaptation-property` | `set_adaptation_property` |
| `assets` | `list_assets` |
| `asset` | `get_asset` |
| `asset-save` | `save_asset` |
| `asset-delete` | `delete_asset` |
| `asset-moments` | `asset_moments` |
| `extraction-settings` | `get_extraction_settings` |
| `extraction-granularity fine` | `set_extraction_granularity` |

例如，`node-changes.json` 保存 `{"name":"改编新角色","node_type":"person"}`，`material.json` 保存 `{"category":"material","title":"旧铜","prompt":"暗色铜锈"}`：

```powershell
narrative adaptation-import-candidates --project 作品.nproj --json
narrative adaptation-import --moment E1 --moment E2 --project 作品.nproj --json
narrative adaptation-edit --changes-file node-changes.json --project 作品.nproj --json
narrative asset-save --payload-file material.json --project 作品.nproj --json
narrative adaptation-check --project 作品.nproj --json
narrative-mcp --project 作品.nproj
```

节点编辑用 `--node 节点ID`；新增省略节点ID。`--changes`、`--payload`、`--value` 接受 JSON，也各自支持 `--changes-file`、`--payload-file`、`--value-file`。属性的 `--value 0`、`--value false` 保留原始类型，文本值须用 JSON 字符串。`adaptation-suppress 节点ID --suppressed false` 恢复节点及子树；属性失效用 `--record 取值ID --event 时点ID --expire true`。放置位置通过 `--after`、`--parent` 指定；省略排序参照时移动/插入放到开头，复制紧随原节点，省略父级则保留归属。

写操作可传 `--expected-revision`：改编使用 `adaptation` 返回的独立 `revision`，资产使用 `assets` 或 `asset` 返回的独立 `revision`；不能混用提取图修订号。错误参数或过期修订不写入。`narrative tool list --json` 与 MCP `tools/list` 提供完整参数结构；通用 CLI `narrative tool call 工具名 '参数JSON' --project 作品.nproj` 仍可使用。MCP 未设默认工程时，项目工具在 arguments 中传 `project`（`.nproj` 入口），全局工程列表无需工程参数。


## 工具中的编号

新建业务对象由程序自动分配稳定的可读编号，例如 `adapt-person-1`、`material-1`、`screenplay-1`、`segment-1`、`unit-1`、`block-1`；模型无需计算哈希或为新对象选号。编号只标识对象，顺序仍由原文坐标、`sort_id` 或结构位置决定，改名、移动不改身份，删除不复用已分配编号。

工具中的范围、上下文、场次计划和存储检查指纹以短依据编号传递（如 `span-basis-1@…`），映射和真实指纹校验由程序内部完成。句柄绑定工程与精确快照，旧上下文仍会被拒绝，不能靠重新读取当前指纹覆盖过期依据。CLI、MCP 和对话工具共用这一处理。写作对话的任务号和依据档案由宿主自动关联，模型不再搬运这些内部参数；内部审计与原始导出保留必要的完整校验信息。既有对象身份不因显示方式调整而重写。

## 剧本

左侧“剧本”页分为改编时间表、段落表、场表、正文四栏，每栏可独立折叠／展开并拖动调整宽度，窄窗可横向滚动。先从改编时间线多选导入节点及相关状态到当前剧本的独立副本，再选择连续范围建立段落；场表和对应正文同时显示。源改编后续变化不自动影响副本，本地调整也不修改源改编或其他剧本。场表行与正文共用稳定场次 ID；修改场表不会自动清空正文。“采用场表”只是工作标记，可以继续修改，也不要求先采用才能写作。

已有剧本可继续“追加导入时间点”：新节点按源改编中的顺序放到本地时间线末尾，已导入节点跳过，保留已有顺序、本地修改、段落、场表和正文；已导入容器仍可选择，以追加后来新增的子项。选区、当前场次和四栏折叠状态也保留。未编辑旧状态因源新增后续状态而需要合法结束时，可补齐结束边界并在导入摘要中明确列出；本地修改过的状态或真正区间冲突不会被静默覆盖。重新同步既有来源字段仍是独立的显式导入操作，不借追加执行。

“写当前场”“批量写空场”和“对话修改”均进入当前聊天的同一常驻模型会话。发送时固定修改目标；批量默认只选择启用、尚无正文的普通场景，逐场原子提交。已有正文须明确授权修改；块级任务只可改指定块。失败或停止不撤销已提交场次。任务状态、正文是否存在和输入依据是否变化分别显示。

拆场以连续时空为单位，同一时空的事件、情绪、招式或胜负变化作为场内节拍处理。`read_scene_table_context` 提供有效事件、逐点状态、逐事件已知／未记录地点及拆场标准；不发送原始引文、屏蔽叙事或旧场表概要、结果、制作备注。旧场表只提供稳定编号、修订和正文存在状态。场数和场界交由模型依据标准判断，程序不强制分配事件、计算场界或限制场数；可选来源引用只校验其是否属于有效范围。`production_notes` 不在拆场生成阶段填写；场次来源失效会静态提示，正文保留。

拆场对话只开放当前段落上下文、完整场表提交和当前剧本检查，不能绕回全量时间线或原文取出屏蔽内容。手工编辑与正文写作仍使用各自入口。合为一场时复用一个已有场次编号，冗余场可停用，原正文不被隐式删除。

独立“结构”页用集和幕组织同一批段落，支持调序及明确的单片／剧集重组。主表与结构页使用同一权威顺序。段落来源随当前剧本副本的锚点动态解析；范围失效或输入改变会提示复查并保留旧正文。导出支持 UTF-8 剧本文本与包含身份、关联和状态的 JSON。

所有业务经 Operations，CLI／MCP 与界面共用合同。常用命令包括 `screenplay-timeline-candidates`、`screenplay-timeline-import`、`screenplay-timeline-append`、`screenplay-timeline`、`span-preview`、`span-create`、`screenplay-create`、`screenplay`、`script-segment-create`、`scene-table-context`、`scene-table-submit`、`scene-table-edit`、`scene-table-adopt`、`script-unit-context`、`script-unit-submit`、`script-block-edit`、`script-writing-targets`、`screenplay-structure`、`screenplay-check`、`screenplay-export`；与工具名的对应及完整参数见 `narrative tool list --json` 和各命令 `--help`。

```powershell
narrative screenplay-create --title "雨夜交接" --format single --reason "创建剧本方案" --project 作品.nproj --json
narrative screenplay-timeline-candidates --screenplay-id 剧本ID --project 作品.nproj --json
narrative screenplay-timeline-import --screenplay-id 剧本ID --moment-ids-file selection.json --expected-timeline-revision 0 --expected-adaptation-revision 源改编修订号 --reason "导入剧本独立副本" --project 作品.nproj --json
narrative span-preview --screenplay-id 剧本ID --selection-ids-file selection.json --project 作品.nproj --json
narrative screenplay --screenplay-id 剧本ID --project 作品.nproj --json
narrative screenplay-export --screenplay-id 剧本ID --format text --path 剧本.txt --project 作品.nproj --json
```

`selection.json` 为改编节点 ID 的 JSON 数组。对象、动作数组和正文块等复杂参数均支持 `--参数名-file` 读取 UTF-8 JSON。生成前读取相应 context，提交必须带该次读取返回的对象修订号与输入指纹；不拿全局图 revision 代替单场并发检查。写入要求说明 `reason`。剧本数据使用工程数据库内独立十一表（含每剧本独占时间线），正常只读不初始化；当前结构不提供旧格式迁移。旧剧本表不完整时，界面提供丢弃剧本域并重建的入口；原文、改编和资产不受影响。空的旧表只在明确新建时初始化。CLI／MCP 对应 `screenplay-storage`／`inspect_screenplay_storage` 与 `screenplay-storage-reset`／`reset_screenplay_storage`，重建要求先检查取得指纹，并明确丢弃范围和原因。

## 统一对话

在同一个对话面板提出阅读、查证、整理切片或提取要求。原文页也可用“提取／复核”按钮针对当前全文或切片发起同样的 agent 任务，输出进入当前聊天；它们不是独立工作台或另一套流程。助手与提取共用工程的常驻 Harness 进程；同一聊天继续同一真实 SDK 会话，不通过拼接界面聊天记录冒充模型上下文。不同聊天保持独立上下文。

提取属于当前任务：agent 可以按需读取全部已导入原文及工程资料，在本次任务内完成节点、属性、关系和有效性核对；没有程序安排的固定多轮，也没有对话次数上限。任务没有硬性总时限，可由用户手动停止；模型服务的连接及响应空闲检测独立保留，默认推理等级为 `low`。

统一 agent 可以使用当前工程全部业务写入工具，随时补建和修订节点、属性、关系及包含结构；写入通过 `Operations` 静态检查并保留事务审计。每个活动事件必须归属场景，单事件也不能省略场景。 每个活动物品必须有当前世界事实层的持有者，明确为节点、“无”或“未知”；缺失与界面派生提示都不等于已记录未知。

常规提取直接调用 `submit_extraction`，程序自动定位引文、分配编号、静态校验并原子入库；`preview_extraction` 仅供单独试查，不要求先重复提交同一草稿。它们是当前 agent 可调用的业务工具，不会启动另一个模型任务，也没有人工接受步骤。大事件需由正文发生过程支持，卷章标题只是组织信息；人物关系的性质与显示分类分别记录。

重新提取或复核时，已查证的事件用 `existing_id` 继续引用；事件保存全部 `evidence_spans`，不会因为换了首条引文就必然新建。只有同源、同名或已知别名、同场景与参与者，并有同位置完整证据命中的唯一候选才自动复用；有歧义时由 agent 查证，确属另一次发生用 `new_occurrence` 明确声明。同名本身不等于同一事件。 已有重复事件由模型查证后，通过 `preview_event_merge` / `merge_events` 显式合并；物理合并证据与当前属性、结构引用，删除重复节点，不留重定向或旧节点版本。开发不会直接修改用户历史结果。

有疑点时保留原因，不伪造事实。无效草稿返回诊断供 agent 修订，不能靠人工修改模型输出后宣称通过。模型每次可见输出和工具间说明都在会话中保留，工具过程默认折叠。会话原在底部时跟随新输出，翻阅历史时保持阅读位置；原始输出、使用量及完整工具记录另行保存。

完整工具调用因 JSON 格式错误失败时，原参数与诊断会保存并反馈给同一任务中的模型修正；程序不执行非法工具、不补改模型结果、不另开任务。网络错误、容量截断或响应持续空闲仍停止。

工具已经成功入库后，即使后续回答失败或由用户停止，已写入的事实仍保留。程序分别报告入库结果与整个对话任务是否完成，不自动重试模型请求。

关系图按当前可见连线计算位置：相连节点由弹簧吸引靠近，结合关系文字和同对多边数量留出空间；节点、标签均避碰，不连通的组件分开排布。没有按人物或节点 ID 指定的坐标，也不再强制把关系最多者放在圆心。数据或画布尺寸改变时重算；缩放和平移使用已有布局。

左侧“静态检查”可手动只读扫描当前工程，按错误、警告和提示筛选，并定位节点、属性记录或原文。结果标明检查修订；数据变化后提示过期，不自动修复或改写历史。静态检查不调用模型，不能替代原文语义复核。

## 程序接口

UI、CLI 和 MCP 共用 `Operations` 与同一张工具表：

```powershell
narrative tool list
narrative tool call list_sources '{}' --project D:/小说/作品.nproj
narrative nodes --project D:/小说/作品.nproj --type person
narrative-mcp --project D:/小说/作品.nproj
```

读取投影和基础数据编辑仍可使用。旧的分步提取、复核、单独提交以及人工审核命令已移除，不提供兼容入口。当前协议见[接口合同](docs/AGENT_API.md)。

## 开发与验收

`data/` 只存运行产物与历史验收证据，不是代码来源，不进入发布包。禁止执行其中的历史脚本，或把其中的旧实现、提示词和补丁合并回当前代码。历史报告只说明当时的版本与结果。

开发先运行必要工程测试，再按本轮影响范围使用隔离工程做有界真实模型验收。原始输出质量、自动校验结果和实际入库结果分别记录；失败、截断、缺凭据和未完成不得用 mock、回放或手工修改代替。具体规则见 [AGENTS.md](AGENTS.md)。
