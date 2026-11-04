"""Shared screenplay tool catalog for CLI, MCP and the resident assistant."""
from __future__ import annotations


def register(Tool, register_tool, validate):
    text = {"type": "string"}
    integer = {"type": "integer"}
    obj = {"type": "object"}
    strings = {"type": "array", "items": text}
    objects = {"type": "array", "items": obj}
    reason = {"reason": text | {"required": True, "description": "本次修改依据，保留操作审计"}}
    timeline_revision = {"expected_timeline_revision": integer | {"required": True, "description": "目标剧本独立时间线修订号，不是源改编修订号"}}
    screenplay_id = {"screenplay_id": text | {"required": True}}
    selection = {"selection_ids": strings, "start_node_id": text, "end_node_id": text}
    basis = {"span_fingerprint": text | {"required": True, "description": "照用context返回的范围依据编号；程序内部还原校验，不计算hash"}, "context_fingerprint": text | {"required": True, "description": "照用context返回的上下文依据编号；程序内部校验"},
             "dependency_refs": objects | {"description": "可选明确参考kind/id数组；默认只含相关精简资料，可显式读取unit_plan/unit_body/previous_unit或adaptation_entity（含屏蔽/范围外参考），不会扩大创作或写入目标"}, "manifest_ref": {"type": ["string", "null"], "description": "对话任务由程序内部关联，模型省略"}, "task_id": {"type": ["string", "null"], "description": "对话任务由程序内部注入，模型省略"}}

    def add(name, summary, params, *, writes=False):
        def invoke(ops, args):
            for key, value in args.items():
                validate(value, params[key], key)
            try:
                return getattr(ops, name)(**args)
            except (ValueError, OSError) as exc:
                from .tool_api import ToolError
                raise ToolError(str(exc)) from exc
        register_tool(Tool(name, summary, params, invoke, writes=writes))

    add("preview_adaptation_span", "只读解析五级改编选区为连续事件/动作闭区间、逐点状态、屏蔽诊断及输入指纹；不生成动作", selection | screenplay_id)
    add("create_adaptation_span", "以当前剧本本地事件/动作锚点创建时间段；须先创建剧本并导入时间线", selection | screenplay_id | timeline_revision | reason | {
        "name": text | {"required": True}}, writes=True)
    add("update_adaptation_span", "修改范围名称或锚点，校验范围与当前剧本时间线修订；返回影响而不改写正文", timeline_revision | reason | {
        "span_id": text | {"required": True}, "changes": obj | {"required": True}, "expected_revision": integer | {"required": True}}, writes=True)
    add("read_adaptation_span", "读取时间段定义、当前动态成员与逐点状态指纹；失效/全屏蔽仍保留旧正文", {"span_id": text | {"required": True}})
    add("inspect_screenplay_storage", "只读检查当前剧本存储结构、行数及重建指纹；不迁移旧数据", {})
    add("reset_screenplay_storage", "明确丢弃并重建整个剧本域，不影响原文与改编；须先检查存储取得指纹", reason | {
        "scope": {"type": "string", "enum": ["screenplay"], "required": True},
        "expected_storage_fingerprint": text | {"required": True, "description": "原样传回检查返回的存储快照编号；程序内部校验，不计算hash"},
        "confirm_discard": {"type": "boolean", "required": True}}, writes=True)
    add("create_screenplay", "创建独立剧本方案；single单片，series剧集自动建立第1集；不写原作", reason | {
        "title": text | {"required": True}, "format": {"type": "string", "enum": ["single", "series"]},
        "language": text, "creative_brief": obj}, writes=True)
    add("import_screenplay_timeline_candidates", "只读列出可从源改编时间线多选导入的节点；不改变剧本副本", screenplay_id)
    add("read_screenplay_timeline", "读取指定剧本的独立时间线副本、属性、revision及来源；不自动跟随源改编", screenplay_id)
    add("import_screenplay_timeline", "将选定源改编时间点与必要状态引用复制到目标剧本；重复导入保留本地调整，不改源改编或其他剧本", screenplay_id | timeline_revision | reason | {
        "moment_ids": strings | {"required": True}, "expected_adaptation_revision": integer | {"required": True, "description": "源改编当前revision"}}, writes=True)
    add("append_screenplay_timeline", "将新选改编时间点及相关状态追加到剧本时间线末尾；已有节点、顺序、值与正文不覆盖；仅可为未改旧值补齐合法后续结束边界。重复ID跳过，实际状态冲突原子拒绝", screenplay_id | timeline_revision | reason | {
        "moment_ids": strings | {"required": True}, "expected_adaptation_revision": integer | {"required": True}}, writes=True)
    placement = {"after_id": {"type": ["string", "null"], "description": "排序参照；新建/移动省略或null置于开头，复制省略或null紧随原子树末项"}, "parent_id": {"type": ["string", "null"], "description": "省略或null保持归属；空字符串移为顶层，E/A仍须合法父级"}}
    node_id = {"node_id": text | {"required": True}}
    add("edit_screenplay_timeline_node", "在目标剧本独立时间线新建或编辑节点，绝不修改源改编；新物品initial_holder必须明确", screenplay_id | timeline_revision | reason | placement | {
        "node_id": {"type": ["string", "null"]}, "changes": obj | {"required": True}}, writes=True)
    add("move_screenplay_timeline_node", "调整目标剧本本地时间线顺序与归属；独立于源改编和其他剧本", screenplay_id | timeline_revision | reason | placement | node_id, writes=True)
    add("copy_screenplay_timeline_node", "复制当前剧本本地节点及子树，不修改源改编或其他剧本", screenplay_id | timeline_revision | reason | placement | node_id, writes=True)
    add("delete_screenplay_timeline_node", "物理删除当前剧本本地节点及子树并返回本地范围诊断；已用于本剧本场表或正文的实体须先显式修正引用", screenplay_id | timeline_revision | reason | node_id, writes=True)
    add("suppress_screenplay_timeline_node", "屏蔽或恢复目标剧本独立时间线节点及子树", screenplay_id | timeline_revision | reason | node_id | {"suppressed": {"type": "boolean"}}, writes=True)
    add("set_screenplay_timeline_property", "在目标剧本本地时点新增、更正或失效属性，不回写源改编", screenplay_id | timeline_revision | reason | node_id | {
        "property_id": text | {"required": True}, "value": {}, "event_id": {"type": ["string", "null"]},
        "record_id": {"type": ["string", "null"]}, "expire": {"type": "boolean"}}, writes=True)
    add("read_screenplay", "读取剧本、集幕树、唯一权威段落顺序及场表正文；省略ID列出剧本", {"screenplay_id": {"type": ["string", "null"]}})
    add("apply_screenplay_structure", "原子编辑集/幕/段落严格树及排序；非空集幕删除须明确搬移目标；变更模式须完整重组", reason | {
        "screenplay_id": text | {"required": True}, "actions": objects | {"required": True, "description": "typed op: create_episode(title,number?,brief?,id?); create_act(title,episode_id?,brief?,id?); update_episode(episode_id,expected_revision,changes); update_act(act_id,expected_revision,changes); update_screenplay(expected_revision,changes); move(child_kind,child_id,parent_kind,parent_id,after_entry_id?); delete_episode/delete_act(id字段,expected_revision,move_to?); delete_segment(segment_id,expected_revision,body_policy=require_empty|discard)。非空集幕删除必须给move_to。"},
        "expected_structure_revision": integer | {"required": True}, "format": {"type": "string", "enum": ["single", "series"]}}, writes=True)
    add("create_script_segment", "创建段落并绑定恰好一个时间段；结构入口决定集幕归属和排序", reason | {
        "screenplay_id": text | {"required": True}, "span_id": text | {"required": True}, "title": text | {"required": True},
        "expected_structure_revision": integer | {"required": True}, "parent_kind": {"type": ["string", "null"]},
        "parent_id": {"type": ["string", "null"]}, "after_entry_id": {"type": ["string", "null"]},
        "role": {"type": "string", "enum": ["normal", "prologue", "epilogue"]}, "brief": {"type": ["object", "string", "null"]},
        "target_duration_seconds": {"type": ["number", "null"]}}, writes=True)
    add("update_script_segment", "修改段落要求或范围绑定，使用本对象revision，不清空已有场表或正文", reason | {
        "segment_id": text | {"required": True}, "changes": obj | {"required": True}, "expected_revision": integer | {"required": True}}, writes=True)
    add("read_script_segment", "读取段落、场表、正文及逐场依据状态", {"segment_id": text | {"required": True}})
    add("read_scene_table_context", "读取目标段落、当前场表、范围与有效工程指导，返回提交所需修订和依据", {
        "segment_id": text | {"required": True}, "dependency_refs": objects | {"description": "可选明确参考kind/id数组；默认只含相关精简资料，可显式读取unit_plan/unit_body/previous_unit或adaptation_entity（含屏蔽/范围外参考），不会扩大创作或写入目标"}})
    unit_draft = {"type": "object", "description": "新场可省id；已有场传真实id。kind=scene/screen_segment，enabled，scene_number及完整plan；启用场至少非空summary，其余字段遵循schema。plan.source_event_ids可选，只记录本范围有效事件引用。保留全部已有ID；停用已有场须enabled=false且plan={}，不删除正文。"}
    add("submit_scene_table", "原子提交场表及依据；沿用稳定场次ID，不隐式删除已有场或清空正文", reason | basis | {
        "segment_id": text | {"required": True}, "units": {"type": "array", "items": unit_draft, "required": True},
        "expected_outline_revision": integer | {"required": True}}, writes=True)
    add("apply_scene_table_changes", "原子调整场表insert/update/move/delete；删除须显式body_policy=require_empty或discard，不隐式清空正文", reason | {
        "segment_id": text | {"required": True}, "changes": objects | {"required": True}, "expected_outline_revision": integer | {"required": True}}, writes=True)
    add("adopt_scene_table", "将当前场表标记采用或取消；仅工作标记，不构成人工生成审批", reason | {
        "segment_id": text | {"required": True}, "expected_outline_revision": integer | {"required": True}, "adopted": {"type": "boolean"}}, writes=True)
    add("read_script_unit_context", "读取单场plan、绑定范围、实际前文依赖、当前正文和提交指纹；不以终点状态代替全段", {
        "unit_id": text | {"required": True}, "dependency_refs": objects | {"description": "可选明确参考kind/id数组；默认只含相关精简资料，可显式读取unit_plan/unit_body/previous_unit或adaptation_entity（含屏蔽/范围外参考），不会扩大创作或写入目标"}, "target_block_ids": strings})
    block_draft = {"type": "object", "description": "{id?,type,payload}。新块省略id由程序自动分配可读编号；更新已有块才使用读取的稳定id。type:action(text),dialogue(speaker_id?,speaker_label,text,delivery=on_screen/off_screen/voice_over/inner_voice,parenthetical),screen_text(text,presentation=overlay/card),sound(text,category=effect/music/other,entity_id?),transition(kind,text?)"}
    add("submit_script_unit", "逐场原子提交正文和当前WritingBasis；校验本场plan/body修订及实际输入依赖，不要求无关场修订一致", reason | basis | {
        "unit_id": text | {"required": True}, "blocks": {"type": "array", "items": block_draft, "required": True},
        "expected_plan_revision": integer | {"required": True}, "expected_body_revision": integer | {"required": True},
        "plan_fingerprint": text | {"required": True, "description": "照用context返回的场次计划依据编号；不计算hash"}, "entity_usages": objects}, writes=True)
    add("apply_script_block_changes", "只修改明确目标块：insert(block,after_id?)/update(block_id,block)/move(block_id,after_id?)/delete(block_id)。保留其他正文；手改不伪造新的写作依据", reason | {
        "unit_id": text | {"required": True}, "changes": objects | {"required": True}, "target_block_ids": strings | {"required": True},
        "expected_body_revision": integer | {"required": True}}, writes=True)
    add("mark_writing_basis_current", "明确核对后记录新的当前写作依据和理由；不修改正文，不是接受队列", reason | {
        "target_kind": {"type": "string", "enum": ["segment", "unit"], "required": True}, "target_id": text | {"required": True},
        "expected_revision": integer | {"required": True}, "dependency_refs": objects | {"description": "可选明确参考kind/id数组；默认只含相关精简资料，可显式读取unit_plan/unit_body/previous_unit或adaptation_entity（含屏蔽/范围外参考），不会扩大创作或写入目标"},
        "manifest_ref": {"type": ["string", "null"], "description": "对话任务由程序内部关联，模型省略"}, "task_id": {"type": ["string", "null"], "description": "对话任务由程序内部注入，模型省略"}}, writes=True)
    add("prepare_script_writing_targets", "冻结按剧本顺序批量写作的unit清单；默认仅enabled且无正文scene，重写已有正文须明确指定", {
        "screenplay_id": text | {"required": True}, "unit_ids": strings,
        "include_existing": {"type": "boolean"}, "include_screen_segments": {"type": "boolean"}})
    add("read_script_writing_targets", "核对冻结目标与当前结构差异；不静默纳入任务中新场或重排目标", {"target_manifest": obj | {"required": True}})
    add("prepare_script_writing_task", "为当前聊天准备显式目标和不可变批量清单；只读，不改变已运行任务的权限", {
        "target": obj | {"required": True}, "prompt": text})
    add("check_screenplay", "只读检查剧本引用、结构、范围、场表与逐场正文依据；草稿提示不等于失败", {"screenplay_id": text | {"required": True}})
    add("export_screenplay", "按同一集幕/段落/场次/块顺序导出UTF-8文本或结构JSON，携带过期和失效提示", {
        "screenplay_id": text | {"required": True}, "format": {"type": "string", "enum": ["text", "json"]}, "path": {"type": ["string", "null"]}})
