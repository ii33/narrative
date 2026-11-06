"""UI-direct Operations and shared transactions stage only authored content errors."""
import copy

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project

from narrative.graph_store import active_connection, atomic_write_scope


def test_direct_settings_invalid_stage_local_patch_commits(project):
    before = project.load_compile_settings()
    settings = copy.deepcopy(before['settings'])
    settings['font_size_pt'] = -1
    staged = project.save_compile_settings(settings, before['revision'], '非法排版')
    assert staged['status'] == 'staged' and staged['operation'] == 'save_compile_settings'
    assert staged['problems'][0]['path'] == ['settings', 'font_size_pt']
    assert project.load_compile_settings() == before
    assert 'arguments' not in staged
    detail = project.read_content_submission(staged['submission_id'], paths=[['settings', 'font_size_pt']])
    assert detail['values'] == [{'path': ['settings', 'font_size_pt'], 'value': -1}]
    ready = project.preview_content_patch(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['settings', 'font_size_pt'], 'value': 14}])
    assert ready['operation'] == 'save_compile_settings' and ready['arguments']['settings']['font_size_pt'] == 14
    committed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['settings', 'font_size_pt'], 'value': 14}], '只改字号')
    assert committed['status'] == 'accepted'
    assert project.load_compile_settings()['settings']['font_size_pt'] == 14
    assert project.list_content_submissions()['submissions'] == []
    with pytest.raises(ValueError):
        project.patch_content_submission(staged['submission_id'], 2, [{'op': 'set', 'path': ['settings', 'font_size_pt'], 'value': 15}], '不得重放')


def test_stale_business_revision_is_hard_reject_and_cannot_patch_target(project):
    before = project.load_compile_settings()
    project.save_compile_settings(before['settings'], before['revision'], '推进修订')
    with pytest.raises(ValueError, match='变化'):
        project.save_compile_settings(before['settings'], before['revision'], '过期')
    assert project.list_content_submissions()['submissions'] == []
    bad = project.save_compile_settings(before['settings'] | {'font_size_pt': -1}, project.load_compile_settings()['revision'], '暂存')
    with pytest.raises(ValueError, match='目标|依据'):
        project.patch_content_submission(bad['submission_id'], 1, [{'op': 'set', 'path': ['expected_revision'], 'value': 999}], '不能偷更新')


def test_shared_connection_reads_existing_transaction_and_rolls_back(project):
    before = project.graph_project_info()
    with pytest.raises(RuntimeError):
        with atomic_write_scope(project._store) as db:
            assert active_connection(project._store.path) is db
            with project._store.connect(write=True) as borrowed:
                assert borrowed is db
                borrowed.execute("UPDATE meta SET value='事务临时值' WHERE key='title'")
            assert project.graph_snapshot()['revision'] == before['revision']
            assert project.graph_project_info()['title'] == '事务临时值'
            raise RuntimeError('失败')
    assert project.graph_project_info() == before


def test_graph_content_invalid_stage_no_nested_duplicate_and_patch(project):
    sid = project.import_source('原文', TEXT)
    assert project.submit_extraction(sid, draft_result(ended=False))['status'] == 'accepted'
    names = {n['name']: n['id'] for n in project.graph_snapshot()['nodes']}
    staged = project.record_property_change(names['甲'], '新计数', names['甲约好接乙'], 'set', value='错误数字', value_type='number',
        expected_revision=project.graph_project_info()['revision'], reason='内容暂存')
    assert staged['status'] == 'staged'
    pending = project.list_content_submissions()
    assert [row['operation'] for row in pending['submissions']] == ['record_property_change']
    committed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['value'], 'value': 3}], '只改数值')
    assert committed['status'] == 'accepted'
    assert project.property_state(names['甲'], '新计数')['state']['value'] == 3


def test_extraction_adapter_is_discoverable_readable_and_patchable(project):
    sid = project.import_source('原文', TEXT)
    draft = draft_result(ended=False)
    draft['nodes'][0]['quotes'] = ['不存在']
    staged = project.submit_extraction(sid, draft)
    assert project.list_content_submissions(operation='submit_extraction')['submissions'][0]['submission_id'] == staged['submission_id']
    args = project.read_content_submission(staged['submission_id'], paths=[[]])['values'][0]['value']
    assert args['result']['nodes'][0]['quotes'] == ['不存在']
    patch = [{'op': 'set', 'path': ['result', 'nodes', 0, 'quotes'], 'value': ['甲约好接乙。']}]
    resolved = project.preview_content_patch(staged['submission_id'], 1, patch)
    assert resolved['operation'] == 'submit_extraction'
    assert project.patch_content_submission(staged['submission_id'], 1, patch, '仅修引文')['status'] == 'accepted'


def test_material_metadata_draft_and_receipt_share_authoritative_json(project):
    sid = project.import_source('原文', TEXT)
    project.submit_extraction(sid, draft_result(ended=False))
    person = next(n['id'] for n in project.graph_snapshot()['nodes'] if n['name'] == '甲')
    staged = project.material_add_card(person, '', '资料正文')
    assert staged['status'] == 'staged'
    fixed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['title'], 'value': '新资料'}], '只补标题')
    assert fixed['status'] == 'accepted'
    assert len(project.material_view(person)['cards']) == 1
    from narrative.node_material import MaterialStore
    data = MaterialStore(project._store.path).load(person)
    receipt = data['content_submissions'][staged['submission_id']]
    assert receipt['submission_status'] == 'committed' and receipt['business_result']['id'] == data['cards'][0]['id']


def test_protected_asset_id_cannot_change_through_whole_payload_replacement():
    from narrative.business_staging import _apply_patch
    row = {'operation': 'save_asset', 'arguments': {'payload': {'id': 'asset-1', 'title': ''}, 'expected_revision': 1}}
    with pytest.raises(ValueError, match='身份|目标'):
        _apply_patch(row, [{'op': 'set', 'path': ['payload'], 'value': {'id': 'asset-2', 'title': '偷换'}}])
    new = {'operation': 'save_asset', 'arguments': {'payload': {'title': ''}, 'expected_revision': 1}}
    with pytest.raises(ValueError, match='身份|目标'):
        _apply_patch(new, [{'op': 'set', 'path': ['payload', 'id'], 'value': 'asset-2'}])


def test_asset_content_patch_uses_shared_attached_transaction(project):
    staged = project.save_asset({'category': 'material', 'title': ''}, 0)
    assert staged['status'] == 'staged'
    assert project.list_assets('material')['assets'] == []
    fixed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['payload', 'title'], 'value': '纸张设定'}], '补标题')
    assert fixed['status'] == 'accepted'
    assert project.get_asset(fixed['id'])['title'] == '纸张设定'


def test_post_commit_feedback_failure_cannot_reject_or_replay_committed_content(project):
    from narrative.business_staging import invoke
    from narrative.graph_store import defer_after_commit
    def call(_arguments):
        with project._store.connect(write=True) as db:
            db.execute("UPDATE meta SET value='确已保存' WHERE key='title'")
        def feedback():
            raise RuntimeError('反馈故障')
        assert defer_after_commit(project._store.path, feedback)
        return {'status': 'accepted'}
    saved = invoke(project, 'save_project_style', {'text': 'x', 'expected_revision': 0, 'reason': '反馈测试'}, call)
    assert saved['status'] == 'accepted' and saved['diagnostics'][0]['code'] == 'POST_COMMIT_FEEDBACK'
    assert project.graph_project_info()['title'] == '确已保存'
    assert project.read_content_submission(saved['submission_id'])['submission_status'] == 'committed'


def test_material_index_failure_returns_warning_and_file_receipt_prevents_replay(project, monkeypatch):
    sid = project.import_source('原文', TEXT)
    project.submit_extraction(sid, draft_result(ended=False))
    person = next(n['id'] for n in project.graph_snapshot()['nodes'] if n['name'] == '甲')
    # The graph-index save is imported locally; fail only its committed mirror.
    from narrative import business_staging
    save = business_staging._save
    def fail(db, row):
        if row['operation'] == 'material_add_card' and row['submission_status'] == 'committed':
            raise RuntimeError('索引反馈故障')
        return save(db, row)
    monkeypatch.setattr(business_staging, '_save', fail)
    result = project.material_add_card(person, '确定性资料')
    assert result['status'] == 'accepted' and result['feedback_error']['type'] == 'RuntimeError'
    assert len(project.material_view(person)['cards']) == 1
    assert project.read_content_submission(result['submission_id'])['submission_status'] == 'committed'
    assert project.list_content_submissions()['submissions'] == []
    with pytest.raises(ValueError):
        project.patch_content_submission(result['submission_id'], 1,
            [{'op': 'set', 'path': ['title'], 'value': '不重放'}], '拒绝')


def test_captured_bulk_target_cannot_be_changed_by_whole_array_replacement():
    from narrative.business_staging import _apply_patch
    row = {'operation': 'apply_script_block_changes', 'arguments': {'unit_id': 'unit-1', 'changes': [
        {'op': 'update', 'id': 'block-1', 'changes': {'payload': {'text': ''}}}], 'target_block_ids': ['block-1'], 'expected_body_revision': 1}}
    with pytest.raises(ValueError):
        _apply_patch(row, [{'op': 'set', 'path': ['changes'], 'value': [
            {'op': 'update', 'id': 'block-2', 'changes': {'payload': {'text': '不能偷换'}}}]}])


def test_stale_settings_with_bad_payload_reject_before_content_staging(project):
    from narrative.content_errors import ContentConflictError
    before = project.load_compile_settings()
    project.save_compile_settings(before['settings'], before['revision'], '先保存')
    with pytest.raises(ContentConflictError):
        project.save_compile_settings({'font_size_pt': -1}, before['revision'], '')
    assert project.list_content_submissions()['submissions'] == []


def test_empty_reason_is_locally_repairable_without_refreshing_revision(project):
    before = project.load_compile_settings()
    staged = project.save_compile_settings(before['settings'], before['revision'], '')
    assert staged['status'] == 'staged' and 'reason' in staged['editable_roots']
    fixed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['reason'], 'value': '明确的排版修改依据'}], '补全修改依据')
    assert fixed['status'] == 'accepted'


def test_stale_graph_property_with_bad_value_rejects_before_content_staging(project):
    sid = project.import_source('原文', TEXT)
    project.submit_extraction(sid, draft_result(ended=False))
    names = {n['name']: n['id'] for n in project.graph_snapshot()['nodes']}
    result = project.record_property_change(names['甲'], '新值', names['甲约好接乙'], 'invalid', value_type=[],
        expected_revision=0, reason='')
    assert result['status'] == 'rejected'
    assert project.list_content_submissions()['submissions'] == []


def test_rollback_cleanup_failure_keeps_original_error_and_runs_remaining_callbacks(project):
    from narrative.graph_store import on_rollback
    performed = []
    def broken():
        performed.append('broken')
        raise OSError('一个新文件被占用')
    with pytest.raises(ValueError, match='原始内容错误') as captured:
        with atomic_write_scope(project._store):
            on_rollback(project._store.path, lambda: performed.append('remaining'))
            on_rollback(project._store.path, broken)
            raise ValueError('原始内容错误')
    assert performed == ['broken', 'remaining']
    assert any('回滚清理失败' in note and 'OSError' in note for note in captured.value.__notes__)


def test_primitive_reference_array_is_repairable_without_retargeting_existing_row():
    from narrative.business_staging import _apply_patch
    row = {'operation': 'apply_scene_table_changes', 'arguments': {'segment_id': 'segment-1', 'expected_outline_revision': 1,
        'changes': [{'op': 'update', 'unit_id': 'unit-1', 'changes': {'plan': {'cast_ids': ['not-in-this-script']}}}]}}
    candidate = _apply_patch(row, [{'op': 'set', 'path': ['changes', 0, 'changes', 'plan', 'cast_ids'], 'value': []}])
    assert candidate['changes'][0]['changes']['plan']['cast_ids'] == []
    assert candidate['changes'][0]['unit_id'] == 'unit-1'


def test_material_empty_image_list_does_not_bypass_staging(project):
    sid = project.import_source('原文', TEXT)
    project.submit_extraction(sid, draft_result(ended=False))
    person = next(n['id'] for n in project.graph_snapshot()['nodes'] if n['name'] == '甲')
    staged = project.material_add_card(person, '', '表单总传空图列表', images=[])
    assert staged['status'] == 'staged'
    fixed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['title'], 'value': '合法资料'}], '只补标题')
    assert fixed['status'] == 'accepted' and fixed['committed_changes']


def test_material_existing_image_caption_is_content_but_image_deletion_is_immediate(project, tmp_path):
    from PIL import Image
    sid = project.import_source('原文', TEXT)
    project.submit_extraction(sid, draft_result(ended=False))
    person = next(n['id'] for n in project.graph_snapshot()['nodes'] if n['name'] == '甲')
    image = tmp_path / 'original.png'
    Image.new('RGB', (4, 4), 'white').save(image)
    original_bytes = image.read_bytes()
    card = project.material_add_card(person, '带图资料', images=[{'path': str(image), 'caption': '旧说明'}])
    existing = [{'id': card['images'][0]['id'], 'caption': '新说明'}]
    staged = project.material_update_card(person, card['id'], title='', images=existing)
    assert staged['status'] == 'staged'
    fixed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['title'], 'value': '新标题'}], '保留图片只改文案')
    assert fixed['status'] == 'accepted'
    saved = project.material_view(person)['cards'][0]
    assert saved['images'][0]['caption'] == '新说明'
    assert image.read_bytes() == original_bytes
    with pytest.raises(ValueError):
        project.material_update_card(person, card['id'], title='', images=[])
    assert not project.list_content_submissions()['submissions']
    assert len(project.material_view(person)['cards'][0]['images']) == 1


def test_pending_material_edit_cannot_overwrite_later_change_to_same_card(project):
    from narrative.content_errors import ContentConflictError
    sid = project.import_source('原文', TEXT)
    project.submit_extraction(sid, draft_result(ended=False))
    person = next(n['id'] for n in project.graph_snapshot()['nodes'] if n['name'] == '甲')
    card = project.material_add_card(person, '原标题')
    staged = project.material_update_card(person, card['id'], title='', detail='旧任务想改的详情', images=[])
    assert staged['status'] == 'staged'
    project.material_update_card(person, card['id'], title='后续明确修改', images=[])
    with pytest.raises(ContentConflictError):
        project.patch_content_submission(staged['submission_id'], 1,
            [{'op': 'set', 'path': ['title'], 'value': '不能覆盖后续编辑'}], '过期资料')
    assert project.material_view(person)['cards'][0]['title'] == '后续明确修改'
    assert project.read_content_submission(staged['submission_id'])['submission_status'] == 'rejected'


def test_source_slice_range_table_stages_one_bad_row_and_patches_it(project):
    sid = project.import_source('原文', TEXT)
    staged = project.create_source_slices(sid, [
        {'title': '第一句', 'start': 0, 'end': 6},
        {'title': '后续', 'start': 6, 'end': len(TEXT) + 1}])
    assert staged['status'] == 'staged'
    assert staged['problems'][0]['path'] == ['slices', 1, 'end']
    assert project.source_slices(sid) == []
    fixed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['slices', 1, 'end'], 'value': len(TEXT)}], '只修越界终点')
    assert fixed['status'] == 'accepted' and len(fixed['result']) == 2


def test_source_slice_unknown_field_is_precise_and_removable(project):
    sid = project.import_source('原文', TEXT)
    staged = project.create_source_slices(sid, [{'title': '范围', 'start': 0, 'end': len(TEXT), 'extra': True}])
    assert staged['status'] == 'staged' and staged['problems'][0]['path'] == ['slices', 0, 'extra']
    fixed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'remove', 'path': ['slices', 0, 'extra']}], '删非法字段')
    assert fixed['status'] == 'accepted'


def test_readable_counter_corruption_and_no_transaction_are_hard_conflicts():
    import sqlite3

    from narrative.content_errors import ContentConflictError
    from narrative.readable_ids import allocate_id
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
    with pytest.raises(ContentConflictError):
        allocate_id(db, 'test', 'node-')
    db.execute('BEGIN')
    db.execute("INSERT INTO meta VALUES ('readable_id:test:node-', 'broken')")
    with pytest.raises(ContentConflictError):
        allocate_id(db, 'test', 'node-')
    db.rollback()
    db.close()


def test_attempt_and_preview_use_current_host_provenance_not_old_task(project):
    from narrative.content_operations import execution_arguments
    from narrative.tool_execution_context import bind_tool_execution_metadata
    arguments = {'unit_id': 'unit-1', 'blocks': [], 'expected_plan_revision': 2, 'expected_body_revision': 3,
        'span_fingerprint': 'span-original', 'context_fingerprint': 'context-original', 'plan_fingerprint': 'plan-original',
        'task_id': 'old-task', 'manifest_ref': 'old-manifest', 'reason': '只读验证'}
    with bind_tool_execution_metadata({'task_id': 'current-task', 'manifest_ref': 'current-manifest'}):
        actual = execution_arguments(project, 'submit_script_unit', arguments)
    assert actual['task_id'] == 'current-task' and actual['manifest_ref'] == 'current-manifest'
    assert actual['expected_body_revision'] == 3 and actual['context_fingerprint'] == 'context-original'
    assert arguments['task_id'] == 'old-task'
    ui = execution_arguments(project, 'submit_script_unit', arguments)
    assert ui['task_id'] is None and ui['manifest_ref'] is None


def test_optional_asset_revision_still_captures_pending_basis(project):
    from narrative.content_errors import ContentConflictError
    staged = project.save_asset({'category': 'material', 'title': ''})
    assert staged['status'] == 'staged'
    project.save_asset({'category': 'material', 'title': '后来创建的合法资料'})
    with pytest.raises(ContentConflictError, match='依据已变化'):
        project.patch_content_submission(staged['submission_id'], 1,
            [{'op': 'set', 'path': ['payload', 'title'], 'value': '不能偷偷刷新原依据'}], '并发后修补')
    assert len(project.list_assets('material')['assets']) == 1


def test_fixed_missing_owner_is_guard_but_missing_value_reference_can_be_repaired(project):
    from narrative.content_errors import ContentGuardError
    sid = project.import_source('原文', TEXT)
    project.submit_extraction(sid, draft_result(ended=False))
    names = {n['name']: n['id'] for n in project.graph_snapshot()['nodes']}
    definition = {'kind': 'property_definition', 'id': '指向对象', 'value_type': 'node', 'cardinality': 'one'}
    change = {'kind': 'property_change', 'node_id': 'missing-owner', 'property_id': '指向对象',
        'event_id': names['甲约好接乙'], 'sequence': 10, 'op': 'set', 'value': names['乙']}
    with pytest.raises(ContentGuardError, match='主体不存在'):
        project.apply_changes([], [definition, change], project.graph_project_info()['revision'], '固定主体错误')
    assert project.list_content_submissions()['submissions'] == []
    staged = project.apply_changes([], [definition, change | {'node_id': names['甲'], 'value': 'missing-value'}],
        project.graph_project_info()['revision'], '仅内容引用错误')
    assert staged['status'] == 'staged'
    fixed = project.patch_content_submission(staged['submission_id'], 1,
        [{'op': 'set', 'path': ['property_records', 1, 'value'], 'value': names['乙']}], '修内容引用')
    assert fixed['status'] == 'accepted'
    assert project.property_state(names['甲'], '指向对象')['state']['value'] == [names['乙']]
