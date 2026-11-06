"""Explicit business content catalog; commands and external side effects stay immediate."""
import inspect
from dataclasses import dataclass, replace
from functools import wraps


@dataclass(frozen=True)
class ContentOperationSpec:
    editable: tuple[str, ...]
    targets: tuple[str, ...] = ()
    actions_field: str | None = None
    allowed_actions: frozenset[str] = frozenset()
    medium: str = 'graph'


CONTENT_OPERATIONS = {
    'submit_extraction': ContentOperationSpec(('result',), ('source_id', 'start', 'end'), medium='extraction'),
    'create_source_slices': ContentOperationSpec(('slices',), ('source_id',)),
    'set_property_categories': ContentOperationSpec(('config',)),
    'property_categorize': ContentOperationSpec(('category', 'note'), ('targets',)),
    'apply_changes': ContentOperationSpec(('graph_objects', 'property_records')),
    'record_property_change': ContentOperationSpec(('property_id', 'op', 'value', 'value_type', 'cardinality', 'symmetric', 'scope', 'holder_id', 'context_id', 'quote', 'source_id', 'category', 'source', 'related_nodes'), ('node_id', 'event_id')),
    'correct_property': ContentOperationSpec(('changes',), ('record_id',)),
    'edit_adaptation_node': ContentOperationSpec(('changes',), ('node_id', 'parent_id', 'after_id')),
    'set_adaptation_property': ContentOperationSpec(('property_id', 'op', 'value', 'scope', 'holder_id', 'context_id'), ('node_id', 'event_id', 'record_id')),
    'edit_screenplay_timeline_node': ContentOperationSpec(('changes',), ('screenplay_id', 'node_id', 'parent_id', 'after_id')),
    'set_screenplay_timeline_property': ContentOperationSpec(('property_id', 'op', 'value', 'scope', 'holder_id', 'context_id'), ('screenplay_id', 'node_id', 'event_id', 'record_id')),
    'create_screenplay': ContentOperationSpec(('title', 'format', 'language', 'creative_brief')),
    'create_adaptation_span': ContentOperationSpec(('name', 'start_node_id', 'end_node_id', 'selection_ids'), ('screenplay_id',)),
    'update_adaptation_span': ContentOperationSpec(('changes',), ('span_id',)),
    'create_script_segment': ContentOperationSpec(('title', 'brief', 'role', 'target_duration_seconds'), ('screenplay_id', 'span_id', 'parent_kind', 'parent_id', 'after_entry_id')),
    'update_script_segment': ContentOperationSpec(('changes',), ('segment_id',)),
    'submit_scene_table': ContentOperationSpec(('units',), ('segment_id',)),
    'submit_script_unit': ContentOperationSpec(('blocks', 'entity_usages'), ('unit_id',)),
    'apply_scene_table_changes': ContentOperationSpec(('changes',), ('segment_id',), 'changes', frozenset({'insert', 'update', 'update_plan'})),
    'apply_script_block_changes': ContentOperationSpec(('changes',), ('unit_id', 'target_block_ids'), 'changes', frozenset({'insert', 'update'})),
    'apply_screenplay_structure': ContentOperationSpec(('actions',), ('screenplay_id',), 'actions', frozenset({'create_episode', 'create_act', 'update_episode', 'update_act', 'update_screenplay'})),
    'apply_storyboard_changes': ContentOperationSpec(('changes',), ('unit_id',), 'changes', frozenset({'create_shot', 'update_shot', 'create_object', 'update_object', 'set_action_keyframe', 'create_marker', 'update_marker', 'bind_keyframe_marker', 'bind_action_keyframe_marker', 'set_camera_keyframe', 'set_camera_overlays', 'set_camera_preset', 'create_sound_intent', 'update_sound_intent', 'create_panel', 'update_panel', 'set_fps'})),
    'save_asset': ContentOperationSpec(('payload',), ('payload.id', 'payload.node_id', 'payload.category', 'payload.domain'), medium='asset'),
    'save_storyboard_camera_preset': ContentOperationSpec(('name', 'settings')),
    'set_storyboard_animation': ContentOperationSpec(('mode', 'layers'), ('unit_id', 'shot_id')),
    'save_compile_settings': ContentOperationSpec(('settings',)),
    'save_prompt_override': ContentOperationSpec(('text',), ('prompt_id',)),
    'save_project_style': ContentOperationSpec(('text',)),
    'save_creative_agreement': ContentOperationSpec(('project_defaults', 'script_overrides', 'title', 'language'), ('screenplay_id',)),
    'material_add_card': ContentOperationSpec(('title', 'detail', 'valid_from', 'valid_to', 'images'), ('node_id',), medium='material'),
    'material_update_card': ContentOperationSpec(('title', 'detail', 'valid_from', 'valid_to', 'images'), ('node_id', 'card_id'), medium='material'),
    'material_update_image_caption': ContentOperationSpec(('caption',), ('node_id', 'card_id', 'image_id'), medium='material'),
}


def eligible(operation, arguments, ops=None):
    spec = CONTENT_OPERATIONS.get(operation)
    if spec is None:
        return False
    if spec.actions_field:
        changes = arguments.get(spec.actions_field)
        if not isinstance(changes, list) or not changes or any(not isinstance(c, dict) or c.get('op') not in spec.allowed_actions for c in changes):
            return False
    if operation in {'edit_adaptation_node', 'edit_screenplay_timeline_node'} and arguments.get('node_id') and any(arguments.get(k) is not None for k in ('after_id', 'parent_id')):
        return False
    if operation.startswith('material_') and arguments.get('images') is not None:
        images = arguments['images']
        if not isinstance(images, list):
            return False
        if operation == 'material_add_card':
            return images == []
        if ops is None:
            return all(isinstance(image, dict) and image.get('id') for image in images) or images == []
        view = ops.material_view(arguments['node_id'])
        card = next((card for card in [*view.get('cards', []), *view.get('unpositioned_cards', [])] if card['id'] == arguments.get('card_id')), None)
        if card is None:
            from .node_material import MaterialStore
            card = next((card for card in MaterialStore(ops._store.path).load(arguments['node_id'])['cards'] if card['id'] == arguments.get('card_id')), None)
        if card is None:
            return False
        old_ids = [image['id'] for image in card.get('images', [])]
        new_ids = [image.get('id') for image in images if isinstance(image, dict)]
        return len(new_ids) == len(images) and len(set(new_ids)) == len(new_ids) and set(new_ids) == set(old_ids)
    return True


def catalog_view():
    return {'operations': [{'operation': name, 'editable_roots': list(spec.editable), 'target_fields': list(spec.targets),
        'actions_field': spec.actions_field, 'allowed_actions': sorted(spec.allowed_actions), 'medium': spec.medium}
        for name, spec in CONTENT_OPERATIONS.items()],
        'immediate': ['delete', 'move', 'copy', 'reset', 'approve', 'adopt', 'file_import', 'render', 'export', 'host_command', 'credentials'],
        'rules': '内容按明确目录暂存；混合删除移动等命令保持即时原子执行。权限与并发冲突不转为待修内容。'}


def bind_arguments(method, self, args, kwargs):
    signature = inspect.signature(method)
    bound = signature.bind(self, *args, **kwargs)
    bound.apply_defaults()
    arguments = dict(bound.arguments)
    arguments.pop('self', None)
    for key, parameter in signature.parameters.items():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            arguments.update(arguments.pop(key, {}))
    return arguments


def install_content_operations(cls):
    for operation in CONTENT_OPERATIONS:
        if operation == 'submit_extraction':
            continue  # Existing extraction adapter owns its atomic staging lifecycle.
        original = getattr(cls, operation, None)
        if original is None or getattr(original, '_content_wrapped', False):
            continue
        if 'reason' in inspect.signature(original).parameters and 'reason' not in CONTENT_OPERATIONS[operation].editable:
            CONTENT_OPERATIONS[operation] = replace(CONTENT_OPERATIONS[operation], editable=(*CONTENT_OPERATIONS[operation].editable, 'reason'))
        def factory(name, method):
            @wraps(method)
            def wrapped(self, *args, **kwargs):
                from .business_staging import invoke, nested
                if nested(self):
                    return method(self, *args, **kwargs)
                arguments = bind_arguments(method, self, args, kwargs)
                if not eligible(name, arguments, self):
                    return method(self, *args, **kwargs)
                return invoke(self, name, arguments, lambda candidate: method(self, **candidate))
            wrapped.__dict__['_content_wrapped'] = True
            return wrapped
        setattr(cls, operation, factory(operation, original))


def target_value(arguments, field):
    value = arguments
    for part in field.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def validate_protected_arguments(before, after, spec):
    """Retain identities/route fields of existing rows, even for root replacement."""
    from .content_errors import ContentGuardError
    route_keys = {'id', 'unit_id', 'block_id', 'shot_id', 'object_id', 'marker_id', 'panel_id', 'sound_id', 'keyframe_id', 'record_id', 'node_id', 'event_id', 'existing_id', 'path', 'file'}
    stable_arrays = {'nodes', 'properties', 'graph_objects', 'property_records', 'changes', 'actions', 'blocks', 'units', 'images', 'reference_images', 'slices'}
    def protected_keys(path):
        if len(path) == 2 and path[0] == 'graph_objects':
            return {'id'}
        if len(path) == 2 and path[0] == 'property_records':
            return {'id', 'node_id', 'event_id'}
        if len(path) == 2 and path[0] in {'blocks', 'units', 'nodes'}:
            return {'id', 'existing_id'}
        if len(path) == 2 and path[0] in {'changes', 'actions'}:
            return route_keys
        if path and isinstance(path[-1], int) and len(path) >= 2 and path[-2] in {'images', 'reference_images'}:
            return {'id', 'path', 'file'}
        if len(path) == 3 and path[0] in {'changes', 'actions'} and path[2] in {'block', 'unit'}:
            return {'id'}
        return set()
    def visit(old, new, path):
        if isinstance(old, dict):
            if not isinstance(new, dict):
                if any(key in protected_keys(path) for key in old):
                    raise ContentGuardError('不能替换已捕获的业务对象身份')
                return
            for key in protected_keys(path):
                if old.get(key) != new.get(key) or (key in old) != (key in new):
                    raise ContentGuardError('不能通过修补更换业务对象身份或路由')
            if path and path[0] == spec.actions_field and 'op' in old and old.get('op') != new.get('op'):
                raise ContentGuardError('不能通过修补改变已有动作类型')
            for key, value in old.items():
                if key in new:
                    visit(value, new[key], [*path, key])
        elif isinstance(old, list):
            stable = bool(path and path[-1] in stable_arrays)
            if stable:
                if not isinstance(new, list) or len(new) < len(old):
                    raise ContentGuardError('不能删除或重排已有对象数组项')
                for index, value in enumerate(old):
                    visit(value, new[index], [*path, index])
    visit(before, after, [])
    for field in spec.targets:
        if target_value(before, field) != target_value(after, field):
            raise ContentGuardError('不能修改固定业务目标')


def preflight(ops, operation, arguments):
    """Freshness/immutable targets precede authored payload validation."""
    from .content_errors import ContentConflictError, ContentGuardError
    loaders = {'save_compile_settings': ops.load_compile_settings,
               'save_prompt_override': ops.load_prompt_settings,
               'save_project_style': ops.load_project_style}
    if operation in loaders:
        revision = loaders[operation]()['revision']
        if type(arguments.get('expected_revision')) is not int or arguments['expected_revision'] != revision:
            raise ContentConflictError('设置已变化，请重新读取当前修订')
    if operation == 'save_creative_agreement':
        current = ops.load_creative_agreement(arguments.get('screenplay_id'))
        if type(arguments.get('expected_project_revision')) is not int or arguments['expected_project_revision'] != current['project_revision']:
            raise ContentConflictError('工程创作约定已变化，请重新读取')
        if arguments.get('screenplay_id') is not None and (type(arguments.get('expected_script_revision')) is not int or arguments['expected_script_revision'] != current['script_revision']):
            raise ContentConflictError('剧本创作约定已变化，请重新读取')
    if operation in {'edit_adaptation_node', 'set_adaptation_property'}:
        current = ops.adaptation_snapshot()
        expected = arguments.get('expected_revision')
        if expected is not None and (type(expected) is not int or expected != current['revision']):
            raise ContentConflictError('改编修订已变化，请刷新后重试')
        identifier = arguments.get('node_id')
        if identifier is not None and not any(row['id'] == identifier for row in current['nodes']):
            raise ContentGuardError('改编目标不存在')
    if operation in {'apply_changes', 'record_property_change'}:
        graph = ops.graph_snapshot()
        if type(arguments.get('expected_revision')) is not int or arguments['expected_revision'] != graph['revision']:
            return  # Preserve this operation's structured revision rejection.
        nodes = {node['id']: node for node in graph['nodes']}
        for node in arguments.get('graph_objects', []):
            if isinstance(node, dict) and node.get('kind') == 'node' and isinstance(node.get('id'), str) and node.get('node_type') in {'person', 'group', 'item', 'location', 'scene', 'event', 'action', 'big_event', 'storyline'}:
                nodes[node['id']] = node
        if operation == 'record_property_change':
            fixed = [(arguments.get('node_id'), arguments.get('event_id'))]
        else:
            fixed = [(row.get('node_id'), row.get('event_id')) for row in arguments.get('property_records', []) if isinstance(row, dict) and row.get('kind') == 'property_change']
        for owner, event in fixed:
            if not isinstance(owner, str) or owner not in nodes:
                raise ContentGuardError('固定属性主体不存在，不能暂存后更换目标；请明确目标重新提交')
            if not isinstance(event, str) or nodes.get(event, {}).get('node_type') not in {'event', 'action'}:
                raise ContentGuardError('固定变更事件不存在，不能暂存后更换目标；请明确事件重新提交')
    if operation == 'property_categorize':
        valid = {row['id'] for row in ops.property_records() if row.get('kind') == 'property_change'}
        if not arguments.get('targets') or any(identifier not in valid for identifier in arguments['targets']):
            raise ContentGuardError('请选择存在的属性变更记录')
    if operation == 'create_source_slices':
        try:
            ops.source(arguments['source_id'])
        except ValueError as exc:
            raise ContentGuardError(str(exc)) from exc
    if operation == 'correct_property':
        records = {r['id']: r for r in ops.property_records()}
        if arguments.get('record_id') not in records:
            raise ContentGuardError('更正记录不存在')
        changes = arguments.get('changes')
        if isinstance(changes, dict) and 'event_id' in changes:
            moments = {n['id'] for n in ops.graph_snapshot()['nodes'] if n.get('node_type') in {'event', 'action'}}
            if not isinstance(changes['event_id'], str) or changes['event_id'] not in moments:
                raise ContentGuardError('更正的变更事件不存在')
        if type(arguments.get('expected_revision')) is not int or arguments['expected_revision'] != ops.graph_project_info()['revision']:
            raise ContentConflictError('正式资料版本已变化，请刷新')


def capture_basis(ops, operation, arguments):
    if operation in {'edit_adaptation_node', 'set_adaptation_property'} and arguments.get('expected_revision') is None:
        from .adaptation_operations import _KEY
        with ops._store.connect() as db:
            row = db.execute("SELECT json_extract(value,'$.revision') FROM meta WHERE key=?", (_KEY,)).fetchone()
        return {'adaptation_revision': row[0] if row else 0}
    if operation == 'save_asset' and arguments.get('expected_revision') is None:
        with ops._asset_db() as db:
            return {'asset_revision': db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]}
    if operation == 'set_property_categories':
        return ops.property_categories()
    if operation == 'property_categorize':
        ids = arguments.get('targets') or []
        return [row for row in ops.property_records() if row['id'] in ids]
    return None


def operation_target(operation, arguments):
    spec = CONTENT_OPERATIONS[operation]
    if operation == 'apply_changes':
        ids: set[str] = {row['id'] for row in arguments.get('graph_objects', []) if isinstance(row, dict) and row.get('kind') == 'node' and isinstance(row.get('id'), str)}
        ids.update(row['node_id'] for row in arguments.get('property_records', []) if isinstance(row, dict) and row.get('kind') == 'property_change' and isinstance(row.get('node_id'), str))
        return {'node_id': next(iter(ids))} if len(ids) == 1 else {'node_ids': sorted(ids)}
    return {k: target_value(arguments, k) for k in spec.targets if target_value(arguments, k) is not None}


def execution_arguments(ops, operation, arguments):
    from copy import deepcopy

    from .tool_execution_context import current_tool_execution_metadata
    candidate = deepcopy(arguments)
    parameters = inspect.signature(getattr(ops, operation)).parameters
    metadata = current_tool_execution_metadata()
    for key in ('task_id', 'manifest_ref'):
        if key in parameters:
            candidate[key] = metadata.get(key)
    return candidate
