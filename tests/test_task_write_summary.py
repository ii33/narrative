"""Program summaries account for committed business outcomes, not model claims."""
import copy
import json

import pytest

from narrative.operations import Operations
from narrative.project_package import layout
from narrative.task_write_summary import build_submission_summary


def step(tool, result, args=None, **fields):
    return {'tool': tool, 'args': args or {}, 'ok': True, 'result': result, **fields}


def test_staged_extraction_is_pending_not_a_business_commit():
    problem = {'severity': 'error', 'code': 'EVIDENCE', 'section': 'nodes', 'index': 2,
               'ref': '@3', 'field': 'quotes', 'message': '场景缺少引文'}
    result = build_submission_summary([step('submit_extraction', {
        'status': 'staged', 'valid': False, 'submission_id': 'extraction-submission-1',
        'submission_revision': 1, 'node_count': 20, 'problems': [problem]})])
    assert result['committed_operations'] == 0 and not result['changes']
    assert result['status'] == 'incomplete' and result['needs_correction']
    assert result['pending_submissions'][0]['problem_count'] == 1
    assert result['diagnostics'] == [problem]
    assert '暂存1批待修正' in result['compact_text']


def test_newer_staging_feedback_replaces_old_problem_points_and_preserves_stop():
    first = {'status': 'staged', 'valid': False, 'submission_id': 'extraction-submission-1',
             'submission_revision': 1, 'problems': [{'code': 'OLD', 'message': '旧问题'}]}
    second = first | {'submission_revision': 2, 'problems': [{'code': 'NEW', 'message': '剩余问题'}]}
    result = build_submission_summary([step('submit_extraction', first), step('patch_extraction_submission', second),
                                       step('submit_extraction', copy.deepcopy(first))], 'cancelled')
    assert result['status'] == 'cancelled'
    assert result['compact_text'].startswith('停止：')
    assert result['diagnostics'] == second['problems']
    assert result['pending_submissions'][0]['submission_revision'] == 2


def test_committed_correction_clears_pending_without_counting_staging_as_write():
    first = {'status': 'staged', 'valid': False, 'submission_id': 'extraction-submission-1',
             'submission_revision': 1, 'problems': [{'code': 'EVIDENCE', 'message': '待修'}]}
    committed = {'status': 'accepted', 'valid': True, 'submission_id': 'extraction-submission-1',
                 'submission_revision': 2, 'committed_changes': [
                     {'action': 'created', 'object_type': 'event', 'id': 'E1', 'name': '会面', 'count': 1}]}
    result = build_submission_summary([step('submit_extraction', first), step('patch_extraction_submission', committed)])
    assert not result['pending_submissions'] and not result['diagnostics']
    assert result['status'] == 'completed' and result['committed_operations'] == 1
    assert result['totals']['created'] == 1


def test_reading_an_existing_staged_draft_does_not_make_readonly_task_incomplete():
    result = build_submission_summary([step('read_extraction_submission', {
        'status': 'staged', 'submission_id': 'extraction-submission-1', 'submission_revision': 1,
        'problems': [{'code': 'EVIDENCE', 'message': '草稿待修'}]})])
    assert result['status'] == 'completed' and not result['pending_submissions']
    assert result['committed_operations'] == 0


def test_host_actions_are_not_business_commits_or_completed_commands():
    result = build_submission_summary([
        step('write_host_file', {'path': 'outside.txt'}),
        step('run_host_command', {'command_id': 'command-1', 'status': 'running'}),
        step('read_host_command', {'status': 'running'}),
    ])
    assert result['text'] == '完成：写入文件1，启动命令1'
    assert result['committed_operations'] == 0 and not result['changes']
    assert not any(result['totals'].values())
    assert len(result['host_operations']) == 2


def test_host_and_project_changes_keep_separate_receipts():
    result = build_submission_summary([
        step('save_asset', {'id': 'material-1', 'title': '纸张'}, {'payload': {'category': 'material'}}),
        step('run_host_command', {'command_id': 'command-1', 'status': 'running'}),
    ], 'cancelled')
    assert result['committed_operations'] == 1
    assert result['text'] == '停止：新增资产1，启动命令1'


def test_unfinished_host_cleanup_cannot_be_reported_as_task_success():
    cleanup = {'cleanup_receipts': [{'command_id': 'command-1', 'action': 'stopped_at_task_end', 'status': 'stopped',
                                    'reason': 'task_completed_before_command_finished'}],
               'diagnostics': [{'severity': 'warning', 'code': 'host_command_stopped_at_task_end', 'message': '命令尚未完成'}]}
    result = build_submission_summary([step('run_host_command', {'status': 'running'})], host_cleanup=cleanup)
    assert result['status'] == 'incomplete'
    assert result['text'] == '未完成：启动命令1；部分命令未完成，已停止1个'
    assert result['host_commands_incomplete'] == 1
    assert result['committed_operations'] == 0
    assert result['host_cleanup'] == cleanup
    assert result['diagnostics'][-1]['code'] == 'host_command_stopped_at_task_end'


def test_cancelled_command_cleanup_does_not_turn_user_stop_into_failure():
    cleanup = {'cleanup_receipts': [{'command_id': 'command-1', 'action': 'stopped_at_task_end', 'status': 'cancelled',
                                    'reason': 'task_cancelled'}], 'diagnostics': []}
    result = build_submission_summary([step('run_host_command', {'status': 'running'})], 'cancelled', host_cleanup=cleanup)
    assert result['status'] == 'cancelled' and result['host_commands_incomplete'] == 0
    assert result['compact_text'].startswith('停止：')


@pytest.fixture
def ops(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    return Operations.create_graph_project(tmp_path / 'summary.nproj')


def test_rejected_preview_failed_and_read_calls_never_become_committed_writes():
    steps = [step('read_source', {'text': '模型声称新增99个人物'}), step('preview_extraction', {'valid': True, 'node_count': 99}),
             step('submit_extraction', {'status': 'rejected', 'node_count': 4}), step('submit_extraction', {'accepted': False, 'node_count': 4}),
             step('apply_changes', {'applied': False, 'graph_count': 2}), step('apply_changes', {'dry_run': True, 'graph_count': 2}),
             step('apply_changes', {'preview': True, 'graph_count': 2}), {'tool': 'save_asset', 'ok': False, 'error': '素材修订冲突'}]
    result = build_submission_summary(steps, 'failed')
    assert result['committed_operations'] == 0 and result['no_changes']
    assert result['text'] == '失败：无入库变更' and not result['changes']
    assert any(d['message'] == '素材修订冲突' for d in result['diagnostics'])


def test_explicit_unchanged_is_completed_without_fabricating_changes():
    result = build_submission_summary([step('apply_changes', {'status': 'accepted', 'committed_changes': []}), step('save_asset', {'status': 'unchanged'})])
    assert result['completed_operations'] == 2 and result['committed_operations'] == 0
    assert result['no_changes'] and not any(result['totals'].values())
    assert all(not row['unresolved'] and row['action_count'] == 0 for row in result['operations'])


@pytest.mark.parametrize('created', [False, True])
def test_upsert_receipt_without_creation_evidence_is_saved_not_guessed_update(created):
    result = {'scenes': [{'scene_card': {'id': 'scene-card-1', 'unit_id': 'unit-1'}}]}
    if created:
        result['created_ids'] = {'@1': 'scene-card-1'}
    summary = build_submission_summary([step('apply_production_changes', result, {
        'unit_id': 'unit-1', 'changes': [{'op': 'upsert_scene_card', 'intent': '人物第一次听见远处的声音'}],
    })])
    assert summary['totals']['created'] == int(created)
    assert summary['totals']['saved'] == int(not created) and summary['totals']['updated'] == 0
    assert summary['changes'][0]['id'] == 'scene-card-1'
    assert summary['changes'][0]['type_label'] == '场卡'


def test_production_and_script_asset_domains_survive_cancel_without_revision_guess():
    steps = [step('apply_production_changes', {'created_ids': {'@1': 'element-1'}, 'elements': [{'id': 'element-1', 'name': '敲窗', 'kind': 'action'}]}, {'unit_id': 'unit-1', 'changes': [{'op': 'create_element', 'kind': 'action', 'name': '敲窗'}]}),
             step('apply_script_block_changes', {'blocks': [{'id': 'block-1', 'type': 'action', 'payload': {'text': '这不是总结文本'}}]}, {'unit_id': 'unit-1', 'target_block_ids': ['block-1'], 'changes': [{'op': 'update', 'block_id': 'block-1', 'block': {'type': 'action', 'payload': {'text': 'SECRET_MODEL_BODY'}}}]}),
             step('save_asset', {'id': 'material-1', 'title': '金属外观'}, {'payload': {'category': 'material'}})]
    result = build_submission_summary(steps, 'cancelled')
    assert result['committed_operations'] == 3 and result['totals']['created'] == 2 and result['totals']['updated'] == 1
    assert {r['domain'] for r in result['changes']} == {'production', 'screenplay', 'assets'}
    assert result['changes'][0]['name'] == '敲窗' and result['text'].startswith('停止：')
    assert 'SECRET_MODEL_BODY' not in json.dumps(result, ensure_ascii=False)


def test_stable_call_identity_dedupes_copies_but_repeated_actions_remain_history():
    create = step('save_asset', {'id': 'material-1', 'title': '道具'}, {'payload': {}}, call_id='call1')
    update = step('save_asset', {'id': 'material-1', 'title': '道具改'}, {'payload': {'id': 'material-1'}}, call_id='call2')
    delete = step('delete_asset', {'id': 'material-1'}, {'asset_id': 'material-1'}, call_id='call4')
    result = build_submission_summary([create, copy.deepcopy(create), update, update | {'call_id': 'call3'}, delete])
    assert result['committed_operations'] == 4
    assert result['totals']['created'] == result['totals']['updated'] == result['totals']['deleted'] == 1
    assert next(r for r in result['changes'] if r['action'] == 'updated')['occurrences'] == 2


def test_real_extraction_delta_distinguishes_create_update_and_event_change(ops):
    text = '甲在门口等乙。乙到门口见甲。'
    source = ops.import_source('交接', text)
    draft = {'nodes': [{'type': 'person', 'name': '甲', 'quotes': ['甲在门口等乙。']}, {'type': 'person', 'name': '乙', 'quotes': ['乙到门口见甲。']},
                       {'type': 'location', 'name': '门口', 'quotes': ['甲在门口等乙。']}, {'type': 'scene', 'name': '会面', 'quotes': [text]},
                       {'type': 'event', 'name': '等待', 'scene': '@4', 'participants': ['@1', '@2'], 'quotes': ['甲在门口等乙。']},
                       {'type': 'event', 'name': '会合', 'scene': '@4', 'participants': ['@1', '@2'], 'quotes': ['乙到门口见甲。']}],
             'properties': [{'node': '@5', 'op': 'set', 'property': '地点', 'value': '@3', 'value_type': 'node', 'at_event': '@5', 'quotes': ['甲在门口等乙。']}]}
    created = ops.submit_extraction(source, draft)
    assert created['status'] == 'accepted' and created['committed_changes']
    node = next(n for n in ops.graph_snapshot()['nodes'] if n['name'] == '等待')
    updated = ops.apply_changes([node | {'name': '等候乙'}], [], ops.graph_snapshot()['revision'], '更正名称')
    event = next(n for n in ops.graph_snapshot()['nodes'] if n['name'] == '会合')
    changed = ops.record_property_change(node['id'], '地点', event['id'], 'clear', expected_revision=ops.graph_snapshot()['revision'], reason='场所记录结束')
    assert changed['status'] == 'accepted'
    summary = build_submission_summary([step('submit_extraction', created, {'result': draft}), step('apply_changes', updated), step('record_property_change', changed)])
    assert summary['totals']['created'] > 0 and summary['totals']['updated'] == 1
    assert any(r['object_type'] == 'property_change' for r in summary['changes'])
    assert any(r['name'] == '等候乙' and r['action'] == 'updated' for r in summary['changes'])


@pytest.mark.parametrize('audit_status,card_status', [('incomplete', 'failed'), ('cancelled', 'cancelled'), ('completed', 'completed')])
def test_trusted_task_audit_reader_is_readonly_and_scoped(ops, audit_status, card_status):
    path = layout(ops.project_info().path).extraction / 'assistant' / 'tasks' / 'task1' / 'task.json'
    path.parent.mkdir(parents=True)
    steps = [step('save_compile_settings', {'revision': 1})]
    path.write_text(json.dumps({'chat_id': 'chat1', 'status': audit_status, 'steps': steps}), encoding='utf-8')
    before = path.read_bytes()
    summary = ops.read_task_write_summary('task1', 'chat1')
    assert summary == build_submission_summary(steps, card_status, task_id='task1')
    assert summary['status'] == card_status and summary['committed_operations'] == 1
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        ops.read_task_write_summary('../task1', 'chat1')
    with pytest.raises(ValueError, match='聊天'):
        ops.read_task_write_summary('task1', 'other')


def test_uncertainty_and_unknown_write_operation_are_not_invented_counts():
    result = build_submission_summary([step('submit_extraction', {'status': 'accepted', 'committed_changes': [{'action': 'created', 'object_type': 'person', 'id': 'P1', 'name': '甲', 'count': 1}]}, {'result': {'uncertainties': ['同名人物身份仍存疑']}}), step('duplicate_project', {'path': 'copy.nproj'})])
    assert result['committed_operations'] == 2 and result['totals']['created'] == 1
    assert result['operations'][1]['unresolved'] and result['operations'][1]['action_count'] is None
    assert any(d['message'] == '同名人物身份仍存疑' for d in result['diagnostics'])


def test_headless_current_turn_summaries_are_cumulative_snapshots_not_additive(ops):
    from narrative.chat_store import ChatStore

    chat = ChatStore(ops.project_info().path).create('无头任务')['id']
    steps = [step('save_asset', {'id': 'material-1', 'title': '纸张'}, {'payload': {}})]
    first = build_submission_summary(steps, 'partial', task_id='headless-task')
    steps.append(step('save_compile_settings', {'revision': 1}))
    final = build_submission_summary(steps, 'completed', task_id='headless-task')
    root = layout(ops.project_info().path).extraction / 'assistant'
    for turn, summary in [('turn-a', first), ('turn-b', final)]:
        path = root / 'chats' / chat / 'turns' / turn / 'metadata.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'task_id': 'headless-task', 'chat_id': chat, 'usage_turn_id': turn, 'commit_summary': summary}), encoding='utf-8')
    result = ops.read_task_write_summary('headless-task', chat)
    assert result['committed_operations'] == 2 and result['totals']['created'] == 1
    assert result['status'] == 'completed'
    # The native task audit is authoritative if both current sources exist.
    path = root / 'tasks' / 'headless-task' / 'task.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'chat_id': chat, 'status': 'completed', 'steps': steps}), encoding='utf-8')
    assert ops.read_task_write_summary('headless-task', chat) == result


def test_file_name_and_body_excerpt_are_readable_but_body_is_not_copied_whole():
    text = '可见的正文细节' * 80
    result = build_submission_summary([step('import_production_file', {'path': '/project/material/production_files/frame.png', 'file_name': 'frame.png'}),
        step('apply_script_block_changes', {'blocks': [{'id': 'block-1', 'payload': {'text': text}}]}, {'changes': [{'op': 'update', 'block_id': 'block-1'}]})])
    assert result['changes'][0]['name'] == 'frame.png'
    assert result['changes'][1]['name'].endswith('…') and len(result['changes'][1]['name']) == 49
    assert text not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_clear_receipt_counts_elements_and_dependencies_without_double_count(status):
    changes = [{'action': 'deleted', 'object_type': kind, 'id': identifier, 'count': 1}
               for kind, identifier in [('element', 'element-1'), ('occurrence', 'occurrence-1'), ('use', 'use-1')]]
    receipt = {'unit_id': 'unit-1', 'deleted_element_count': 1, 'deleted_dependency_count': 2, 'remaining_elements': 0, 'committed_changes': changes}
    result = build_submission_summary([step('apply_production_changes', {'committed_changes': changes, 'clear_elements_summaries': [receipt]}, {'changes': [{'op': 'clear_elements'}]})], status)
    assert result['totals']['deleted'] == 3 and result['committed_operations'] == 1
    assert result['clear_elements'] == [receipt]
    assert result['compact_text'] == {'completed': '完成', 'failed': '失败', 'cancelled': '停止'}[status] + '：删除制作元素1及相关记录2'
    assert result['text'] == result['compact_text']
    if status != 'completed':
        assert result['status'] == status


def test_clear_no_change_and_rejection_never_fabricate_deletions():
    receipt = {'unit_id': 'unit-1', 'deleted_element_count': 0, 'deleted_dependency_count': 0, 'remaining_elements': 0}
    result = build_submission_summary([step('apply_production_changes', {'committed_changes': [], 'clear_elements_summaries': [receipt]})])
    assert result['no_changes'] and result['totals']['deleted'] == 0
    assert result['compact_text'] == '完成：无入库变更'
    rejected = build_submission_summary([step('apply_production_changes', {'status': 'rejected', 'clear_elements_summaries': [receipt]})], 'failed')
    assert rejected['clear_elements'] == [] and rejected['text'] == '失败：无入库变更'


def test_mixed_clear_batch_accounts_exact_receipt_and_other_actions():
    receipt = {'unit_id': 'unit-1', 'action_index': 1, 'deleted_element_count': 1, 'deleted_dependency_count': 0, 'remaining_elements': 0,
               'committed_changes': [{'domain': 'production', 'action': 'deleted', 'object_type': 'element', 'id': 'element-old', 'count': 1}]}
    result = build_submission_summary([step('apply_production_changes', {'clear_elements_summaries': [receipt], 'created_ids': {'@2': 'element-new'}},
        {'changes': [{'op': 'clear_elements'}, {'op': 'create_element', 'kind': 'action', 'name': '新行为'}]})])
    assert result['totals']['deleted'] == result['totals']['created'] == 1
    assert result['object_counts_complete'] and len(result['changes']) == 2


def test_compact_summary_groups_objects_without_ids_or_operation_counts():
    steps = [step('save_asset', {'committed_changes': [
        {'action': 'created', 'object_type': 'asset', 'id': 'asset-1', 'name': '秘密名称'},
        {'action': 'created', 'object_type': 'asset', 'id': 'asset-2'},
    ]}), step('save_asset', {'committed_changes': [
        {'action': 'created', 'object_type': 'asset', 'id': 'asset-1'},
        {'action': 'deleted', 'object_type': 'asset', 'id': 'asset-1'},
    ]})]
    result = build_submission_summary(steps)
    assert result['compact_text'] == result['text'] == '完成：新增资产2，删除资产1'
    assert result['committed_operations'] == 2
    assert result['changes'][0]['occurrences'] == 2
    assert result['changes'][0]['name'] == '秘密名称'


def test_compact_unknown_write_does_not_invent_object_counts():
    result = build_submission_summary([step('duplicate_project', {'path': 'copy.nproj'})], 'cancelled')
    assert result['compact_text'] == '停止：变更已保存'
    assert not result['object_counts_complete'] and not result['changes']


def test_compact_limits_groups_without_losing_audit_detail():
    changes = [{'action': 'created', 'object_type': kind, 'id': kind, 'count': 1}
               for kind in ['element', 'occurrence', 'marker', 'requirement', 'use']]
    result = build_submission_summary([step('apply_production_changes', {'committed_changes': changes})])
    assert result['compact_text'] == '完成：新增制作元素1，新增出现1，新增同步标记1，另2类变更'
    assert len(result['changes']) == 5 and result['totals']['created'] == 5


def test_clear_compact_never_hides_unrelated_asset_deletion():
    changes = [{'action': 'deleted', 'object_type': kind, 'id': kind, 'count': 1}
               for kind in ['asset', 'element', 'occurrence']]
    receipt = {'deleted_dependency_count': 1, 'committed_changes': changes[1:]}
    result = build_submission_summary([step('apply_production_changes', {'committed_changes': changes, 'clear_elements_summaries': [receipt]})])
    assert result['compact_text'] == '完成：删除制作元素1及相关记录1，删除资产1'
    assert result['totals']['deleted'] == 3 and len(result['changes']) == 3
