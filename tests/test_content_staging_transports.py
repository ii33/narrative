"""Every adapter shares content staging, partial repairs, and commit receipts."""
import json

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project

from narrative import assistant, mcp_server, tool_api, tool_cli
from narrative.conversation_session import record_business_action, session_directory, use_chat
from narrative.graph_store import atomic_write_scope
from narrative.project_package import layout
from narrative.task_write_summary import build_submission_summary


def test_tool_stage_cli_patch_mcp_partial_read_and_canonical_receipt(project, capsys):
    entry = str(layout(project.project_info().path).entry)
    initial = project.load_compile_settings()
    arguments = {'settings': initial['settings'] | {'font_size_pt': -1},
                 'expected_revision': initial['revision'], 'reason': '校验字号'}
    staged = tool_api.call('save_compile_settings', arguments, project=entry)
    assert staged['status'] == 'staged' and 'arguments' not in staged
    assert '尚未入库' in assistant._summary('save_compile_settings', staged)
    response = mcp_server.handle({'id': 1, 'method': 'tools/call', 'params': {
        'name': 'read_content_submission', 'arguments': {'submission_id': staged['submission_id'],
            'paths': [['settings', 'font_size_pt']]}}}, default_project=entry)
    assert not response['result']['isError']
    detail = json.loads(response['result']['content'][0]['text'])
    assert detail['values'] == [{'path': ['settings', 'font_size_pt'], 'value': -1}]
    assert tool_cli.main(['content-patch', staged['submission_id'], '1', '--patches', json.dumps([
        {'op': 'set', 'path': ['settings', 'font_size_pt'], 'value': 15}]),
        '--reason', '仅修字号', '--project', entry, '--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted['status'] == 'accepted' and project.load_compile_settings()['settings']['font_size_pt'] == 15
    receipt = build_submission_summary([
        {'tool': 'save_compile_settings', 'args': arguments, 'ok': True, 'result': staged},
        {'tool': 'patch_content_submission', 'args': {}, 'ok': True, 'result': accepted}])
    assert not receipt['pending_submissions'] and not receipt['diagnostics']
    assert receipt['committed_operations'] == 1


def test_empty_authored_title_reaches_same_staging_through_tools(project):
    entry = str(layout(project.project_info().path).entry)
    staged = tool_api.call('create_screenplay', {'title': '', 'reason': '新建标题待修'}, project=entry)
    assert staged['status'] == 'staged'
    assert not project.list_screenplays()
    fixed = tool_api.call('patch_content_submission', {'submission_id': staged['submission_id'],
        'expected_revision': 1, 'patches': [{'op': 'set', 'path': ['title'], 'value': '新剧本'}],
        'reason': '补标题'}, project=entry)
    assert fixed['status'] == 'accepted' and len(project.list_screenplays()) == 1


def test_generic_api_repairs_existing_extraction_without_second_draft(project):
    source = project.import_source('提取适配', TEXT)
    draft = draft_result(ended=False)
    draft['nodes'][0]['quotes'] = ['原文没有的句子']
    staged = project.submit_extraction(source, draft)
    pending = project.list_content_submissions(operation='submit_extraction')
    assert [row['submission_id'] for row in pending['submissions']] == [staged['submission_id']]
    fixed = project.patch_content_submission(staged['submission_id'], 1, [
        {'op': 'set', 'path': ['result', 'nodes', 0, 'quotes'], 'value': ['甲约好接乙。']}], '统一入口只修引文')
    assert fixed['status'] == 'accepted'
    assert project.list_content_submissions()['submissions'] == []


def test_schema_publishes_catalog_and_exact_same_repair_tool_schemas(project):
    contract = project.get_schema()['content_submission']
    assert any(row['operation'] == 'submit_script_unit' for row in contract['operations'])
    for name in ('list_content_submissions', 'read_content_submission', 'patch_content_submission'):
        assert contract['repair_tools'][name] == tool_api.TOOLS[name].schema()


def test_material_tool_envelope_never_turns_staged_receipt_into_accepted(project):
    source = project.import_source('资料卡输入', TEXT)
    accepted = project.submit_extraction(source, draft_result(ended=False))
    person = accepted['node_ids']['@1']
    entry = str(layout(project.project_info().path).entry)
    staged = tool_api.call('add_material_card', {'node_id': person, 'title': ''}, project=entry)
    assert staged['status'] == 'staged' and staged['operation'] == 'material_add_card'
    assert project.material_view(person)['cards'] == []
    repaired = tool_api.call('patch_content_submission', {'submission_id': staged['submission_id'],
        'expected_revision': 1, 'patches': [{'op': 'set', 'path': ['title'], 'value': '人物备注'}],
        'reason': '补标题'}, project=entry)
    assert repaired['status'] == 'accepted'
    assert project.material_view(person)['cards'][0]['title'] == '人物备注'


def test_rolled_back_atomic_write_does_not_publish_business_feedback(project):
    with use_chat(project):
        path = session_directory(project) / 'business-actions.jsonl'
        before = path.read_bytes() if path.exists() else b''
        with pytest.raises(RuntimeError, match='事务回滚'):
            with atomic_write_scope(project._store):
                record_business_action(project, {'action': 'atomic_feedback_probe', 'result': {'revision': 1}})
                assert (path.read_bytes() if path.exists() else b'') == before
                raise RuntimeError('事务回滚')
        assert (path.read_bytes() if path.exists() else b'') == before
        with atomic_write_scope(project._store):
            record_business_action(project, {'action': 'atomic_feedback_probe', 'result': {'revision': 1}})
        assert path.read_bytes().startswith(before) and len(path.read_bytes()) > len(before)
