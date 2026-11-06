# 叙事 · 证据工作台

当前主入口是 Qt Quick 原生桌面。统一对话中的 agent 按用户任务读取原文、提取节点与属性、核对关系和失效，再通过 `Operations` 自动入库。数据浏览、人物关系图与原文浏览共用同一份工程；没有独立提取、属性审核或入库记录工作台。

公开仓库只包含代码、文档、测试和合成样例；真实作品、原始附件、数据库和运行记录不随仓库分发。范围见[公开仓库约定](docs/PUBLIC_REPOSITORY.md)。

## 启动

Windows 首次安装前需先安装 `uv` 并确保它在 PATH 中；环境脚本不会自动安装 uv。双击 `setup-environment.cmd` 完成准备，或在终端运行 `./setup-environment.ps1`。准备完成后双击 `start-desktop.cmd`，或执行：

```powershell
./start-desktop.ps1
./start-desktop.ps1 -Project D:/小说/作品.nproj
```

环境配置脚本执行以下依赖同步，再准备和检查工程内 Python，不启动界面：

```powershell
uv sync --locked --all-packages --all-extras --all-groups
```

首次准备时会把 `.venv` 使用的本地 CPython 复制到 `.runtime/python`，并检查启动器和依赖。直接启动时也会尝试准备；已有运行时不完整时会提示检查，不会自动覆盖它。uv 重建环境后若 Python 版本或架构与已有运行时不一致，需检查并重新准备运行时。解释器、标准库、依赖与 Harness 运行时必须位于项目内。启动也检查 Windows 进程完整性，异常 Low 标签会阻止正常目录和配置库写入。

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

图使用 Schema v10 的节点与属性记录表达人物、群体、物品、地点、主要故事、大事件、场景、事件及动作。属性带原文证据、披露位置和作用域；状态变化关闭旧值区间并追加新值；节点只保存当前对象，按阅读位置可删除后续数据回退。工程设置默认精细，可选标准／精细提取粒度、配置模型并编辑内置提示词；精细档提取更密集的事件，两档均不默认提取动作。事件编辑器提供按需“动作提取”，动作归属选中的事件。语义说明见[数据模型](docs/DATA_MODEL.md)。

工程设置的“内置提示词”按用途列出系统原则、提取、复核、粒度、动作提取、资料使用、场级划分、正文写作和快捷按钮等默认文本。可原样编辑多行内容、预览有效值、单项恢复默认；保存空文本表示停用该片段，恢复默认使用独立操作。设置仅影响本工程，下一任务使用保存后的配置，当前任务保持发起时的提示快照；任务审计记录实际文本及版本。默认文案不预设题材、人物立场、冲突形式或结局，工具结构与原子校验继续由程序执行。CLI可用`prompt-settings`读取，`prompt-override`修改；MCP提供`load_prompt_settings`与`save_prompt_override`同一合同。

## 改编与资产

左侧“改编”默认复用数据浏览的分类、节点列表、详情与节点编辑器，从已提取数据导入独立副本。时间点列表条目可拖动改变改编顺序，节点编辑器可屏蔽时间点及其子树，也可新建任意类型节点。可插入、复制、改名、屏蔽或移动节点，指定父级、出场时点，并在时间点新增、更改或失效属性。改编使用独立 `sort_id` 排序，不按原节点编号排序；写入带修订检查、静态校验及审计，不修改提取节点和属性。

“从提取导入”与剧本导入复用从左到右的主要故事、大事件、场景、事件、动作五列列表。点击名称逐级筛选右侧，也可恢复全部分支；勾选独立于导航，支持跨分支多选，上级包含全部下属，下属单选时上级半选，切换筛选保留选择；动作只列出现有节点。只导入所选时间点（选择容器含下属）以及必要归属、参与实体和相关有效状态；单独选事件不会带入兄弟或其他剧情事件。属性在事件处记录 set/add/remove/clear/unknown，由程序沿本地排序轴推导状态；必要前置事件仅保留无剧情的状态锚点，不出现在事件列表或生成内容中。重复导入保留本地改编，源与改编冲突显示诊断。改编数据保存在工程数据库的独立元数据中，使用自己的修订号。

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

节点编辑用 `--node 节点ID`；新增省略节点ID。`--changes`、`--payload`、`--value` 接受 JSON，也各自支持 `--changes-file`、`--payload-file`、`--value-file`。属性的 `--value 0`、`--value false` 保留原始类型，文本值须用 JSON 字符串。`adaptation-suppress 节点ID --suppressed false` 恢复节点及子树；属性变化指定事件及操作（set/add/remove/clear/unknown），关系结束用 remove 指定对方；录入错误直接更正或删除变更记录。放置位置通过 `--after`、`--parent` 指定；省略排序参照时移动/插入放到开头，复制紧随原节点，省略父级则保留归属。

写操作可传 `--expected-revision`：改编使用 `adaptation` 返回的独立 `revision`，资产使用 `assets` 或 `asset` 返回的独立 `revision`；不能混用提取图修订号。错误参数或过期修订不写入。`narrative tool list --json` 与 MCP `tools/list` 提供完整参数结构；通用 CLI `narrative tool call 工具名 '参数JSON' --project 作品.nproj` 仍可使用。MCP 未设默认工程时，项目工具在 arguments 中传 `project`（`.nproj` 入口），全局工程列表无需工程参数。


## 工具中的编号

新建业务对象由程序自动分配稳定的可读编号，例如 `adapt-person-1`、`material-1`、`screenplay-1`、`segment-1`、`unit-1`、`block-1`；模型无需计算哈希或为新对象选号。编号只标识对象，顺序仍由原文坐标、`sort_id` 或结构位置决定，改名、移动不改身份，删除不复用已分配编号。

工具中的范围、上下文、场次计划和存储检查指纹以短依据编号传递（如 `span-basis-1@…`），映射和真实指纹校验由程序内部完成。句柄绑定工程与精确快照，旧上下文仍会被拒绝，不能靠重新读取当前指纹覆盖过期依据。CLI、MCP 和对话工具共用这一处理。写作对话的任务号和依据档案由宿主自动关联，模型不再搬运这些内部参数；内部审计与原始导出保留必要的完整校验信息。既有对象身份不因显示方式调整而重写。

## 剧本

左侧“剧本”页分为改编时间表、段落表、场表、正文四栏，每栏可独立折叠／展开并拖动调整宽度，窄窗可横向滚动。“从改编时间线导入”使用从左到右的主要故事、大事件、场景、事件、动作五级列表，各列按改编排序展示，可逐级筛选并跨分支多选。先将所选节点及相关状态导入当前剧本的独立副本，再选择连续范围建立段落；场表和对应正文同时显示。剧本时间列表默认全部折叠，同一时间线刷新保留用户的展开选择，新导入容器默认折叠。源改编后续变化不自动影响副本，本地调整也不修改源改编或其他剧本。场表行与正文共用稳定场次 ID；修改场表不会自动清空正文。“采用场表”只是工作标记，可以继续修改，也不要求先采用才能写作。

剧本使用“剧本创作约定”入口，合并原项目风格与剧本要求。九项字段为整体基调、视听风格、对白风格、叙事节奏与镜头倾向、受众、叙事与改编原则、剧本形式与篇幅、制作约束、其他要求，默认均留空。工程默认供所有剧本继承，单份剧本可逐项覆盖；未覆盖或null表示继承，显式空串表示该剧本不受此项默认限制。窗口同时保留剧本名称与语言，显示实际生效值，不需编辑JSON。有效约定及其来源进入写作和制作上下文、依据与审计，修改后旧依据不能覆盖新约定。

CLI 使用 `creative-agreement` / `creative-agreement-save`，MCP 使用 `load_creative_agreement` / `save_creative_agreement`。读取返回 `project_defaults`、`script_overrides`、`effective`、`effective_sources` 和两级修订；保存需传当前修订，可原子提交工程默认、剧本覆盖及名称／语言。

正文以当前剧本独立时间线和指定范围决定剧情取舍；场表在一致时细化表达，原作、源改编及旧稿只作参考，不能据此恢复已删除或未选情节。表达可依据项目风格重构，以剧本表现力和可演、可视、可听的效果为目标，不要求逐句还原。场表卡会显示非空的结果字段，便于核对遗留剧情。

剧本时间表支持新建、编辑、移动、复制和物理删除时间点。编辑使用当前剧本独立时间线的修订号，经同一Operations进入UI、CLI和MCP；删除直接清理节点及其子项与相关引用，不创建屏蔽标记、归档或节点历史副本。删除已绑定范围的端点会产生明确失效诊断；场表和正文仍由其自身编辑操作管理。

已有剧本可继续“追加导入时间点”：新节点按源改编中的顺序放到本地时间线末尾，已导入节点跳过，保留已有顺序、本地修改、段落、场表和正文；已导入容器仍可选择，以追加后来新增的子项。选区、当前场次和四栏折叠状态也保留。未编辑旧状态因源新增后续状态而需要合法结束时，可补齐结束边界并在导入摘要中明确列出；本地修改过的状态或真正区间冲突不会被静默覆盖。重新同步既有来源字段仍是独立的显式导入操作，不借追加执行。

当前场写作与修改共用一个入口：无正文时显示“写当前场”，已有正文时显示“修改当前场”。它与“批量写空场”、正文块“修改”均进入当前聊天的同一常驻模型会话。发送时固定修改目标；批量默认只选择启用、尚无正文的普通场景，逐场原子提交。已有正文须明确授权修改；块级任务只可改指定块。失败或停止不撤销已提交场次。任务状态、正文是否存在和输入依据是否变化分别显示。

工程内全部资料和业务工具在各类任务中开放，界面快捷入口只设置默认上下文和明确的写作目标，不规定固定工作阶段；对话、CLI、MCP和无头调用复用Operations。用户可将提取内容及结构化查询用作精细目录、检索，将资料卡用于考据、参考、设定、备注。默认优先读取当前段落、本场和相关前后文，也可主动查阅全部原文、提取、改编、资产、资料卡、旧场表和正文。屏蔽表示不纳入当前改编，并非禁止查阅；参考资料不会自动成为待改编内容，原作与当前改编冲突时以当前改编为准，旧场表是可编辑对象。定向写作仅修改本次明确指定的对象；普通对话按用户指令组织工作流。所有写入继续接受结构、引用、证据、修订检查和审计。

默认按时空及叙事连续性判断场界，具体划分标准可在工程提示词中调整。`read_scene_table_context`默认提供有效事件、逐点状态、逐事件已知／未记录地点及拆场标准，并可查阅已有场表；不通过工具白名单或读取封锁控制创作。场数和场界交由模型依据标准判断，程序不强制分配事件、计算场界或限制场数；可选来源引用只校验其是否属于有效范围。场次来源失效会静态提示，正文保留。

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

剧本页的“编译 / 导出”提供格式化预览，可保存 PDF、Word（DOCX）、HTML、纯文本、Fountain 和结构 JSON。编译按当前集、幕、段落、场及正文块顺序处理启用内容，连续编号场次；人物名、对白、括注和转场分别排版。范围、依据及空场提示在预览旁单独显示，不写入成品正文，也不改动已有场表和正文。PDF 与 Word 在程序内生成，无需安装 Office；更新环境时运行 `setup-environment.cmd` 安装新增依赖。

CLI 与 MCP 共用 `compile_screenplay`：

```powershell
narrative screenplay-compile --screenplay-id 剧本ID --format html --project 作品.nproj --json
narrative screenplay-compile --screenplay-id 剧本ID --format pdf --path 剧本.pdf --project 作品.nproj --json
narrative screenplay-compile --screenplay-id 剧本ID --format docx --path 剧本.docx --project 作品.nproj --json
```

PDF / DOCX 不传 `path` 时只返回预览及诊断，传入路径后才生成文件。预览用于检查版式与内容，实际分页以保存的文档为准。

编译预览中的“排版设置”按工程保存字体、字号、倍数行距、四向页边距，以及场头、人物名、对白、动作、场景描写的对齐、左右缩进和段前段后间距。同工程所有剧本共用，保存后刷新预览，并用于 HTML、PDF、Word 输出。PDF 从本机已安装字体中解析并嵌入所选字体；未安装或无法嵌入时明确报错，不静默改用另一字体。纯文本与 Fountain 不承载字体和纸张属性，会返回格式限制提示。

`compile-settings` / `compile-settings-save`（MCP：`load_compile_settings` / `save_compile_settings`）读取和修改同一设置；保存需完整 `settings`、当前 `expected_revision` 及修改原因。编译结果中的 `compile_settings` 标明本次使用的设置和修订。

正文栏上方集中提供“修改、上移、删除”，单击选中正文块后操作，双击可直接编辑；每块不再重复按钮。同一修改窗口可直接编辑，也可填写对话修改意见。文字有改动时，“保存并发送修改意见”先按打开窗口时的修订保存，成功后才把当前块作为固定目标交给模型；保存冲突保留输入，不启动对话。未改文字时可直接发送意见。新增块先保存取得正式编号，再使用对话修改。

左侧“分镜稿”和“导演台”为两个独立模块，共用本场分镜数据与修订。分场剧本先进入分镜稿，按场读写文稿及图格；导演台集中镜头时间轨、场面调度、机位与动态预演。切换模块保留场次和镜头，隐藏模块暂停播放。分镜稿支持：逐镜编辑画面、镜头语言、视觉资源及跨镜对白与声音，按需绘制画格，再进入编排。可独立导出 TXT 或内嵌画格的 HTML／PDF（CLI：`storyboard-script-export`）。静帧、二维调度、命名时刻、机位视图与临时声音共用播放头，支持逐帧、循环、拖帧及共享动作的多机位重看。基础运镜、跟随、环绕、揭示与推轨变焦可展开编辑并保存为工程预设，2.5D视图明确采用近似投影。草图由本机 ComfyUI 图像模型生成，内部几何图仅作参考；定案后结合资产及项目风格转绘，可并排或叠看。局部图层／平面机位动画可导出干净MP4。正式视频支持准备实际输入、提交、查询回填和显式选段采用，当前接MiniMax H3，需单独配置视频API凭据。详见[分期方案](docs/STORYBOARD_DIRECTOR.md)、[图像服务](docs/STORYBOARD_STYLE_PROVIDER.md)、[时间与调度合同](docs/STORYBOARD_TIMING.md)及[视频服务与采用](docs/STORYBOARD_VIDEO.md)。UI、CLI、MCP 和模型共用 Operations；`storyboard-preview` 查询指定剪辑时刻，`storyboard-check` 读取程序静态诊断，不让模型重复审核账目。

`selection.json` 为改编节点 ID 的 JSON 数组。对象、动作数组和正文块等复杂参数均支持 `--参数名-file` 读取 UTF-8 JSON。生成前读取相应 context，提交必须带该次读取返回的对象修订号与输入指纹；不拿全局图 revision 代替单场并发检查。写入要求说明 `reason`。剧本数据使用工程数据库内独立十一表（含每剧本独占时间线），正常只读不初始化；当前结构不提供旧格式迁移。旧剧本表不完整时，界面提供丢弃剧本域并重建的入口；原文、改编和资产不受影响。空的旧表只在明确新建时初始化。CLI／MCP 对应 `screenplay-storage`／`inspect_screenplay_storage` 与 `screenplay-storage-reset`／`reset_screenplay_storage`，重建要求先检查取得指纹，并明确丢弃范围和原因。

## 统一对话

在同一个对话面板提出阅读、查证、整理切片或提取要求。原文页也可用“提取／复核”按钮针对当前全文或切片发起同样的 agent 任务，输出进入当前聊天；它们不是独立工作台或另一套流程。助手与提取共用工程的常驻 Harness 进程；同一聊天继续同一真实 SDK 会话，不通过拼接界面聊天记录冒充模型上下文。不同聊天保持独立上下文。

任务执行中仍可输入文字和添加附件，点击“插话”向该任务的同一 SDK 会话追加意见；不会触发停止或另开任务。排队和实际送达分别显示，已结束、已停止或切换工程后的旧任务不接受迟到插话，未送达会明确反馈。插话沿用发送中任务的权限快照。

聊天文字、思考与工具过程支持鼠标选取、Ctrl+C 和右键复制。工具名称来自程序工具注册，参数和实际输出格式化显示；长输出预览有明确截断标记，完整结果仍保留任务审计。附件可多选，原文件复制到工程内保存并使用 `attachment-N` 编号，不覆盖用户原件或 Library；支持文本、PDF文字层和 DOCX 文字分页读取，图片以图像内容交给支持识图的模型。扫描 PDF 不做 OCR，未知二进制只显示元数据，不假称已经阅读。CLI 可用 `chat-attachment-import` 显式导入，助手模型只获得已登记附件的读取工具。

助手面板显示上下文占用及可展开的用量统计，分别列出本次任务和当前聊天的输入、输出、缓存及请求数。缓存属于输入明细，不重复加入合计；未返回的用量标为未知。占用按最近模型输入或明确标注的估计显示，不使用累计 token 充当上下文长度；调用和压缩事件实时刷新，切换聊天或工程时隔离数据。CLI 使用 `narrative usage-statistics --project 作品.nproj --json`，MCP 使用同一只读 `read_usage_statistics` 操作。

工程设置可直接输入 API Key。密钥由 Windows 当前用户 DPAPI 加密，存入本机应用凭据库，按工程、供应商与服务地址隔离，不写入工程、聊天、公开配置或工程副本。留空保留已保存的值，单独勾选清除；当前服务的本机密钥优先于所选环境变量，环境变量仍可使用。保存从下次任务生效，不中断正在运行的聊天。“检查有效性”使用当前填写配置发送一次最小请求，不保存配置、不自动重试，区分认证失败、权限不足、配置错误、限流与网络问题；无法确认时不冒称密钥无效或有效。该检查会产生独立服务用量，不计作聊天任务。

CLI 使用 `narrative project-api-key status --project 作品.nproj` 查看状态，`set --key-stdin` 从标准输入读一行保存，`clear` 清除，`check` 检查；检查也可用 `--key-stdin` 测试尚未保存的密钥。MCP 完全访问服务提供对应配置操作，助手业务模型目录不暴露密钥设置与检查工具。所有公开结果只返回状态和来源，不返回密钥。

聊天缓存命中在面板中直接显示，展开可分别查看当前任务与聊天累计统计。命中率按同一批请求的命中输入 token 总和除以输入 token 总和计算，不平均每条请求的百分比。部分请求未报告缓存用量时，仅显示已知请求的命中率及覆盖请求数，不把未知当作零；输入为零时不计算百分比。统计以服务端原始用量为准，服务端缓存 token 不与本地资料、文件读取或统计投影缓存混算。当前方舟接入利用服务端隐式前缀缓存，程序不创建显式缓存对象，也不提供缓存清空或有效期设置；缓存命中不代表上下文变短或免于账户限流。

每次任务结束，程序根据真实提交记录生成入库汇总卡，区分成功操作与新增、修改、删除、失效等对象变化；失败、预览和拒绝提交不计入。失败或停止时仍列出已成功提交的内容，没写入则明确显示无入库变更。模型无需重复生成入库数量清单，必要的创作说明、语义判断和存疑理由仍可保留；程序不为汇总另开模型请求，也不在某次工具成功后提前截断尚未完成的任务。CLI 使用 `narrative task-write-summary --task-id 任务编号 --project 作品.nproj --json`，MCP 使用 `read_task_write_summary`，无头调用返回同一 `commit_summary` 与 `commit_summary_text`。

提取属于当前任务：agent 可以按需读取全部已导入原文及工程资料，在本次任务内完成节点、属性、关系和有效性核对；没有程序安排的固定多轮，也没有对话次数上限。任务没有硬性总时限，可由用户手动停止；模型服务的连接及响应空闲检测独立保留，默认推理等级为 `low`。

统一 agent 可以使用当前工程全部业务写入工具，随时补建和修订节点、属性、关系及包含结构；写入通过 `Operations` 静态检查并保留事务审计。每个活动事件必须归属场景，单事件也不能省略场景。 属性按事件记录建立、变更和失效，不再填写有效期，程序依据事件顺序推导状态。每个活动物品必须有当前世界事实层的持有者，明确为节点、“无”或“未知”；缺失与界面派生提示都不等于已记录未知。

关系复核会对照建立依据、后续终止及存续证据检查事件变更。明确终止时在对应事件追加 remove 指向具体对方，保留此前事实；争吵、一次攻击或后文未再提及不自动代表失效。泛称、反讽、否认和追述不能直接当成当前双方关系；原记录对象或依据有误时应纠错，不用“失效”掩盖错误。旧结果通过显式复核更新，不在程序升级时自动改写。

默认关系提取沿本批人物及事件角色核对身份、社会联系和实际作用，不以主线重要性筛掉次要关系；同一对对象的不同关系可以并存。单方“视为”或“自称”须归属相应认知者或说话人，不能据此补成双方客观关系；只有一次行为证据时保留在事件或事件属性中，不擅自扩成持续关系；关系变化须有对应事件证据。查漏针对当前范围进行，不要求把全部人物两两配对，也不新增例行全库审计。

常规提取直接调用 `submit_extraction`，由模型选择语义上支持事实的准确原文引文，程序自动完成逐字匹配、唯一性与坐标定位、编号、静态校验并原子入库；`preview_extraction` 仅供单独试查，不要求先重复提交同一草稿。它们是当前 agent 可调用的业务工具，不会启动另一个模型任务，也没有人工接受步骤。无歧义提取不需要先调用 `locate_source` 或 `preview_extraction`；定位工具仅用于确需坐标、消歧或处理提交返回的具体定位诊断。助手工具卡显示实际程序用时，不把模型多轮等待计作文本搜索时间。大事件需由正文发生过程支持，卷章标题只是组织信息；人物关系的性质与显示分类分别记录。

重新提取或复核时，已查证的事件用 `existing_id` 继续引用；事件保存全部 `evidence_spans`，不会因为换了首条引文就必然新建。只有同源、同名或已知别名、同场景与参与者，并有同位置完整证据命中的唯一候选才自动复用；有歧义时由 agent 查证，确属另一次发生用 `new_occurrence` 明确声明。同名本身不等于同一事件。 已有重复事件由模型查证后，通过 `preview_event_merge` / `merge_events` 显式合并；物理合并证据与当前属性、结构引用，删除重复节点，不留重定向或旧节点版本。开发不会直接修改用户历史结果。

有疑点时保留原因，不伪造事实。无效草稿返回诊断供 agent 修订，不能靠人工修改模型输出后宣称通过。模型每次可见输出和工具间说明都在会话中保留，工具过程默认折叠。会话原在底部时跟随新输出，翻阅历史时保持阅读位置；原始输出、使用量及完整工具记录另行保存。

完整工具调用因 JSON 格式错误失败时，原参数与诊断会保存并反馈给同一任务中的模型修正；程序不执行非法工具、不补改模型结果、不另开任务。网络错误、容量截断或响应持续空闲仍停止。

工具已经成功入库后，即使后续回答失败或由用户停止，已写入的事实仍保留。程序分别报告入库结果与整个对话任务是否完成，不自动重试模型请求。

关系图按当前可见连线计算位置：相连节点由弹簧吸引靠近，结合关系文字和同对多边数量留出空间；节点、标签均避碰，不连通的组件分开排布。没有按人物或节点 ID 指定的坐标，也不再强制把关系最多者放在圆心。数据或画布尺寸改变时重算；缩放和平移使用已有布局。

节点净距和独立分组间距已适度增大，关系标签使用更紧凑的留白和细边框，保持原字号与默认全图适配。拖动、缩放和阅读位置切换继续使用同一份关系数据，不通过隐藏关系来降低拥挤。

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


助手主机能力的 CLI / MCP 启动示例：

```powershell
narrative --access-mode full host-run --project 作品.nproj --command "python --version" --cwd C:/a
narrative-tools --access-mode full host-list --project 作品.nproj --path C:/a
narrative-mcp --project 作品.nproj --access-mode full
```

省略 `--access-mode` 为标准访问。完全访问须由启动参数授权，标准 MCP 不能通过聊天配置工具自行提权。CLI 前台命令等待实际结束，没有任务总超时；Ctrl+C 仅停止本次命令。CLI 不支持后台命令，请使用驻留助手或 MCP。MCP 在同一服务会话内保留命令归属，退出时仅收束本会话启动的命令，不停止其他任务。

助手模型结束回复时，若仍有未完成的前台命令被任务收尾停止，程序会保留命令清理收据，并将任务标为未完成、列出相关诊断；模型回复结束不等于这些命令执行成功。正常完成的命令和显式移交的后台命令不会被误报。工具过程将已匹配结果与未匹配的实际执行结果分别完整呈现，不因某条模型工具参数省略空值而隐藏后续回复。


助手面板的访问模式按聊天独立保存，默认标准访问。完全访问可操作工程外文件和执行系统命令，以当前操作系统账户权限运行，不会自动取得管理员权限。切换仅影响下一任务，当前任务使用启动时固定的权限，同一 SDK 会话不会因切换而重启。当前页面焦点不代表写入范围；业务写入仍经 Operations 静态检查与审计。

驻留助手成功结束任务时，明确请求的后台命令可移交给工程；工程关闭时收束这些命令。CLI 每次启动有独立命令归属，`host-run` 的前台结果包含本次完整输出；`host-output` 不接管另一进程的在运行命令，请在同一驻留 MCP 会话查询其命令。
