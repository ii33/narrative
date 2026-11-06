"""Staged drafts are repaired by stable slot without resending or partial facts."""
import json

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project


def bad_draft():
    draft = draft_result(ended=False)
    draft['nodes'][0]['quotes'] = ['不在原文中的句子']
    return draft


def test_invalid_submit_persists_only_draft_patch_auto_commits_and_reopen_reads(project):
    sid = project.import_source('暂存', TEXT)
    before = project.graph_snapshot(), project.property_records()
    result = project.submit_extraction(sid, bad_draft())
    assert result['status'] == 'staged' and result['submission_revision'] == 1
    assert result['submission_id'] == 'extraction-submission-1'
    assert result['problems'] and 'result' not in result and 'nodes' not in result
    assert (project.graph_snapshot(), project.property_records()) == before
    identifier = result['submission_id']
    summary = project.read_extraction_submission(identifier)
    assert 'entries' not in summary
    partial = project.read_extraction_submission(identifier, section='nodes', indices=[0])
    assert partial['entries'][0]['reference'] == '@1'
    assert partial['entries'][0]['item']['quotes'] == ['不在原文中的句子']
    fixed = project.patch_extraction_submission(identifier, 1,
        [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes', 'value': ['甲约好接乙。']}], '更正证据')
    assert fixed['status'] == 'accepted' and fixed['submission_status'] == 'committed'
    assert fixed['submission_revision'] == 2 and not fixed['problems']
    assert fixed['committed_changes'] and len(project.graph_snapshot()['nodes']) == 6
    assert project.read_extraction_submission(identifier)['submission_status'] == 'committed'
    assert project.read_extraction_submission(identifier)['status'] == 'accepted'
    assert project.list_extraction_submissions()['submissions'] == []
    with pytest.raises(ValueError, match='已完成'):
        project.patch_extraction_submission(identifier, 2, [{'op': 'append', 'section': 'nodes', 'value': {}}], '不能重复')
    with project.project.store.connect() as db:
        summaries = [json.loads(r[0]) for r in db.execute("SELECT summary FROM actions WHERE action='extraction_staging' ORDER BY rowid")]
    assert [r['submission_revision'] for r in summaries] == [1, 2]
    assert summaries[-1]['changed_fields'] == [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes'}]
    assert all('result' not in r for r in summaries)


def test_patch_stale_revision_and_illegal_splice_are_atomic(project):
    sid = project.import_source('暂存', TEXT)
    initial = project.submit_extraction(sid, bad_draft())
    identifier = initial['submission_id']
    changed = project.patch_extraction_submission(identifier, 1,
        [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes', 'value': ['依旧不存在']}], '仍待修')
    assert changed['status'] == 'staged' and changed['submission_revision'] == 2
    before = project.read_extraction_submission(identifier, section='nodes')
    with pytest.raises(ValueError, match='revision'):
        project.patch_extraction_submission(identifier, 1, [{'op': 'append', 'section': 'nodes', 'value': {}}], '过期')
    with pytest.raises(ValueError):
        project.patch_extraction_submission(identifier, 2, [{'op': 'remove', 'section': 'nodes', 'index': 0}], '禁止删行')
    assert project.read_extraction_submission(identifier, section='nodes') == before
    assert not project.graph_snapshot()['nodes']


def test_formal_changes_between_read_and_patch_are_revalidated(project):
    sid = project.import_source('暂存', TEXT)
    staged = project.submit_extraction(sid, bad_draft())
    # A separate legal submission changes formal identities while the draft waits.
    accepted = project.submit_extraction(sid, draft_result(ended=False))
    assert accepted['status'] == 'accepted'
    fixed = project.patch_extraction_submission(staged['submission_id'], 1,
        [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes', 'value': ['甲约好接乙。']}], '在新正式资料重检')
    assert fixed['status'] == 'unchanged' and fixed['submission_status'] == 'committed'
    assert len(project.graph_snapshot()['nodes']) == 6


def test_callback_failure_rolls_back_facts_and_submission_together(project, monkeypatch):
    from narrative import extraction_staging
    sid = project.import_source('暂存', TEXT)
    staged = project.submit_extraction(sid, bad_draft())
    before = project.graph_snapshot(), project.property_records()
    original = extraction_staging._save
    def fail(db, row):
        if row['submission_status'] == 'committed':
            raise RuntimeError('注入暂存写入失败')
        return original(db, row)
    monkeypatch.setattr(extraction_staging, '_save', fail)
    with pytest.raises(RuntimeError, match='注入'):
        project.patch_extraction_submission(staged['submission_id'], 1,
            [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes', 'value': ['甲约好接乙。']}], '回滚测试')
    assert (project.graph_snapshot(), project.property_records()) == before
    assert project.read_extraction_submission(staged['submission_id'])['submission_revision'] == 1


def test_staging_not_loaded_into_canonical_validation_snapshot(project):
    sid = project.import_source('暂存', TEXT)
    result = project.submit_extraction(sid, bad_draft())
    batch = project.capture_changes()
    assert not any(key.startswith('extraction_submission:') for key in batch._meta)
    assert 'next_extraction_submission_number' not in batch._meta
    assert project.read_extraction_submission(result['submission_id'])['problems']


def test_schema_problem_can_be_repaired_by_remove_field_without_row_reordering(project):
    sid = project.import_source('暂存', TEXT)
    draft = draft_result(ended=False)
    draft['nodes'][0]['unexpected'] = True
    staged = project.submit_extraction(sid, draft)
    problem = staged['problems'][0]
    assert (problem['section'], problem['index'], problem['reference']) == ('nodes', 0, '@1')
    fixed = project.patch_extraction_submission(staged['submission_id'], 1,
        [{'op': 'remove', 'section': 'nodes', 'index': 0, 'field': 'unexpected'}], '移除非法字段')
    assert fixed['status'] == 'accepted'
    assert fixed['node_ids']['@1'] in {n['id'] for n in project.graph_snapshot()['nodes']}


def test_append_preserves_prior_at_reference_indices(project):
    sid = project.import_source('暂存', TEXT)
    draft = draft_result(ended=False)
    draft['properties'][0]['holder'] = '@7'
    staged = project.submit_extraction(sid, draft)
    fixed = project.patch_extraction_submission(staged['submission_id'], 1,
        [{'op': 'append', 'section': 'nodes', 'value': {'type': 'person', 'name': '额外称谓', 'quotes': ['乙收起便笺。']}}], '补齐明确引用')
    assert fixed['status'] == 'accepted'
    assert set(fixed['node_ids']) >= {'@1', '@7'}


def test_conflicting_formal_revision_retains_draft_without_committing(project, monkeypatch):
    sid = project.import_source('暂存', TEXT)
    staged = project.submit_extraction(sid, bad_draft())
    commit = project.commit_changes
    def concurrent(prepared, reason, **kwargs):
        project.import_source('并发原文', '另一个合法原文')
        return commit(prepared, reason, **kwargs)
    monkeypatch.setattr(project, 'commit_changes', concurrent)
    result = project.patch_extraction_submission(staged['submission_id'], 1,
        [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes', 'value': ['甲约好接乙。']}], '并发重检')
    assert result['status'] == 'staged' and result['submission_revision'] == 2
    assert result['problems'][0]['code'] == 'FORMAL_REVISION_CHANGED'
    assert not project.graph_snapshot()['nodes']


def test_unchanged_formal_revision_conflict_is_staged_not_error(project, monkeypatch):
    from narrative import extraction_draft
    sid = project.import_source('暂存', TEXT)
    assert project.submit_extraction(sid, draft_result(ended=False))['status'] == 'accepted'
    staged = project.submit_extraction(sid, bad_draft())
    prepare = extraction_draft._prepare
    def concurrent(*args, **kwargs):
        result = prepare(*args, **kwargs)
        project.import_source('并发原文', '另一份原文')
        return result
    monkeypatch.setattr(extraction_draft, '_prepare', concurrent)
    result = project.patch_extraction_submission(staged['submission_id'], 1,
        [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes', 'value': ['甲约好接乙。']}], '无变化并发')
    assert result['status'] == 'staged' and result['problems'][0]['code'] == 'FORMAL_REVISION_CHANGED'
    assert len(project.graph_snapshot()['nodes']) == 6


def test_pending_is_discoverable_after_interrupted_check_and_pages_are_bounded(project, monkeypatch):
    from narrative import extraction_staging
    sid = project.import_source('暂存', TEXT)
    evaluate = extraction_staging._evaluate
    def interrupted(*args):
        raise RuntimeError('检查前任务中止')
    monkeypatch.setattr(extraction_staging, '_evaluate', interrupted)
    with pytest.raises(RuntimeError):
        project.submit_extraction(sid, bad_draft())
    pending = project.list_extraction_submissions()
    assert len(pending['submissions']) == 1
    assert pending['submissions'][0]['validation_status'] == 'pending'
    assert pending['submissions'][0]['problems'][0]['code'] == 'CHECK_PENDING'
    monkeypatch.setattr(extraction_staging, '_evaluate', evaluate)
    project.submit_extraction(sid, bad_draft())
    first = project.list_extraction_submissions(sid, limit=1)
    assert len(first['submissions']) == 1 and first['next_after'] == 1
    second = project.list_extraction_submissions(sid, limit=1, after=first['next_after'])
    assert second['submissions'][0]['submission_id'] == 'extraction-submission-2'
    assert second['next_after'] is None
    assert all('result' not in row and 'entries' not in row for row in first['submissions'])


def test_concurrent_draft_patch_before_commit_rolls_back_canonical_delta(project, monkeypatch):
    sid = project.import_source('暂存', TEXT)
    staged = project.submit_extraction(sid, bad_draft())
    commit = project.commit_changes
    def concurrent(prepared, reason, **kwargs):
        # Another editor successfully stores a different unresolved revision.
        competing = project.patch_extraction_submission(staged['submission_id'], 1,
            [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes', 'value': ['另一个错误引文']}], '另编辑者')
        assert competing['submission_revision'] == 2
        return commit(prepared, reason, **kwargs)
    monkeypatch.setattr(project, 'commit_changes', concurrent)
    with pytest.raises(ValueError, match='revision'):
        project.patch_extraction_submission(staged['submission_id'], 1,
            [{'op': 'set', 'section': 'nodes', 'index': 0, 'field': 'quotes', 'value': ['甲约好接乙。']}], '落后修改')
    assert not project.graph_snapshot()['nodes']
    current = project.read_extraction_submission(staged['submission_id'], section='nodes', indices=[0])
    assert current['submission_revision'] == 2 and current['entries'][0]['item']['quotes'] == ['另一个错误引文']


def test_static_problem_targets_exact_original_property_row_not_same_name_other_owner(project):
    sid = project.import_source('同属性不同主体', TEXT)
    draft = draft_result(ended=False)
    draft['properties'] = [
        {'node': '甲', 'property': '计数', 'value_type': 'number', 'value': 7,
         'op': 'set', 'at_event': '甲约好接乙', 'quotes': ['甲约好接乙。']},
        {'node': '乙', 'property': '计数', 'value_type': 'number', 'value': '不是数值',
         'op': 'set', 'at_event': '甲接到乙后离开', 'quotes': ['甲接到乙后离开。']},
    ]
    staged = project.submit_extraction(sid, draft)
    assert staged['status'] == 'staged'
    property_problems = [p for p in staged['problems'] if p['object_id'].startswith('PC')]
    assert property_problems
    assert all([(t['section'], t['index']) for t in p['targets']] == [('properties', 1)] for p in property_problems), property_problems
    assert all(not any(t['index'] == 0 for t in p['targets'] if t['section'] == 'properties') for p in property_problems)
    fixed = project.patch_extraction_submission(staged['submission_id'], 1,
        [{'op': 'set', 'section': 'properties', 'index': 1, 'field': 'value', 'value': 8}], '仅修错误主体的计数')
    assert fixed['status'] == 'accepted'
    names = {n['name']: n['id'] for n in project.graph_snapshot()['nodes']}
    assert project.property_state(names['甲'], '计数')['state']['value'] == 7
    assert project.property_state(names['乙'], '计数')['state']['value'] == 8
    assert all('_draft_index' not in r and 'property_origins' not in r for r in project.property_records())
