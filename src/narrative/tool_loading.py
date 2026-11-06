"""Tool schema loading is exposure state, never a second permission system."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

TOOL_LOADING_GUIDE = (
    '工具按需加载：常驻目录不是全部能力或阶段权限白名单。需要其他业务工具时，先用list_available_tools按名称、业务family或关键词发现，'
    '再用load_tools的names或families加载本次确需的工具；成功后精确参数schema会出现在下一次模型请求的tools中，继续同一任务调用。'
    '已经加载的工具无需重复加载，不要在工作前预先加载全部工具；读取合同和加载都不执行业务写入。现有访问模式、目标权限和Operations校验仍生效。'
)

CORE_TOOL_NAMES = (
    'list_available_tools', 'load_tools', 'project_info', 'list_nodes', 'get_node', 'search',
    'list_sources', 'read_source', 'read_source_slice', 'list_source_slices', 'get_schema',
    'list_content_submissions', 'read_content_submission', 'patch_content_submission',
    'list_extraction_submissions', 'read_extraction_submission', 'patch_extraction_submission',
    'read_task_write_summary', 'read_usage_statistics',
)

FAMILY_DESCRIPTIONS = {
    'project': '工程信息、结构状态和检查',
    'sources': '原文、切片、定位与导入',
    'extraction': '原文提取与提取草稿局部修补',
    'graph': '节点、包含结构和通用图内容修改',
    'properties': '事件属性、人物关系、分类和纠错',
    'materials': '原作节点资料卡与图片',
    'adaptation': '独立改编及其节点、时点和属性',
    'assets': '形象、物品、地点和材质设定',
    'screenplay': '剧本结构、独立时间线和范围',
    'writing': '场表、正文与写作上下文',
    'storyboard': '分镜、共享调度、摄影机、画格、媒体与预演',
    'settings': '创作约定、提示词、粒度和编译设置',
    'output': '剧本编译与导出',
    'submissions': '统一内容暂存和局部修补',
    'activity': '程序入库总结和模型用量',
    'attachments': '当前聊天附件元数据和内容读取',
    'host': '完全访问模式的工程外文件与系统命令',
    'discovery': '发现和按需加载工具合同',
    'other': '其他当前工程业务工具',
}


@dataclass(frozen=True)
class _Binding:
    loader: Callable[[list[str]], Iterable[str]]
    visible_names: Callable[[], Iterable[str]] | None
    allowed_names: frozenset[str] | None
    access_mode: str


_BINDING: ContextVar[_Binding | None] = ContextVar('tool_loading_binding', default=None)


def _registry():
    # Imported only on use, after tool_api has finished registering its tools.
    from .tool_api import TOOLS
    return TOOLS


def tool_family(name: str) -> str:
    if name in {'list_available_tools', 'load_tools'}:
        return 'discovery'
    if name in {'read_task_write_summary', 'read_usage_statistics'}:
        return 'activity'
    if name.startswith(('list_chat_attachment', 'read_chat_attachment')):
        return 'attachments'
    if name in {'read_host_file', 'write_host_file', 'list_host_directory', 'run_host_command', 'read_host_command', 'stop_host_command'}:
        return 'host'
    if 'storyboard' in name:
        return 'storyboard'
    if name in {'compile_screenplay', 'export_screenplay'}:
        return 'output'
    if name in {'list_assets', 'get_asset', 'save_asset', 'delete_asset', 'asset_moments'}:
        return 'assets'
    if name in {'load_project_style', 'save_project_style', 'load_prompt_settings', 'save_prompt_override',
                'load_creative_agreement', 'save_creative_agreement', 'load_compile_settings', 'save_compile_settings',
                'get_extraction_settings', 'set_extraction_granularity'}:
        return 'settings'
    if name.endswith('content_submissions') or name.endswith('content_submission'):
        return 'submissions'
    if name in {'preview_extraction', 'submit_extraction', 'list_extraction_submissions', 'read_extraction_submission', 'patch_extraction_submission'}:
        return 'extraction'
    if name in {'read_source', 'list_sources', 'locate_source', 'import_source', 'list_source_slices',
                'read_source_slice', 'create_source_slices', 'preview_pattern_slices', 'create_pattern_slices'}:
        return 'sources'
    if 'material' in name:
        return 'materials'
    if name in {'record_property_change', 'correct_property', 'delete_property_records', 'property_categories', 'node_properties',
                'audit_properties', 'relation_map', 'merge_properties', 'rename_property', 'categorize_records', 'scene_state', 'event_state'}:
        return 'properties'
    if name.startswith(('adaptation_', 'edit_adaptation_', 'copy_adaptation_', 'move_adaptation_', 'delete_adaptation_',
                        'suppress_adaptation_', 'set_adaptation_', 'check_adaptation', 'sync_adaptation', 'import_adaptation')):
        return 'adaptation'
    if name in {'submit_scene_table', 'apply_scene_table_changes', 'adopt_scene_table', 'read_scene_table_context', 'read_script_unit_context',
                'submit_script_unit', 'apply_script_block_changes', 'mark_writing_basis_current', 'prepare_script_writing_targets',
                'read_script_writing_targets', 'prepare_script_writing_task'}:
        return 'writing'
    if 'screenplay' in name or 'script_segment' in name or 'adaptation_span' in name:
        return 'screenplay'
    if name in {'project_info', 'list_projects', 'duplicate_project', 'check_project', 'statistics', 'read_project_state'}:
        return 'project'
    if name in {'list_nodes', 'get_node', 'search', 'list_moments', 'revise_node', 'attach_children', 'preview_event_merge',
                'merge_events', 'preview_changes', 'apply_changes', 'rollback_preview', 'rollback_to', 'get_schema'}:
        return 'graph'
    return 'other'


def initial_tool_names(available_names: Iterable[str]) -> list[str]:
    available = set(available_names)
    return [name for name in CORE_TOOL_NAMES if name in available]


@contextmanager
def bind_tool_loader(loader: Callable[[list[str]], Iterable[str]], *, visible_names: Callable[[], Iterable[str]] | None = None,
                     allowed_names: Iterable[str] | None = None, access_mode: str = 'standard'):
    if access_mode not in {'standard', 'full'} or not callable(loader) or visible_names is not None and not callable(visible_names):
        raise ValueError('无效的运行时工具加载绑定')
    token = _BINDING.set(_Binding(loader, visible_names, frozenset(allowed_names) if allowed_names is not None else None, access_mode))
    try:
        yield
    finally:
        _BINDING.reset(token)


def _allowed(registry, access_mode, allowed_names):
    if access_mode not in {'standard', 'full'}:
        raise ValueError('访问模式须为standard或full')
    scopes = {'project', 'host', 'workspace'} if access_mode == 'full' else {'project'}
    selected = set(allowed_names) if allowed_names is not None else None
    return {name for name, tool in registry.items() if tool.scope in scopes and (selected is None or name in selected)}


def resolve_tool_names(names=None, families=None, *, allowed_names=None, access_mode='standard', registry=None) -> list[str]:
    registry = _registry() if registry is None else registry
    if names is not None and (not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names)):
        raise ValueError('names须为非空工具名组成的数组')
    if families is not None and (not isinstance(families, list) or any(not isinstance(family, str) or not family for family in families)):
        raise ValueError('families须为业务组名数组')
    if not names and not families:
        raise ValueError('请指定本次需要的names或families；发现工具可先调用list_available_tools')
    allowed = _allowed(registry, access_mode, allowed_names)
    unknown = set(names or []) - set(registry)
    if unknown:
        raise ValueError('工具不存在：' + '、'.join(sorted(unknown)))
    denied = set(names or []) - allowed
    if denied:
        raise ValueError('当前访问权限未开放这些工具：' + '、'.join(sorted(denied)))
    known_families = {tool_family(name) for name in allowed}
    unknown_families = set(families or []) - known_families
    if unknown_families:
        raise ValueError('当前可用业务组不存在：' + '、'.join(sorted(unknown_families)))
    selected = set(names or []) | {name for name in allowed if tool_family(name) in set(families or [])}
    return [name for name in registry if name in selected]


def _manifest(registry, name, visible=None):
    tool = registry[name]
    return {'name': name, 'summary': tool.summary, 'family': tool_family(name), 'writes': tool.writes,
            'loaded': name in visible if visible is not None else None}


def list_available_tools(names=None, family=None, query=None) -> dict[str, Any]:
    registry = _registry()
    binding = _BINDING.get()
    allowed = _allowed(registry, binding.access_mode if binding else 'standard', binding.allowed_names if binding else None)
    visible = set(binding.visible_names()) if binding and binding.visible_names else None
    if names is not None and (not isinstance(names, list) or any(not isinstance(name, str) for name in names)):
        raise ValueError('names须为工具名数组')
    if family is not None and (not isinstance(family, str) or family not in FAMILY_DESCRIPTIONS):
        raise ValueError('family不存在，请先读取默认业务组目录')
    if query is not None and not isinstance(query, str):
        raise ValueError('query须为文本')
    groups = [{'family': name, 'summary': description, 'count': sum(tool_family(tool) == name for tool in allowed)}
              for name, description in FAMILY_DESCRIPTIONS.items() if any(tool_family(tool) == name for tool in allowed)]
    result: dict[str, Any] = {'runtime_bound': binding is not None, 'families': groups}
    if names is None and family is None and not query:
        return result
    if names is not None and set(names) - allowed:
        raise ValueError('查询包含当前不可用或不存在的工具')
    needle = (query or '').casefold().strip()
    picked = [name for name in registry if name in allowed and (names is None or name in names)
              and (family is None or tool_family(name) == family)
              and (not needle or needle in (name + ' ' + registry[name].summary).casefold())]
    result['tools'] = [_manifest(registry, name, visible) for name in picked]
    return result


_GUIDE_TOOLS = {
    'submit_extraction': ('extraction',),
    'preview_extraction': ('extraction',),
    'audit_properties': ('review',),
    'preview_event_merge': ('event_merge_review',),
    'merge_events': ('event_merge_review',),
}


def tool_guidance(ops, names) -> dict[str, Any]:
    """Return effective project guides selected only by exact capability names."""
    if isinstance(names, str):
        raise ValueError('names须为工具名集合')
    requested = set(names)
    keys = []
    for name, guides in _GUIDE_TOOLS.items():
        if name in requested:
            keys.extend(key for key in guides if key not in keys)
    if not keys:
        return {}
    if ops is None:
        raise ValueError('工作流指南需要当前工程Operations，不能用全局默认冒充工程自定义')
    from .prompt_settings import prompt_snapshot
    snapshot = prompt_snapshot(ops)
    result: dict[str, Any] = {'prompt_revision': snapshot['revision']}
    if 'extraction' in keys:
        granularity = ops.load_project_settings()['extraction_granularity']
        if granularity not in {'standard', 'fine'}:
            raise ValueError('当前工程提取粒度无效')
        keys.insert(keys.index('extraction')+1, 'granularity_'+granularity)
        result['extraction_granularity'] = granularity
    result['guides'] = [{'id': key, 'version': snapshot['versions'][key], 'text': snapshot['texts'][key]} for key in keys]
    return result


def load_tools(names=None, families=None, *, ops=None) -> dict[str, Any]:
    registry = _registry()
    binding = _BINDING.get()
    selected = resolve_tool_names(names, families, allowed_names=binding.allowed_names if binding else None,
                                  access_mode=binding.access_mode if binding else 'standard', registry=registry)
    if binding is None:
        guidance = tool_guidance(ops, selected)
        return {**({'workflow_guidance': guidance} if guidance else {}), 'runtime_loaded': False, 'resolved_tools': selected, 'loaded_tools': [],
                'message': '当前未绑定助手运行时；返回精确合同。CLI/MCP服务器保留完整工具注册，无需激活，未改变任何业务数据。',
                'tools': [{'name': name, 'description': registry[name].summary, 'inputSchema': registry[name].schema()} for name in selected]}
    before = set(binding.visible_names()) if binding.visible_names else set()
    guidance = tool_guidance(ops, [name for name in selected if name not in before])
    loaded = before if set(selected) <= before else set(binding.loader(selected))
    allowed = _allowed(registry, binding.access_mode, binding.allowed_names)
    if not set(selected) <= loaded or not loaded <= allowed:
        raise RuntimeError('运行时未确认精确的已授权工具加载结果')
    return {**({'workflow_guidance': guidance} if guidance else {}), 'runtime_loaded': True, 'loaded_tools': [name for name in registry if name in loaded],
            'newly_loaded_tools': [name for name in registry if name in loaded-before],
            'tools': [_manifest(registry, name, loaded) for name in selected],
            'message': '工具已在同一运行时激活，精确参数schema随下一次模型请求tools提供；不必再次加载。'}


def register(Tool, register_tool):
    register_tool(Tool('list_available_tools', '发现当前访问权限内的全部业务能力。默认仅业务组摘要；按names/family/query筛选时返回简明工具说明，不返回全部schema。不执行业务操作。', {
        'names': {'type': 'array', 'items': {'type': 'string'}},
        'family': {'type': 'string', 'enum': list(FAMILY_DESCRIPTIONS)},
        'query': {'type': 'string'},
    }, lambda _ops, args: list_available_tools(**args)))
    register_tool(Tool('load_tools', '按工具名称或业务families按需加载精确参数合同；保持全部授权能力可发现。同一运行时下一请求提供schema，已加载无需重复；不执行业务写入。', {
        'names': {'type': 'array', 'items': {'type': 'string'}, 'description': '实际工具名，可一次加载多个'},
        'families': {'type': 'array', 'items': {'type': 'string', 'enum': list(FAMILY_DESCRIPTIONS)}, 'description': '需要的业务组，组仅组织发现不限制权限'},
    }, lambda ops, args: load_tools(**args, ops=ops)))
