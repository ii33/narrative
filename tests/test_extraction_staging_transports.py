"""Staged repair is the same atomic Operations workflow through tools/CLI/MCP."""
import json

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project

from narrative import assistant, mcp_server, tool_api, tool_cli
from narrative.project_package import layout
from narrative.prompt_defaults import DEFAULT_PROMPTS


def stage_with_unknown_field(project):
    sid = project.import_source('局部修补工具原文', TEXT)
    draft = draft_result(ended=False)
    draft['nodes'][0]['unexpected'] = True
    entry = str(layout(project.project_info().path).entry)
    result = tool_api.call('submit_extraction', {'source_id': sid, 'result': draft}, project=entry)
    assert result['status'] == 'staged' and result['problems']
    assert not project.graph_snapshot()['nodes']
    return entry, result


def test_tool_feedback_can_be_patched_directly_via_cli_without_resubmitting(project, capsys):
    entry, staged = stage_with_unknown_field(project)
    patch = [{'op': 'remove', 'section': 'nodes', 'index': 0, 'field': 'unexpected'}]
    assert tool_cli.main(['extraction-patch', '--submission-id', staged['submission_id'],
        '--expected-revision', str(staged['submission_revision']), '--patches', json.dumps(patch),
        '--reason', '根据问题反馈直接删除非法字段', '--project', entry, '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'accepted' and result['submission_status'] == 'committed'
    assert result['node_ids']['@1'] in {n['id'] for n in project.graph_snapshot()['nodes']}
    assert tool_cli.main(['extraction-submission', '--submission-id', staged['submission_id'], '--project', entry, '--json']) == 0
    read = json.loads(capsys.readouterr().out)
    assert read['submission_status'] == 'committed' and 'entries' not in read and 'result' not in read


def test_mcp_reads_only_requested_slots_and_rejects_patch_of_committed_draft(project):
    entry, staged = stage_with_unknown_field(project)
    response = mcp_server.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'read_extraction_submission',
        'arguments': {'submission_id': staged['submission_id'], 'section': 'nodes', 'indices': [0]}}}, default_project=entry)
    assert not response['result']['isError']
    read = json.loads(response['result']['content'][0]['text'])
    assert len(read['entries']) == 1 and read['entries'][0]['reference'] == '@1'
    fixed = tool_api.call('patch_extraction_submission', {'submission_id': staged['submission_id'],
        'expected_revision': staged['submission_revision'], 'patches': [{'op': 'remove', 'section': 'nodes', 'index': 0, 'field': 'unexpected'}],
        'reason': '局部修补'}, project=entry)
    assert fixed['status'] == 'accepted'
    repeated = mcp_server.handle({'id': 2, 'method': 'tools/call', 'params': {'name': 'patch_extraction_submission',
        'arguments': {'submission_id': staged['submission_id'], 'expected_revision': fixed['submission_revision'],
                      'patches': [{'op': 'append', 'section': 'nodes', 'value': {}}], 'reason': '禁止重复入库'}}}, default_project=entry)
    assert repeated['result']['isError']


def test_current_catalog_and_graph_schema_publish_one_repair_contract(project):
    names = {'list_extraction_submissions', 'read_extraction_submission', 'patch_extraction_submission'}
    assert names <= set(assistant.ASSISTANT_TOOLS)
    published = project.get_schema()['extraction_submission']['repair_tools']
    for name in names:
        assert published[name] == tool_api.TOOLS[name].schema()
    schema = published['patch_extraction_submission']['properties']['patches']['items']
    assert {v['properties']['op']['const'] for v in schema['oneOf']} == {'set', 'remove', 'append'}
    assert all(not v['additionalProperties'] for v in schema['oneOf'])
    assert tool_api.TOOLS['patch_extraction_submission'].writes and not tool_api.TOOLS['read_extraction_submission'].writes


def test_invalid_top_level_json_does_not_dispatch_or_create_a_fake_submission(monkeypatch, capsys):
    monkeypatch.setattr(tool_cli.tool_api, 'call', lambda *a, **k: pytest.fail('invalid JSON must not dispatch'))
    assert tool_cli.main(['tool', 'call', 'submit_extraction', '{broken', '--project', 'unused.nproj']) == 2
    assert 'JSON' in capsys.readouterr().err


def test_guidance_uses_feedback_direct_patch_and_never_claims_staged_as_committed():
    for name in ['extraction', 'source_extraction', 'review', 'action_extraction']:
        text = DEFAULT_PROMPTS[name]['text']
        assert 'staged' in text and '未写入正式事实' in text
        assert '无需先read_extraction_submission' in text and '一次patch数组' in text
        assert '不重发整批' in text and 'submission_revision' in text
        assert 'index从0起' in text and '从1起' in text


def test_pending_submissions_are_discoverable_without_draft_payload(project, capsys):
    entry, staged = stage_with_unknown_field(project)
    assert tool_cli.main(['extraction-submissions', '--project', entry, '--json']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [r['submission_id'] for r in listed['submissions']] == [staged['submission_id']]
    assert listed['submissions'][0]['validation_status'] == 'invalid'
    assert all('entries' not in r and 'result' not in r for r in listed['submissions'])
    tool_api.call('patch_extraction_submission', {'submission_id': staged['submission_id'], 'expected_revision': 1,
        'patches': [{'op': 'remove', 'section': 'nodes', 'index': 0, 'field': 'unexpected'}], 'reason': '完成问题修补'}, project=entry)
    reopened = tool_api.call('list_extraction_submissions', {}, project=entry)
    assert reopened['submissions'] == []


def test_one_patch_array_fixes_multiple_reported_nodes_without_read_or_resubmit(project):
    sid = project.import_source('多点局部修补', TEXT)
    draft = draft_result(ended=False)
    draft['nodes'][0]['quotes'] = ['甲的错误引文']
    draft['nodes'][1]['quotes'] = ['乙的错误引文']
    entry = str(layout(project.project_info().path).entry)
    staged = tool_api.call('submit_extraction', {'source_id': sid, 'result': draft}, project=entry)
    assert staged['status'] == 'staged'
    targeted = {target['index'] for problem in staged['problems'] for target in problem.get('targets', []) if target['section'] == 'nodes'}
    assert {0, 1} <= targeted
    fixed = tool_api.call('patch_extraction_submission', {'submission_id': staged['submission_id'],
        'expected_revision': staged['submission_revision'], 'reason': '按已给反馈一次修复两个引文字段',
        'patches': [{'op': 'set', 'section': 'nodes', 'index': index, 'field': 'quotes', 'value': ['甲约好接乙。']} for index in [0, 1]]}, project=entry)
    assert fixed['status'] == 'accepted' and fixed['submission_revision'] == 2 and not fixed['problems']
