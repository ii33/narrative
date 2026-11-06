# 制作稿数据结构设计（已撤销的设计）

当前流程见[分镜导演台](STORYBOARD_DIRECTOR.md)。以下内容存作设计记录。

本版对应[正文对照编排交互设计](PRODUCTION_DRAFT_UI.md)。每份剧本一份当前制作稿，复用剧本的段落、场次、正文与顺序。正文只在剧本中维护；制作元素、出现范围、正文依据分别保存，不再以旧节拍表作为主结构。

## 对象与归属

| 对象 | 主要字段 | 含义 |
|---|---|---|
| ProductionDraft | id, screenplay_id, revision | 剧本唯一；revision保护并发修改 |
| ProductionSceneCard | id, draft_id, unit_id, intent, entry_conditions | 每场制作意图及入口条件 |
| ProductionElement | id, draft_id, unit_id, position, name, kind, content, purpose, source_refs | 元素内容和正文依据；position仅控制元素清单顺序 |
| ProductionOccurrence | id, element_id, start, end, suppressed | 一次出现的编排范围；一个元素可有多次出现 |
| ProductionMarker | id, draft_id, unit_id, block_id, position, label | 多个端点可共同引用的同步位置 |
| ProductionRequirement | id, draft_id, scope, kind, subject, instruction, origin, decision_status, evidence, timing, change | 分层制作要求，证据与适用范围分离 |
| ElementAssetUse | id, element_id, requirement_id, modes, note | 本元素如何使用共享资源需求 |
| ProductionBinding | id, requirement_id, role, asset_id, image_id, selection_hash | 将需求关联到可复用资源卡及可选图片 |
| ElementRelation | id, draft_id, from_element_id, relation, to_element_id | 明确的先后、同时开始或重叠关系 |
| ShotCoverage | id, shot_id, element_id或block_id, requirement_id, review_ref | 下游镜头承担要求；安排与实际核对分开 |
| ShotFileNeed | id, draft_id, unit_id, shot_id, kind, purpose, element_ids, resource_binding_ids | 本镜文件需求，无文件时也能登记 |
| ShotFileSelection | id, need_id, file_ref, role, selection_hash | 实际选用文件；与登记需求是独立操作 |

元素kind必须显式填写，为scene_description、action、dialogue、sound、screen_text、transition或other，不默认归为action。正文块仍保留自己的类型和完整内容，元素不改写正文。

一个元素对应一种主要制作类型及可独立控制的声画项，不用剧情标题合并动作、对白和声音。对白按单一说话人的连续发言组织；低声、停顿等附属表达可保留，独立肢体动作和音效分别建立元素。动作按独立行为或结果拆分，不逐字拆动词；持续声音可跨越对白区段。并行用同步标记、范围重叠和关系表达，other不能作为多类型混装的兜底。类型由实际制作内容决定，不机械照抄正文块类型；跨类型source_refs仍可作为合法依据。轨道通过中文类型标签、固定配色及图例表达类型，横向避让位置不代表类型。

## 位置、出现与同步

端点Point有且只有一种形式：

- `{block_id, position}`：position为0至1000的整数，表示该正文区段内的有序位置。0为区段起点，1000为区段终点。它不是像素、秒数或引文字数。
- `{marker_id}`：实际位置由同场同步标记给出，不得同时填写block_id或position。

start必填，end为Point或null。null表示开放终点，显示时延续到当前场尾，不能伪称已确定结束。非开放范围须正向且端点属于同场。正文调序后重新检查范围，不按编号大小排序。

正文依据source_refs与出现范围无须重合。例如声音依据可在声音块，出现从动作末端开始并延续到对白块。同一片段可供多个元素引用，同一元素可多次出现。屏蔽只改变suppressed，保留端点并可恢复；删除是物理删除并检查引用。

移动本次出现使用move_occurrence，提供新start/end；直接位置会解除该端点原有标记引用。移动共享标记前使用preview_production_marker_move，返回受影响出现及范围诊断，再以update_marker原子移动全部引用端点。删除标记须显式解除或改挂；解除时转为原直接位置，不丢原范围。

显示用的行高、像素和横向lane由程序计算，不写入业务数据。正文与编排共用区段边界、折叠状态及纵向滚动；重叠出现分配不同横向位置，不能据左右位置推断业务关系。

## 正文依据与修订

source_refs和evidence中的引用包含block_id、anchor和内部basis_hash。anchor为whole或text；text保存payload.text内Unicode半开区间[start,end)及逐字quote。提交唯一短句时程序定位，重复时要求消歧，不模糊挂接。

改正文后分别报告依据变化、依据缺失和范围端点失效。保留制作对象供显式重新定位，不按同名、近似文字或最近块自动修复。像素排版变化不影响业务端点，正文顺序和块删除会触发静态检查。

## 要求与资源

scope为`{type:segment|unit|block|element,id}`。段落和场次要求共享；块与元素要求用于具体表达。读取时按元素实际出现范围汇总适用块要求，不用正文依据冒充出现位置。timing可用Point起点和可开放终点进一步限定，按区间交集判断适用。

| 字段 | 约定 |
|---|---|
| kind | appearance、environment、performance、sound、screen_text、continuity、visibility |
| subject | 局部label或entity_id及timeline_id；可含perspective、state_layer、holder_id |
| instruction | 可据此核对画面或声音的要求 |
| origin | script正文明示、inference推导、proposal提案；采用提案后保留proposal来源 |
| decision_status | pending待定、adopted采用、declined未采用 |
| evidence | 正文证据；script至少一项 |
| timing | 可选`{start:Point,end:Point或null}`，须在作用范围内 |
| change | 可选`{property,before,after}`，仅影响这份剧本的制作连续性 |
| asset_category / required_traits | 可选资源类别及所需特征；未选资源也能保存需求 |

世界事实、角色认知、角色说法和观众披露分别表达。认知要求必须指定持有人。资源绑定不意味着要求该物体在本次出现中清晰可见。

ElementAssetUse.modes可组合visible、offscreen_action、audible、mentioned、reference。共享道具卡可分别用于不同元素的失效形态与可用形态，具体用法写入note或对应要求。

可复用资源包括角色定妆、场景空间基准、道具形态、通用动作和基础音色。ProductionBinding记录实际选材及内容摘要。图片移除、资源变化分别报告missing、changed；不能用资产库级修订号冒充历史图片版本。声音要求可先保存，实际录音须有可解析资源，不能用图片冒充音频。

## 镜头文件与覆盖

ShotFileNeed.kind为keyframe、blocking、action_reference或other，可关联多个元素与资源绑定。用途说明构图、走位、动作或同步要求。ShotFileSelection.file_ref为`{asset_id,image_id?}`或`{path}`，由程序解析并核对真实文件。图片资源卡须指定可解析的具体图片；仅有卡片元数据不算实际文件。同一文件可供多个镜头需求使用。

选择文件不等于覆盖已验证。ShotCoverage分别表示未分配、已安排、已验证；已验证须有针对实际产物的核对记录。更换资源、要求或相关正文后标记待复核。将本镜文件沉淀为通用资源必须显式加入资源库。

## 业务编号与操作入口

编号稳定、可读，移动和改名保留编号，删除后不复用。人物P3等已有业务编号沿用。新对象省略正式ID，同批前序动作可用@1、@2引用，返回created_ids映射。模型无需填写UUID、hash或并发令牌。

changes数组的每个动作必须使用op字段，例如`{op:"create_element",name:"持续环境声",kind:"sound"}`；不得使用operation代替。字段错误由程序拒绝并给出纠错诊断，不由宿主改写参数。

UI、CLI、MCP与统一对话均经Operations：

- read_production_draft按剧本、段、场、块读取，返回修改合同及readN依据批次，不因读取初始化制作稿。
- apply_production_changes原子执行元素、出现、标记、要求、资源用法、绑定、关系、镜头文件与核对记录的类型化修改。
- preview_production_marker_move只读预览共享标记移动影响。
- check_production_draft检查结构、精确证据、范围、引用、资源、连续性及同步冲突。
- export_production_scene输出JSON、文本或HTML，编辑与预览读取同一批对象。
- reset_production_draft显式清空当前剧本的制作域并重建，不做版本识别、迁移或数据适配；不影响剧本正文、资源、其他剧本和历史任务。
- import_production_file将外部文件复制到工程资料目录，重名不覆盖，记录审计；返回工程内路径后再独立选择为镜头文件，不自动创建共享资源卡。

写入按读取批次核对实际修订、正文和资源依据，冲突要求重读，不能在提交时替换为最新摘要掩盖过期读取。未处理引用的删除原子拒绝。所有写入保留事务审计。

清空当前场的所有制作元素使用单个`{op:"clear_elements"}`，由程序在一次事务中删除元素和直属出现、元素要求、用法及相关引用，不让模型先分批删子项再删元素。正文、基础资源、段场块共享要求及独立同步标记保留；本场镜头文件需求保留并移除被删除引用，跨场影响则明确拒绝。返回实际删除数量、逐项提交记录及当前场剩余元素数。限定正文块的任务不得清空整场；明确整稿重建也不能借此绕过冻结范围。界面的“清空本场元素”直接调用该操作，不调用模型服务。

## 格式化制作稿与验收

按场和正文顺序保留场头、各类正文及完整对白。每区列出本区新起的元素、关系、制作要求与资源用法；并行项标同时，跨区首次写全、中间标持续、结束处标结束，开放尾端明确标注未定。默认略去被屏蔽出现。区段归属取出现范围，正文依据仅供追溯。

最小验收覆盖：同动作区内旧物与新物并行；声音在动作末端进入并跨声音区延续到对白；依据与范围独立；共享标记移动及失效拒绝；屏蔽恢复；跨区高亮、同步滚动与折叠；复用资源不同用法；本镜需求与实际文件选择分离；正文变化及并发冲突；格式化预览与业务数据一致。
