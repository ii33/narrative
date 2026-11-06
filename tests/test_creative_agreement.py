"""One creative agreement authority, scoped inheritance and atomic edits."""
import json

import pytest
from test_screenplay_writing import body, table
from test_screenplay_writing import project as project

from narrative.screenplay_store import read_context
from narrative.screenplay_writing import validate_script_writing_tool_call


def test_unified_agreement_cli_write_and_mcp_read_share_effective_values(project, capsys):
    from narrative import mcp_server, tool_cli
    from narrative.project_package import layout
    ops, script, _ = project
    entry = str(layout(ops.project_info().path).entry)
    current = ops.load_creative_agreement(script)
    defaults = current['project_defaults'] | {'tone': '工程轻快', 'dialogue_style': '简短间接'}
    assert tool_cli.main(['creative-agreement-save', '--project', entry, '--json',
                         '--screenplay-id', script, '--expected-project-revision', str(current['project_revision']),
                         '--expected-script-revision', str(current['script_revision']),
                         '--project-defaults', json.dumps(defaults, ensure_ascii=False),
                         '--script-overrides', json.dumps({'tone': '本剧本克制', 'dialogue_style': ''}, ensure_ascii=False),
                         '--reason', '统一入口合同验证']) == 0
    saved = json.loads(capsys.readouterr().out)
    reply = mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                              'params': {'name': 'load_creative_agreement', 'arguments': {'screenplay_id': script}}},
                             default_project=entry)
    assert not reply['result']['isError']
    actual = json.loads(reply['result']['content'][0]['text'])
    assert saved['status'] == 'accepted'
    assert actual == ops.load_creative_agreement(script)
    assert {key: saved[key] for key in actual} == actual
    assert actual['effective']['tone'] == '本剧本克制' and actual['effective']['dialogue_style'] == ''


def save(ops, loaded, **patch):
    args = {'project_defaults': loaded['project_defaults'], 'expected_project_revision': loaded['project_revision'],
            'screenplay_id': loaded['screenplay_id'], 'expected_script_revision': loaded['script_revision'], 'reason': '测试创作约定'}
    return ops.save_creative_agreement(**(args | patch))


def test_legacy_preservation_structured_overrides_and_atomic_revisions(project):
    ops, script, _ = project
    ops.save_project_style('保留原工程长文本', 0)
    legacy = ops.create_screenplay('旧要求', creative_brief={'tone': '轻松', 'old_note': '保留旧剧本要求'}, reason='旧式要求')['screenplay']
    loaded = ops.load_creative_agreement(legacy['id'])
    assert loaded['project_defaults']['other'] == '保留原工程长文本'
    assert loaded['script_overrides'] == {'tone': '轻松', 'other': 'old_note：保留旧剧本要求'}
    updated = save(ops, loaded, project_defaults=loaded['project_defaults'] | {'tone': '工程基调'},
                   script_overrides={'tone': '', 'other': loaded['script_overrides']['other']}, title='新标题', language='en-US')
    assert updated['effective']['tone'] == '' and updated['effective_sources']['tone'] == 'script'
    assert updated['title'] == '新标题' and updated['language'] == 'en-US'
    assert ops.read_screenplay(legacy['id'])['screenplay']['creative_brief'] == legacy['creative_brief']
    before = ops.load_creative_agreement(legacy['id'])
    with pytest.raises(ValueError, match='剧本.*已变化'):
        ops.save_creative_agreement(before['project_defaults'] | {'tone': '不得半保存'}, before['project_revision'],
                                    '过期', legacy['id'], {}, loaded['script_revision'])
    assert ops.load_creative_agreement(legacy['id']) == before
    inherited = save(ops, before, script_overrides={'tone': None})
    assert inherited['script_overrides'] == {} and inherited['effective']['tone'] == '工程基调'
    assert inherited['effective']['other'] == '保留原工程长文本'
    invalid = save(ops, inherited, script_overrides={'unknown': '错误'})
    assert invalid['status'] == 'staged' and invalid['problems']
    persisted = ops.load_creative_agreement(legacy['id'])
    assert {key: inherited[key] for key in persisted} == persisted
    # Another script was never rewritten by editing this script's agreement.
    assert ops.read_screenplay(script)['screenplay']['revision'] == 1


def test_effective_agreement_context_basis_and_cross_script_isolation(project):
    ops, script, segment = project
    unit = body(ops, table(ops, segment)['units'][0]['id'])
    before = ops.read_script_unit_context(unit['id'])
    frozen = ops.prepare_script_writing_task({'kind': 'unit', 'ids': [unit['id']], 'screenplay_id': script})
    other = ops.create_screenplay('另一剧本', reason='隔离')['screenplay']
    loaded_other = ops.load_creative_agreement(other['id'])
    save(ops, loaded_other, script_overrides={'tone': '仅另一剧本'})
    assert ops.read_script_unit_context(unit['id'])['context_fingerprint'] == before['context_fingerprint']
    validate_script_writing_tool_call(frozen, 'submit_script_unit', {'unit_id': unit['id']}, ops)
    own = ops.load_creative_agreement(script)
    save(ops, own, script_overrides={'tone': '本剧本冷静', 'dialogue_style': '简洁', 'other': ''})
    current = ops.read_script_unit_context(unit['id'])
    assert current['creative_agreement']['fields']['tone'] == '本剧本冷静'
    assert current['reference_context']['fingerprint_version'] == 4
    assert current['unit']['basis_state'] == 'changed'
    assert current['context_fingerprint'] != before['context_fingerprint']
    with pytest.raises(ValueError, match='项目风格约定已变化'):
        validate_script_writing_tool_call(frozen, 'submit_script_unit', {'unit_id': unit['id']}, ops)
    body(ops, unit['id'], context=current)
    basis = list(read_context(ops)[0]['script_writing_bases'].values())[-1]
    assert basis['creative_agreement']['fields']['tone'] == '本剧本冷静'
    assert basis['fingerprint_version'] == 4


def test_retired_production_target_is_rejected(project):
    ops, script, segment = project
    unit = table(ops, segment)['units'][0]
    with pytest.raises(ValueError, match="目标kind"):
        ops.prepare_script_writing_task({"kind": "production", "screenplay_id": script, "ids": [unit["id"]]})


def test_storyboard_conversation_can_only_edit_selected_scene(project):
    ops, script, segment = project
    first, second = table(ops, segment)['units']
    task = ops.prepare_script_writing_task({'kind': 'storyboard', 'screenplay_id': script, 'ids': [first['id']]})
    assert task['unit_ids'] == [first['id']]
    assert 'read_storyboard' in task['instruction']
    validate_script_writing_tool_call(task, 'apply_storyboard_changes', {'unit_id': first['id']}, ops)
    for tool, unit_id in [('apply_storyboard_changes', second['id']), ('submit_script_unit', first['id'])]:
        with pytest.raises(ValueError, match='分镜任务'):
            validate_script_writing_tool_call(task, tool, {'unit_id': unit_id}, ops)
