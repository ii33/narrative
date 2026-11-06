"""Deterministic summaries of successful atomic tool submissions, not model prose."""
from __future__ import annotations

import json
import re
from copy import deepcopy

ACTIONS = {'created': '新增', 'updated': '修改', 'deleted': '删除', 'expired': '失效', 'moved': '移动', 'suppressed': '屏蔽', 'restored': '恢复', 'imported': '导入', 'reset': '重建', 'saved': '保存'}
TYPES = {'person': '人物', 'location': '地点', 'item': '物品', 'event': '事件', 'action': '动作', 'scene': '场景', 'big_event': '大事件', 'storyline': '主要故事', 'node': '节点', 'property_change': '属性变更', 'property_definition': '属性定义', 'element': '制作元素', 'occurrence': '出现', 'marker': '同步标记', 'requirement': '制作要求', 'use': '资源用法', 'binding': '资源绑定', 'relation': '元素关系', 'coverage': '镜头承担', 'review': '核对记录', 'shot_file_need': '镜头文件需求', 'shot_file_selection': '镜头文件选择', 'unit': '场次', 'block': '正文块', 'segment': '段落', 'screenplay': '剧本', 'asset': '资产', 'card': '资料卡', 'image': '图片', 'source': '原文', 'slice': '原文切片', 'settings': '设置', 'production_draft': '制作稿'}
_CHILDREN = ('nodes', 'properties', 'units', 'blocks', 'elements', 'occurrences', 'markers', 'requirements', 'uses', 'bindings', 'relations', 'coverage', 'reviews', 'assets', 'images', 'reference_images', 'segments', 'screenplay', 'span', 'scene_card', 'document', 'scenes', 'shot_file_needs', 'shot_file_selections')
TYPES.update(storyboard='分镜方案', scene_card='场卡', file='文件', timeline='时间线', span='时间段', screenplay_storage='剧本存储')


def _index(value):
    result = {}
    def visit(row):
        if isinstance(row, list):
            for item in row:
                visit(item)
        elif isinstance(row, dict):
            if isinstance(row.get('id'), str):
                result[row['id']] = row
            for key in _CHILDREN:
                if key in row:
                    visit(row[key])
    visit(value)
    return result


def _name(row):
    if not isinstance(row, dict):
        return ''
    name = row.get('name') or row.get('title') or row.get('file_name') or row.get('label') or row.get('plan', {}).get('title') or row.get('property_id')
    if name:
        return str(name)
    text = ' '.join(str((row.get('payload') or {}).get('text') or '').split())
    return text[:48] + ('…' if len(text) > 48 else '')


def _domain(tool):
    if 'storyboard' in tool:
        return 'storyboard'
    if 'production' in tool:
        return 'production'
    if 'screenplay' in tool or 'script' in tool or 'scene_table' in tool or 'adaptation_span' in tool or 'writing_basis' in tool:
        return 'screenplay'
    if 'adaptation' in tool:
        return 'adaptation'
    if 'asset' in tool:
        return 'assets'
    if 'material' in tool:
        return 'materials'
    if tool in {'save_project_style', 'save_prompt_override', 'save_creative_agreement', 'save_compile_settings', 'set_extraction_granularity'}:
        return 'settings'
    return 'extraction'


def _changes(tool, args, result):
    index = _index(result)
    changes = []
    def add(action, kind, identifier=None, row=None, count=1):
        data = row if isinstance(row, dict) else index.get(identifier, {})
        changes.append({'domain': _domain(tool), 'action': action, 'object_type': kind, 'id': identifier,
                        'name': _name(data), 'count': count})
    if not isinstance(result, dict):
        if tool == 'import_source' and isinstance(result, str):
            add('imported', 'source', result, {'title': args.get('title', '')})
        elif tool in {'create_source_slices', 'create_pattern_slices'} and isinstance(result, list):
            for row in result:
                if isinstance(row, dict):
                    add('created', 'slice', row.get('id'), row)
        return changes
    if isinstance(result, dict) and isinstance(result.get('committed_changes'), list):
        return [row | {'domain': row.get('domain', _domain(tool))} for row in result['committed_changes']]
    if tool in {'apply_production_changes', 'apply_script_block_changes', 'apply_scene_table_changes', 'apply_screenplay_structure'}:
        created = result.get('created_ids', {})
        for ordinal, action in enumerate(args.get('changes', []), 1):
            op = action.get('op', '')
            if tool == 'apply_production_changes' and op == 'clear_elements':
                for receipt in result.get('clear_elements_summaries', []):
                    if receipt.get('action_index') == ordinal:
                        changes.extend(receipt.get('committed_changes', []))
                continue
            verb, _, kind = op.partition('_')
            if tool == 'apply_script_block_changes':
                kind = 'block'
            elif tool == 'apply_scene_table_changes':
                kind = 'unit'
            elif tool == 'apply_screenplay_structure':
                kind = action.get('kind', kind or 'structure')
            if op == 'upsert_scene_card':
                cards = result.get('scene_cards') or [scene.get('scene_card') for scene in result.get('scenes', []) if scene.get('scene_card')]
                card = next((row for row in cards if row.get('unit_id') == args.get('unit_id')), {})
                identifier = created.get('@' + str(ordinal)) or card.get('id')
                add('created' if '@' + str(ordinal) in created else 'saved', 'scene_card', identifier, card or action)
                continue
            category = {'create': 'created', 'insert': 'created', 'update': 'updated', 'delete': 'deleted', 'move': 'moved', 'suppress': 'suppressed' if action.get('suppressed', True) else 'restored'}.get(verb)
            if category:
                identifier = created.get('@' + str(ordinal)) or action.get(kind + '_id') or action.get('id') or action.get('unit_id') or action.get('block_id')
                add(category, kind, identifier, index.get(identifier) or action.get('unit') or action.get('block') or action)
        return changes
    if tool in {'apply_storyboard_changes', 'generate_storyboard_sketch', 'generate_storyboard_panel', 'generate_storyboard_panels', 'render_storyboard_style',
                'set_storyboard_animation', 'attach_storyboard_video', 'detach_storyboard_video',
                'prepare_storyboard_video', 'submit_storyboard_video', 'refresh_storyboard_video'}:
        add('saved', 'storyboard', args.get('unit_id'))
    elif tool in {'render_storyboard_animatic', 'export_storyboard_panel', 'export_storyboard_script'}:
        add('saved', 'file', row={'name': result.get('path') or result.get('output_path', '')})
    elif tool in {'save_storyboard_camera_preset', 'save_storyboard_video_settings'}:
        add('saved', 'settings')
    elif tool == 'submit_script_unit':
        add('updated', 'unit', args.get('unit_id') or result.get('id'), result)
    elif tool == 'submit_scene_table':
        for row in result.get('units', []):
            add('saved', 'unit', row['id'], row)
    elif tool in {'submit_extraction', 'apply_changes'}:
        for kind, key in (('node', 'node_count' if tool == 'submit_extraction' else 'graph_count'), ('property_change', 'property_count')):
            if result.get(key):
                add('saved', kind, count=result[key])
        for kind, key in (('node', 'deleted_graph_count'), ('property_change', 'deleted_property_count')):
            if result.get(key):
                add('deleted', kind, count=result[key])
    elif tool in {'delete_property_records', 'delete_adaptation_property_changes'}:
        for key in result.get('record_ids', args.get('record_ids', args.get('ids', []))):
            add('deleted', 'property_change', key)
    elif tool in {'record_property_change', 'correct_property'}:
        add('saved' if tool == 'record_property_change' else 'updated', 'property_change', result.get('record_id') or args.get('record_id') or args.get('record', {}).get('id'))
    elif tool in {'create_source_slices', 'create_pattern_slices'}:
        for row in result.get('slices', result if isinstance(result, list) else []):
            add('created', 'slice', row.get('id'), row)
    elif tool in {'edit_adaptation_node', 'edit_screenplay_timeline_node', 'copy_adaptation_node', 'copy_screenplay_timeline_node', 'move_adaptation_node', 'move_screenplay_timeline_node', 'delete_adaptation_node', 'delete_screenplay_timeline_node', 'suppress_adaptation_node', 'suppress_screenplay_timeline_node'}:
        if tool.startswith('delete_'):
            for key in result.get('deleted_node_ids', [args.get('node_id')]):
                add('deleted', 'node', key)
            for key in result.get('deleted_property_ids', []):
                add('deleted', 'property_change', key)
        elif tool.startswith('copy_'):
            for key in result.get('copied_node_ids', [result.get('copied_node_id')]):
                add('created', index.get(key, {}).get('node_type', 'node'), key)
        else:
            key = result.get('created_node_id') or args.get('node_id')
            category = 'created' if result.get('created_node_id') else 'moved' if tool.startswith('move_') else 'suppressed' if tool.startswith('suppress_') and args.get('suppressed', True) else 'restored' if tool.startswith('suppress_') else 'updated'
            add(category, index.get(key, {}).get('node_type', 'node'), key)
    elif tool in {'import_adaptation', 'import_screenplay_timeline', 'append_screenplay_timeline', 'sync_adaptation'}:
        summary = result.get('import_summary') or result.get('sync_summary') or {}
        for kind, key in (('node', 'added_node_ids'), ('property_change', 'added_property_ids')):
            for identifier in summary.get(key, []):
                add('imported', kind, identifier)
        if not changes:
            add('imported' if tool != 'sync_adaptation' else 'updated', 'timeline', args.get('screenplay_id') or 'adaptation-main')
    elif tool in {'set_adaptation_property', 'set_screenplay_timeline_property'}:
        add('updated' if args.get('record_id') else 'saved', 'property_change', args.get('record_id'))
    elif tool in {'import_production_file', 'import_source'}:
        add('imported', 'file' if tool == 'import_production_file' else 'source', result.get('path') or result.get('id') if isinstance(result, dict) else result, result if isinstance(result, dict) else {'title': args.get('title', '')})
    elif tool in {'create_screenplay', 'create_script_segment', 'create_adaptation_span'}:
        kind = {'create_screenplay': 'screenplay', 'create_script_segment': 'segment', 'create_adaptation_span': 'span'}[tool]
        row = result.get(kind, result)
        add('created', kind, row.get('id'), row)
    elif tool in {'reset_production_draft', 'reset_screenplay_storage'}:
        add('reset', 'production_draft' if tool == 'reset_production_draft' else 'screenplay_storage', args.get('screenplay_id'))
    elif tool in {'save_asset', 'delete_asset'}:
        add('deleted' if tool == 'delete_asset' else 'updated' if args.get('payload', {}).get('id') else 'created', 'asset', result.get('id') or args.get('asset_id'), result)
    elif 'material_card' in tool or 'material_image' in tool:
        kind = 'image' if 'image' in tool else 'card'
        add('created' if tool.startswith('add_') else 'deleted' if tool.startswith('delete_') else 'updated', kind, result.get('id') or result.get('removed') or args.get(kind + '_id'), result)
    elif tool in {'revise_node', 'attach_children'}:
        add('updated', 'node', args.get('node_id') or args.get('id'), result)
    elif tool in {'save_project_style', 'save_prompt_override', 'save_creative_agreement', 'save_compile_settings', 'set_extraction_granularity'}:
        add('updated', 'settings', args.get('screenplay_id') or args.get('prompt_id') or tool)
    return changes


def content_commit_changes(operation, arguments, result):
    """Project a committed Operations receipt without exposing its full input."""
    aliases = {'material_add_card': 'add_material_card', 'material_update_card': 'update_material_card',
               'material_add_image': 'add_material_image', 'material_update_image_caption': 'update_material_image'}
    if operation == 'apply_screenplay_structure':
        arguments = arguments | {'changes': arguments.get('actions', [])}
    rows = _changes(aliases.get(operation, operation), arguments, result)
    if rows or not isinstance(result, dict):
        return rows
    typed_updates = {'update_adaptation_span': ('span', 'span_id'),
                     'update_script_segment': ('segment', 'segment_id'),
                     'save_storyboard_camera_preset': ('settings', 'name'),
                     'set_storyboard_animation': ('storyboard', 'shot_id')}
    if operation in typed_updates:
        kind, field = typed_updates[operation]
        return [{'domain': _domain(operation), 'action': 'updated', 'object_type': kind,
                 'id': arguments.get(field), 'name': arguments.get(field) or '', 'count': 1}]
    return []


def build_submission_summary(steps, status='completed', *, task_id=None, host_cleanup=None):
    from .tool_api import TOOLS
    rows, operations, diagnostics, host_operations = [], [], [], []
    latest_submissions = {}
    for step in steps or []:
        if not isinstance(step, dict) or step.get('ok') is not True:
            continue
        result = step.get('result')
        if not isinstance(result, dict) or not isinstance(result.get('submission_id'), str):
            continue
        identifier = result['submission_id']
        name = step.get('tool') or step.get('name')
        if name not in {'read_extraction_submission', 'read_content_submission'} and isinstance(result.get('submission_revision'), int):
            pass
        elif name in {'read_extraction_submission', 'read_content_submission'} and identifier in latest_submissions:
            pass
        else:
            continue
        revision = result.get('submission_revision', 0)
        revision = revision if type(revision) is int else 0
        prior = latest_submissions.get(identifier)
        if prior is None or revision >= prior[0]:
            latest_submissions[identifier] = (revision, result)
    seen_calls = set()
    for ordinal, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        name = step.get('tool') or step.get('name')
        if not isinstance(name, str):
            continue
        tool = TOOLS.get(name)
        result = step.get('result')
        args = step.get('args') or {}
        if isinstance(result, dict):
            superseded_stage = (result.get('status') == 'staged'
                                and latest_submissions.get(result.get('submission_id'), (None, result))[1] is not result)
            for key in (() if superseded_stage else ('diagnostics', 'issues', 'problems')):
                for diagnostic in result.get(key, []) or []:
                    if isinstance(diagnostic, dict):
                        diagnostics.append({k: diagnostic[k] for k in ('severity', 'code', 'message', 'object_id', 'object_ids', 'section', 'index', 'ref', 'reference', 'targets', 'field', 'path', 'model_location', 'editable_roots') if k in diagnostic})
            if result.get('feedback_error'):
                diagnostics.append({'severity': 'warning', 'code': 'feedback_error', 'message': str(result['feedback_error'])})
        if step.get('ok') is False and step.get('error'):
            diagnostics.append({'severity': 'error', 'code': 'tool_failed', 'tool': name, 'message': str(step['error'])})
        # Historical submissions remain readable after their tools leave the catalog.
        historical_write = name in {'apply_production_changes', 'reset_production_draft', 'import_production_file'}
        if not (historical_write or tool and tool.writes) or step.get('ok') is not True:
            continue
        if isinstance(result, dict) and (result.get('status') in {'staged', 'rejected', 'preview', 'conflict', 'failed'} or any(result.get(key) is False for key in ('valid', 'accepted', 'applied')) or result.get('preview') is True or result.get('dry_run') is True):
            continue
        # Call identity is optional. Equal args are not a safe deduplication key.
        identity = step.get('call_id') or step.get('tool_call_id')
        if identity and identity in seen_calls:
            continue
        if identity:
            seen_calls.add(identity)
        if tool and tool.scope == 'host':
            host_operations.append({'tool': name, 'step': ordinal + 1,
                                    'status': result.get('status') if isinstance(result, dict) else None})
            continue
        no_change = isinstance(result, dict) and (result.get('status') == 'unchanged' or result.get('committed_changes') == [] or result.get('committed') is False)
        changes = [] if no_change else _changes(name, args, result if result is not None else {})
        rows.extend(changes)
        operations.append({'tool': name, 'step': ordinal + 1, 'action_count': 0 if no_change else sum(row.get('count', 1) for row in changes) if changes else None, 'unresolved': not no_change and not bool(changes), 'committed': not no_change, 'no_change': no_change})
        if name == 'apply_production_changes' and isinstance(result, dict):
            receipts = result.get('clear_elements_summaries', [])
            operations[-1]['clear_elements'] = [{key: receipt[key] for key in ('unit_id', 'deleted_element_count', 'deleted_dependency_count', 'remaining_elements', 'committed_changes', 'deleted_ids_by_type') if key in receipt}
                                                 for receipt in receipts if isinstance(receipt, dict)]
        if name == 'submit_extraction':
            for reason in args.get('result', {}).get('uncertainties', []):
                diagnostics.append({'severity': 'warning', 'code': 'extraction_uncertainty', 'message': reason if isinstance(reason, str) else json.dumps(reason, ensure_ascii=False)})
    summary = _compose(rows, operations, diagnostics, status, task_id)
    pending = [{'submission_id': identifier, 'submission_revision': revision,
                'problem_count': len(result.get('problems') or result.get('diagnostics') or [])}
               for identifier, (revision, result) in latest_submissions.items() if result.get('status') == 'staged']
    summary['pending_submissions'] = pending
    summary['host_operations'] = host_operations
    if host_operations:
        labels = {'write_host_file': '写入文件', 'run_host_command': '启动命令', 'stop_host_command': '停止命令'}
        counts = {}
        for operation in host_operations:
            label = labels.get(operation['tool'], '系统操作')
            counts[label] = counts.get(label, 0) + 1
        detail = '，'.join(f'{label}{count}' for label, count in counts.items())
        prefix = summary['compact_text'].split('：', 1)[0]
        text = (summary['compact_text'] + '，' + detail if operations else prefix + '：' + detail)
        summary.update(text=text, compact_text=text)
    if isinstance(host_cleanup, dict):
        summary['host_cleanup'] = deepcopy(host_cleanup)
        summary['diagnostics'].extend(deepcopy(host_cleanup.get('diagnostics') or []))
        unfinished = [row for row in host_cleanup.get('cleanup_receipts', [])
                      if row.get('action') == 'stopped_at_task_end'
                      and row.get('reason') == 'task_completed_before_command_finished']
        summary['host_commands_incomplete'] = len(unfinished)
        if unfinished:
            summary['status'] = 'incomplete'
            detail = summary['compact_text'].split('：', 1)[-1]
            summary['text'] = summary['compact_text'] = f'未完成：{detail}；部分命令未完成，已停止{len(unfinished)}个'
    if pending:
        summary['needs_correction'] = True
        if status == 'completed':
            summary['status'] = 'incomplete'
        prefix = '未完成' if status == 'completed' else summary['compact_text'].split('：', 1)[0]
        detail = summary['compact_text'].split('：', 1)[-1]
        text = f'{prefix}：{detail}；暂存{len(pending)}批待修正'
        summary['text'] = summary['compact_text'] = text
    return summary


def _compose(rows, operations, diagnostics, status, task_id):
    status = 'failed' if status == 'incomplete' else status
    changes = []
    positions = {}
    for row in rows:
        occurrences = row.get('occurrences', 1)
        row = {key: row.get(key) for key in ('domain', 'action', 'object_type', 'id', 'name', 'count')}
        row['count'] = row['count'] if isinstance(row['count'], int) else 1
        identity = (row['domain'], row['action'], row['object_type'], row['id']) if row['id'] else None
        if identity and identity in positions:
            changes[positions[identity]]['occurrences'] += occurrences
        else:
            if identity:
                positions[identity] = len(changes)
            changes.append(row | {'occurrences': occurrences, 'action_label': ACTIONS.get(row['action'], row['action']),
                                  'type_label': TYPES.get(row['object_type'], row['object_type'])})
    totals = {action: sum(row['count'] for row in changes if row['action'] == action) for action in ACTIONS}
    unique_diagnostics = {json.dumps(row, sort_keys=True, ensure_ascii=False): row for row in diagnostics}
    committed = sum(row['committed'] for row in operations)
    grouped = {}
    for row in changes:
        key = (row['action'], row['object_type'])
        grouped[key] = grouped.get(key, 0) + row['count']
    clear_receipts = [receipt for operation in operations for receipt in operation.get('clear_elements', [])]
    dependency_ids = {(row.get('object_type'), row.get('id'))
                      for receipt in clear_receipts for row in receipt.get('committed_changes', [])
                      if row.get('action') == 'deleted' and row.get('object_type') != 'element' and row.get('id')}
    dependency_ids.update((kind, identifier) for receipt in clear_receipts
                          for kind, identifiers in receipt.get('deleted_ids_by_type', {}).items()
                          if kind != 'element' for identifier in identifiers)
    related_count = 0
    for row in changes:
        if row['action'] == 'deleted' and (row['object_type'], row['id']) in dependency_ids:
            grouped[(row['action'], row['object_type'])] -= row['count']
            related_count += row['count']
    parts = [f"{ACTIONS.get(action, action)}{TYPES.get(kind, kind)}{count}"
             + (f'及相关记录{related_count}' if action == 'deleted' and kind == 'element' and related_count > 0 else '')
             for (action, kind), count in sorted(grouped.items(), key=lambda item: 0 if clear_receipts and item[0] == ('deleted', 'element') else 1) if count]
    if len(parts) > 3:
        parts = parts[:3] + [f'另{len(parts) - 3}类变更']
    outcome = {'completed': '完成', 'failed': '失败', 'cancelled': '停止'}.get(status, '未完成')
    detail = '，'.join(parts) if parts else '变更已保存' if committed else '无入库变更'
    compact_text = outcome + '：' + detail
    text = compact_text
    return {'task_id': task_id, 'status': status, 'source': 'program', 'committed_operations': committed, 'completed_operations': len(operations),
            'has_changes': bool(committed), 'no_changes': not committed, 'totals': totals, 'changes': changes,
            'object_counts_complete': not any(row['unresolved'] for row in operations),
            'clear_elements': clear_receipts, 'operations': operations, 'diagnostics': list(unique_diagnostics.values()), 'text': text, 'compact_text': compact_text}


project_task_write_summary = build_submission_summary


class TaskWriteSummaryOperationsMixin:
    def project_info(self):
        raise NotImplementedError

    def summarize_task_writes(self, steps, status='completed', task_id=None):
        return build_submission_summary(steps, status, task_id=task_id)

    def read_task_write_summary(self, task_id, chat_id=None):
        from .project_package import layout
        if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', task_id):
            raise ValueError('任务编号无效')
        root = layout(self.project_info().path).extraction / 'assistant' / 'tasks'
        path = root / task_id / 'task.json'
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('任务不属于当前工程')
        if path.is_file():
            audit = json.loads(path.read_text(encoding='utf-8'))
            if chat_id and audit.get('chat_id') != chat_id:
                raise ValueError('任务不属于指定聊天')
            return build_submission_summary(audit.get('steps', []), audit.get('status', 'incomplete'), task_id=task_id,
                                            host_cleanup=audit.get('host_cleanup'))
        if not chat_id:
            raise ValueError('任务审计不存在；读取无头任务总结须明确chat_id')
        from .chat_store import ChatStore
        store = ChatStore(self.project_info().path)
        store.load(chat_id)
        turn_root = root.parent / 'chats' / chat_id / 'turns'
        latest = None
        seen = set()
        for metadata_path in sorted(turn_root.glob('*/metadata.json')):
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            if metadata.get('task_id') != task_id or metadata.get('chat_id') != chat_id:
                continue
            turn_id = metadata.get('usage_turn_id')
            summary = metadata.get('commit_summary')
            if not turn_id or turn_id in seen or not isinstance(summary, dict) or summary.get('source') != 'program':
                continue
            seen.add(turn_id)
            # Each current headless turn stores the cumulative business-task
            # ledger. Later snapshots replace earlier ones, never add to them.
            latest = summary
        if not seen:
            raise ValueError('该聊天中不存在指定任务的程序入库总结')
        assert latest is not None
        return deepcopy(latest) | {'task_id': task_id}
