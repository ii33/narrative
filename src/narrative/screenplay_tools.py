"""Shared screenplay tool catalog for CLI, MCP and the resident assistant."""
from __future__ import annotations

from .storyboard_contract import storyboard_change_schema


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

    add("read_task_write_summary", "只读当前工程可信任务审计的程序入库总结；只计实际成功写入，取消/失败保留已提交内容，不把预览/只读/拒绝当写入", {
        "task_id": text | {"required": True}, "chat_id": {"type": ["string", "null"]}})

    add("read_usage_statistics", "只读统计当前聊天/任务provider请求用量、缓存命中token及加权命中率、最近generation上下文输入；缓存是输入拆分不重复加总，缺报或无可比输入时命中率null，部分覆盖单列已知请求命中率，上下文不等于累计tokens", {
        "chat_id": {"type": ["string", "null"]}, "task_id": {"type": ["string", "null"]}})

    add("load_creative_agreement", "读取统一创作约定：工程默认、单份剧本逐项覆盖和实际生效值；字段为tone/audiovisual_style/dialogue_style/rhythm/audience/adaptation_principles/format_length/production_constraints/other。省略剧本时只读工程默认", {
        "screenplay_id": {"type": ["string", "null"]}})
    add("save_creative_agreement", "原子保存创作约定及可选剧本名称/语言。project_defaults为9字段文本对象；script_overrides缺省/null继承工程，空串显式不限制；只修改相应修订并保留审计", reason | {
        "project_defaults": obj | {"required": True}, "expected_project_revision": integer | {"required": True},
        "screenplay_id": {"type": ["string", "null"]}, "script_overrides": obj,
        "expected_script_revision": {"type": ["integer", "null"]}, "title": text, "language": text}, writes=True)

    add("read_storyboard", "按场读取分镜、静帧、预演帧率和共享二维调度对象/动作轨。毫秒区分剪辑时间、镜内offset_ms和场内动作time_ms；revision用于下一次原子编辑", {
        "unit_id": text | {"required": True}})
    add("check_storyboard", "只读静态检查本场引用、时间、媒体缺失及实际生成依据变化；成功的视频也可需复核。返回影响范围，不改旧图、不自动生成或审批", {
        "unit_id": text | {"required": True}})
    add("preview_storyboard", "只读求指定场剪辑time_ms的镜头、镜内local_ms、共享action_ms、保持的最近静帧和二维对象状态。切点属于下一镜，片尾为末镜末画面；与导演台使用同一时间约定，不写库", {
        "unit_id": text | {"required": True}, "time_ms": integer | {"required": True}})
    add("preview_storyboard_changes", "只读预演现有调度/标记/镜头修改，列出受影响镜头、帧和对象及时间越界冲突，不分配编号、不写数据。移动共享时刻前先查看；有冲突须显式处理后同批提交", {
        "unit_id": text | {"required": True}, "revision": integer | {"required": True},
        "changes": {"type": "array", "minItems": 1, "items": storyboard_change_schema(), "required": True}})
    add("load_storyboard_camera_presets", "读取工程保存的可展开运镜预设与修订；不包含其他场的对象编号", {})
    add("save_storyboard_camera_preset", "将基础运镜参数保存为工程预设。settings含preset、可选amount/start/end机位；不保存场内对象引用", reason | {
        "name": text | {"required": True}, "settings": obj | {"required": True},
        "expected_revision": integer | {"required": True}}, writes=True)
    add("apply_storyboard_changes", "原子编辑导演台，先read_storyboard取得revision。镜头create_shot(title,duration_ms,source_block_ids,action_start_ms=0)/update_shot(shot_id,...)/move_shot(shot_id,index)/delete_shot；action_ms=action_start_ms+镜内时间，调序不改动作。静帧set_keyframe(shot_id,offset_ms,sketch_path或style_path)/move_keyframe(shot_id,frame_id,offset_ms)/delete_keyframe(shot_id,frame_id)。set_fps(fps整数1..120)。调度create_object(name,kind=person|prop|set,x,y,rotation?,depth?,parent_id?,asset_id?,state?)/update_object(object_id,...)/delete_object(object_id)。位置x/y为0..1场平面，rotation为世界朝向度。set_action_keyframe(object_id,time_ms,x?,y?,rotation?,depth?,state?,interpolation=hold|linear)；move_action_keyframe(object_id,keyframe_id,time_ms)/delete_action_keyframe(object_id,keyframe_id)。interpolation控制到下一点的保持或线性运动，状态离散保持。set_approved(approved)按场定案；已定案须显式解除才能改方案。缩短时先显式调整越界帧，不自动丢弃。", {
        "unit_id": text | {"required": True}, "revision": integer | {"required": True},
        "changes": {"type": "array", "minItems": 1, "items": storyboard_change_schema(), "required": True}}, writes=True)

    add("generate_storyboard_sketch", "调用图像生成服务生成本镜描述型素描并绑定offset_ms；程序自动提供本镜正文、创作约定、当前动作调度和关联资产参考图。prompt补充本帧画面要求，simple_color默认false黑白灰。layout只作内部构图参考：composition=single/two_shot/object/empty、framing=wide/medium/close、light=left/right/back/overhead、setting=interior/exterior，可选subjects。最终图必须来自图像模型，失败保留旧图，不回退几何绘制。生成前后校验修订和依据，定案后须先显式解除。", {
        "unit_id": text | {"required": True}, "shot_id": text | {"required": True},
        "offset_ms": integer | {"required": True}, "layout": obj | {"required": True},
        "expected_revision": integer | {"required": True}, "prompt": text,
        "simple_color": {"type": "boolean"}}, writes=True)
    add("generate_storyboard_panel", "为独立画格调用一次真实草图生成，候选仅写入此格，不影响其他画格和关键帧；生成失败不回退。先用apply_storyboard_changes的create_panel建立intent/offset_ms/base，再取得panel_id。候选须明确采用。", {
        "unit_id": {"type": "string", "required": True}, "shot_id": {"type": "string", "required": True},
        "panel_id": {"type": "string", "required": True}, "expected_revision": {"type": "integer", "required": True},
        "prompt": {"type": "string"}}, writes=True)
    add("generate_storyboard_panels", "按画格计划顺序补缺格或整镜逐格试画。missing_only默认true，仅生成没有候选、导入底图、已有格快照或绑定帧的画格；每格一次，不自动采用，任一失败即停止并保留之前已完成候选，不重试。", {
        "unit_id": {"type": "string", "required": True}, "shot_id": {"type": "string", "required": True},
        "expected_revision": {"type": "integer", "required": True}, "missing_only": {"type": "boolean"},
        "prompt": {"type": "string"}}, writes=True)
    add("export_storyboard_panel", "将指定画格导出为新的PNG文件，默认无标注，不覆盖已有文件。", {
        "unit_id": {"type": "string", "required": True}, "shot_id": {"type": "string", "required": True},
        "panel_id": {"type": "string", "required": True}, "output_path": {"type": "string", "required": True},
        "annotated": {"type": "boolean"}})
    add("render_storyboard_style", "将本场定案草图按项目风格送入本机 ComfyUI，取得实际生成图片并绑定同一关键帧。每次调用生成一张图。", {
        "unit_id": text | {"required": True}, "shot_id": text | {"required": True},
        "frame_id": text | {"required": True}, "expected_revision": integer | {"required": True}}, writes=True)
    add("export_storyboard_script", "独立导出本场文字或图文分镜稿；指定新文件，不覆盖现有稿件。", {
        "unit_id": text | {"required": True}, "format": {"type": "string", "enum": ["text", "html", "pdf"], "required": True},
        "path": text | {"required": True}, "illustrated": {"type": "boolean"}, "annotated": {"type": "boolean"}}, writes=True)
    add("export_storyboard_style_package", "将定案镜头草图、逐帧编号、适用项目风格及正文打包为新 ZIP，供外部图像工具生成。output_path 必须是用户指定的新文件。", {
        "unit_id": text | {"required": True}, "shot_id": text | {"required": True},
        "output_path": text | {"required": True}}, writes=True)
    add("read_storyboard_video_settings", "读取本工程视频服务配置与has_api_key，不返回密钥；默认MiniMax H3付费API", {})
    add("save_storyboard_video_settings", "保存视频服务配置，settings为base_url/model/resolution/key_env；不接受或返回密钥值", reason | {
        "settings": obj | {"required": True}, "expected_revision": integer | {"required": True}}, writes=True)
    add("read_storyboard_video_receipts", "读取实际服务回执，包括所属镜头已删除时仍保留的提交/查询结果；不重新提交", {
        "unit_id": text | {"required": True}, "shot_id": {"type": ["string", "null"]}})
    video_target = {"unit_id": text | {"required": True}, "shot_id": text | {"required": True}}
    video_revision = video_target | {"revision": integer | {"required": True}}
    add("set_storyboard_animation", "设置本镜hold静帧保持、layers透明图层动画或camera平面推拉预演。图层path/start_ms/end_ms必填，可选稳定id/object_id/x/y/scale/opacity；省略layers保留现有图层，[]显式清空。二维图不推断未画出的侧面", video_revision | {
        "mode": {"type": "string", "enum": ["hold", "layers", "camera"], "required": True}, "layers": objects}, writes=True)
    add("render_storyboard_animatic", "导出真实无编辑标注MP4预演，按本镜帧时点与动画渲染；variant=sketch/style，风格图缺失拒绝；可显式合成临时声音。output_path须为用户指定的新MP4完整路径", video_target | {
        "output_path": text | {"required": True}, "variant": {"type": "string", "enum": ["sketch", "style"]},
        "include_audio": {"type": "boolean"}}, writes=True)
    add("attach_storyboard_video", "导入并采用真实可解码视频选段，不覆盖原文件。in_ms/out_ms在素材内；fit=trim要求选段等于镜头时长，retime显式变速适配；不自动改变本镜剪辑时长", video_revision | {
        "video_path": text | {"required": True}, "in_ms": integer | {"required": True}, "out_ms": integer | {"required": True},
        "fit": {"type": "string", "enum": ["trim", "retime"]}}, writes=True)
    add("detach_storyboard_video", "取消本镜采用视频，保留原始素材和生成记录，恢复草图/风格审阅", video_revision, writes=True)
    add("prepare_storyboard_video", "为已定案镜头准备实际可查看的素材和提示词，保存审阅记录但不提交。mode=reference_motion用干净草图动画+风格图，endpoints只用首尾风格帧，互斥。duration_ms须显式选择服务支持整秒，不自动改镜头。返回generation编号供下一步提交", video_revision | {
        "mode": {"type": "string", "enum": ["reference_motion", "endpoints"], "required": True},
        "duration_ms": integer | {"required": True}, "prompt": text, "frame_ids": strings, "model": text, "resolution": text}, writes=True)
    generation_target = video_revision | {"generation_id": text | {"required": True}}
    add("submit_storyboard_video", "将已准备并核对依据的生成记录提交一次；已提交/依据变化/缺少密钥拒绝，不自动重试。返回服务任务状态，成功素材不自动采用", generation_target, writes=True)
    add("refresh_storyboard_video", "查询同一已提交任务；成功下载并验证视频、回填原生成记录，不新建服务任务、不自动采用。failed/cancelled保留诊断", generation_target, writes=True)
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
    add("set_screenplay_timeline_property", "在目标剧本事件记录属性变更；set/add/remove携value，clear/unknown不携value，更正保sequence，不回写源改编", screenplay_id | timeline_revision | reason | node_id | {
        "property_id": text | {"required": True}, "value": {}, "event_id": text | {"required": True},
        "op": text | {"enum": ["set", "add", "remove", "clear", "unknown"], "required": True},
        "record_id": {"type": ["string", "null"]}}, writes=True)
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
    block_draft = {"type": "object", "description": "{id?,type,payload}。新块省略id由程序自动分配可读编号；更新已有块才使用读取的稳定id。type:scene_description(text，环境空间及氛围描写),action(text，人物或物体具体行为),dialogue(speaker_id?,speaker_label,text,delivery=on_screen/off_screen/voice_over/inner_voice,parenthetical),screen_text(text,presentation=overlay/card),sound(text,category=effect/music/other,entity_id?),transition(kind,text?)"}
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
    add("prepare_script_writing_task", "为当前聊天准备显式目标和不可变批量清单。只读，不改变已运行任务的权限", {
        "target": obj | {"required": True}, "prompt": text})
    add("check_screenplay", "只读检查剧本引用、结构、范围、场表与逐场正文依据；草稿提示不等于失败", {"screenplay_id": text | {"required": True}})
    add("load_compile_settings", "读取工程共享剧本编译排版设置、默认值与独立修订号", {})
    add("save_compile_settings", "保存工程共享剧本编译排版设置；严格校验单位、段落样式及并发修订并审计", {
        "settings": obj | {"required": True}, "expected_revision": {"type": "integer", "required": True}, "reason": text | {"required": True}}, writes=True)
    add("compile_screenplay", "按当前权威结构将启用场正文编译为可打印HTML、文本、Fountain、JSON、PDF或Word；诊断独立返回，不调用模型。PDF/Word无path时只预览", {
        "screenplay_id": text | {"required": True}, "format": {"type": "string", "enum": ["html", "text", "fountain", "json", "pdf", "docx"]}, "path": {"type": ["string", "null"]}})
    add("export_screenplay", "按同一集幕/段落/场次/块顺序导出UTF-8文本或结构JSON，携带过期和失效提示", {
        "screenplay_id": text | {"required": True}, "format": {"type": "string", "enum": ["text", "json"]}, "path": {"type": ["string", "null"]}})
