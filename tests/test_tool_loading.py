"""Demand loading exposes schemas without changing authoritative permissions or data."""
import importlib
import json

import pytest
from agent_fixtures import project as project

from narrative import tool_api
from narrative.tool_loading import (
    CORE_TOOL_NAMES,
    bind_tool_loader,
    initial_tool_names,
    list_available_tools,
    load_tools,
    resolve_tool_names,
    tool_family,
)


def project_names():
    return [name for name, tool in tool_api.TOOLS.items() if tool.scope == 'project']


def test_small_initial_surface_has_discovery_but_not_screenplay_or_large_storyboard():
    names = initial_tool_names(project_names())
    assert len(CORE_TOOL_NAMES) <= 20 and len(names) == len(CORE_TOOL_NAMES)
    assert {'list_available_tools', 'load_tools'} <= set(names)
    assert 'read_screenplay' not in names
    assert not any('storyboard' in name for name in names)
    assert 'read_screenplay' in tool_api.TOOLS and 'apply_storyboard_changes' in tool_api.TOOLS


def test_default_discovery_is_family_manifest_not_full_schema_or_tool_table():
    result = list_available_tools()
    assert result['runtime_bound'] is False and 'tools' not in result
    assert any(group['family'] == 'screenplay' for group in result['families'])
    assert 'inputSchema' not in json.dumps(result)
    picked = list_available_tools(names=['read_screenplay'])
    assert [tool['name'] for tool in picked['tools']] == ['read_screenplay']
    assert picked['tools'][0]['family'] == 'screenplay'
    assert picked['tools'][0]['loaded'] is None
    assert 'inputSchema' not in json.dumps(picked)
    assert any(tool['name'] == 'read_screenplay' for tool in list_available_tools(query='read_screenplay')['tools'])


def test_bound_loading_returns_activation_not_duplicate_schema_and_is_idempotent():
    allowed = project_names()
    visible = initial_tool_names(allowed)
    calls = []
    def loader(requested):
        calls.append(requested)
        visible.extend(name for name in requested if name not in visible)
        return list(visible)
    with bind_tool_loader(loader, visible_names=lambda: visible, allowed_names=allowed):
        before = list_available_tools(names=['read_screenplay'])['tools'][0]
        assert before['loaded'] is False
        result = load_tools(names=['read_screenplay'])
        assert result['runtime_loaded'] is True
        assert result['newly_loaded_tools'] == ['read_screenplay']
        assert 'read_screenplay' in result['loaded_tools']
        assert 'inputSchema' not in json.dumps(result) and 'parameters' not in json.dumps(result)
        assert list_available_tools(names=['read_screenplay'])['tools'][0]['loaded'] is True
        again = load_tools(names=['read_screenplay'])
        assert again['newly_loaded_tools'] == []
        assert calls == [['read_screenplay']]
    assert list_available_tools()['runtime_bound'] is False


def test_standalone_cli_mcp_load_returns_exact_canonical_contract_without_runtime_claim():
    result = tool_api.TOOLS['load_tools'].handler(None, {'names': ['apply_storyboard_changes']})
    assert result['runtime_loaded'] is False and result['loaded_tools'] == []
    assert result['resolved_tools'] == ['apply_storyboard_changes']
    contract = result['tools'][0]
    assert contract['inputSchema'] == tool_api.TOOLS['apply_storyboard_changes'].schema()
    actions = contract['inputSchema']['properties']['changes']['items']['oneOf']
    keyframe = next(row for row in actions if row['properties']['op']['const'] == 'set_keyframe')
    assert keyframe['additionalProperties'] is False and 'offset_ms' in keyframe['required']
    assert 'keyframe' not in keyframe['properties']
    assert '无需激活' in result['message']


def test_family_resolution_and_name_requests_are_deduped_without_phase_gating():
    expected = [name for name in project_names() if tool_family(name) == 'screenplay']
    actual = resolve_tool_names(names=['read_screenplay', 'read_screenplay'], families=['screenplay'])
    assert actual == expected and actual.count('read_screenplay') == 1
    assert 'apply_screenplay_structure' in actual
    assert 'set_screenplay_timeline_property' in actual


def test_standard_cannot_load_host_or_configuration_and_full_preserves_scope():
    with pytest.raises(ValueError, match='权限'):
        resolve_tool_names(names=['run_host_command'])
    with pytest.raises(ValueError, match='权限'):
        resolve_tool_names(names=['configure_project_api_key'], access_mode='full')
    assert resolve_tool_names(names=['run_host_command'], access_mode='full') == ['run_host_command']
    with bind_tool_loader(lambda names: names, allowed_names=['read_screenplay'], access_mode='full'):
        with pytest.raises(ValueError, match='权限'):
            load_tools(names=['run_host_command'])
        with pytest.raises(ValueError):
            list_available_tools(names=['run_host_command'])


def test_invalid_requests_never_call_loader_and_do_not_partially_load():
    calls = []
    with bind_tool_loader(lambda names: calls.append(names) or names, allowed_names=project_names()):
        for request in ({}, {'names': ['read_screenplay', 'not-a-tool']}, {'families': ['not-a-family']}, {'names': 'read_screenplay'}):
            with pytest.raises(ValueError):
                load_tools(**request)
    assert calls == []


def test_loader_cannot_claim_unrequested_unauthorized_activation():
    with bind_tool_loader(lambda _names: ['read_screenplay', 'run_host_command'], allowed_names=project_names()):
        with pytest.raises(RuntimeError, match='已授权'):
            load_tools(names=['read_screenplay'])


def test_discovery_handlers_need_no_ops_and_registry_cycle_is_safe():
    module = importlib.import_module('narrative.tool_loading')
    assert module._registry() is tool_api.TOOLS
    result = tool_api.TOOLS['list_available_tools'].handler(None, {'family': 'screenplay'})
    assert any(tool['name'] == 'read_screenplay' for tool in result['tools'])
    assert not tool_api.TOOLS['load_tools'].writes
    assert not tool_api.TOOLS['list_available_tools'].writes


@pytest.mark.parametrize('granularity', ['fine', 'standard'])
def test_new_extraction_load_returns_current_custom_project_and_granularity_guides(project, granularity):
    from narrative.prompt_settings import prompt_snapshot
    settings = project.load_project_settings()
    project.save_project_settings(settings | {'extraction_granularity': granularity})
    for key, text in [('extraction', '本工程自定义提取指导'), ('granularity_'+granularity, '本工程粒度细则')]:
        project.save_prompt_override(key, text, project.load_prompt_settings()['revision'], '隔离指南测试')
    snapshot = prompt_snapshot(project)
    visible = initial_tool_names(project_names())
    def loader(requested):
        visible.extend(name for name in requested if name not in visible)
        return visible
    before = project.graph_project_info()['revision']
    with bind_tool_loader(loader, visible_names=lambda: visible, allowed_names=project_names()):
        loaded = tool_api.TOOLS['load_tools'].handler(project, {'names': ['submit_extraction', 'preview_extraction']})
        guidance = loaded['workflow_guidance']
        assert guidance['prompt_revision'] == snapshot['revision']
        assert guidance['extraction_granularity'] == granularity
        assert guidance['guides'] == [
            {'id': key, 'version': snapshot['versions'][key], 'text': snapshot['texts'][key]}
            for key in ['extraction', 'granularity_'+granularity]]
        assert not any('inputSchema' in tool for tool in loaded['tools'])
        again = tool_api.TOOLS['load_tools'].handler(project, {'names': ['submit_extraction']})
        assert again['newly_loaded_tools'] == [] and 'workflow_guidance' not in again
    assert project.graph_project_info()['revision'] == before


def test_guides_follow_exact_tools_not_reading_topics_or_other_workflows(project):
    from narrative.tool_loading import tool_guidance
    for key, text in [('review', '本工程复核约定'), ('event_merge_review', '本工程重复事件核对约定')]:
        project.save_prompt_override(key, text, project.load_prompt_settings()['revision'], '隔离指南测试')
    assert tool_guidance(project, ['read_screenplay', 'read_project_state', 'check_project', 'list_sources']) == {}
    review = tool_guidance(project, ['audit_properties'])
    assert [(row['id'], row['text']) for row in review['guides']] == [('review', '本工程复核约定')]
    merge = tool_guidance(project, ['preview_event_merge', 'merge_events'])
    assert [(row['id'], row['text']) for row in merge['guides']] == [('event_merge_review', '本工程重复事件核对约定')]
    assert 'extraction_granularity' not in review and 'extraction_granularity' not in merge


def test_normal_screenplay_load_has_no_extraction_guide_and_needs_no_project_read():
    class NoProjectRead:
        def __getattr__(self, _name):
            raise AssertionError('普通剧本工具加载不应读取提取指南')
    visible = initial_tool_names(project_names())
    def loader(requested):
        visible.extend(name for name in requested if name not in visible)
        return visible
    with bind_tool_loader(loader, visible_names=lambda: visible, allowed_names=project_names()):
        result = load_tools(names=['read_screenplay'], ops=NoProjectRead())
        assert 'workflow_guidance' not in result
        assert result['newly_loaded_tools'] == ['read_screenplay']


def test_tool_guidance_honors_frozen_task_prompt_snapshot(project):
    from narrative.prompt_settings import bind_prompt_snapshot, prompt_snapshot
    from narrative.tool_loading import tool_guidance
    project.save_prompt_override('review', '本任务捕获的复核约定', project.load_prompt_settings()['revision'], '保存初始约定')
    frozen = prompt_snapshot(project)
    project.save_prompt_override('review', '下一任务的新约定', project.load_prompt_settings()['revision'], '下一任务变更')
    with bind_prompt_snapshot(project, frozen):
        guide = tool_guidance(project, ['audit_properties'])
        assert guide['prompt_revision'] == frozen['revision']
        assert guide['guides'][0]['text'] == '本任务捕获的复核约定'
    assert tool_guidance(project, ['audit_properties'])['guides'][0]['text'] == '下一任务的新约定'


def test_standalone_extraction_load_returns_current_project_guides_and_exact_schema(project):
    result = tool_api.TOOLS['load_tools'].handler(project, {'names': ['submit_extraction']})
    assert result['runtime_loaded'] is False
    assert result['tools'][0]['inputSchema'] == tool_api.TOOLS['submit_extraction'].schema()
    assert [row['id'] for row in result['workflow_guidance']['guides']] == ['extraction', 'granularity_fine']
