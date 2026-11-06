"""Unified conversation drafts use real Operations, without provider requests."""
import copy
import json

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project


def test_preview_is_readonly_and_submit_automatically_applies_with_traceable_uncertainty(project):
    sid = project.import_source('合成原文', TEXT)
    data = draft_result()
    reason = '合成夹具：关系含义有待后文补充'
    data['uncertainties'] = [{'node': '甲', 'reason': reason}]
    before = project.graph_snapshot()
    assert project.preview_extraction(sid, data)['valid']
    assert project.graph_snapshot() == before
    result = project.submit_extraction(sid, data)
    assert result['status'] == 'accepted'
    with project.project.store.connect() as db:
        note = json.loads(db.execute("SELECT reason FROM actions WHERE revision=?", (result["revision"],)).fetchone()[0])
    assert note["uncertainties"] == data["uncertainties"]
    assert note["expirations"] == data["expirations"]
    assert "accepted" not in result and result["uncertainty_count"] == 1
    records = project.property_records()
    plan = next(r for r in records if r.get('property_id') == '计划' and r['kind'] == 'property_value')
    end = plan['valid_to']
    assert any(r['property_id'] == '计划' for r in project.property_node_summary(plan['node_id'], cut=plan['event_id'])['properties'])
    assert not any(r['property_id'] == '计划' for r in project.property_node_summary(plan['node_id'], cut=end['event_id'])['properties'])


def test_duplicate_submission_is_idempotent_and_later_new_facts_apply(project):
    sid = project.import_source('合成原文', TEXT)
    first = project.submit_extraction(sid, draft_result())
    snapshot = copy.deepcopy(project.graph_snapshot())
    again = project.submit_extraction(sid, draft_result())
    assert again['revision'] == first['revision']
    assert project.graph_snapshot() == snapshot
    updated = project.submit_extraction(sid, draft_result(note=True))
    assert updated['status'] == 'accepted'
    assert any(n['name'] == '便笺' for n in project.graph_snapshot()['nodes'])


@pytest.mark.parametrize('problem', ['quote', 'expiration', '关系', '关系（朋友）', '关系(朋友)', 'heading'])
def test_invalid_submission_never_mutates_graph_or_leaves_pending(project, problem):
    heading = '第一卷 远行 第一章 来客'
    sid = project.import_source('合成原文', heading + '\n' + TEXT)
    data = draft_result()
    if problem == 'quote':
        data['nodes'][0]['quotes'] = ['原文没有这句话']
    elif problem == 'expiration':
        data['expirations'] = [{'record_id': 'missing', 'at_event': '甲接到乙后离开', 'reason': '无目标'}]
    elif problem == 'heading':
        data['nodes'][2].update(name='远行', quotes=[heading])
        data['nodes'][3]['big_event'] = '远行'
    else:
        data['properties'][1].update(property=problem, value_type='either')
    before = copy.deepcopy(project.graph_snapshot())
    assert not project.preview_extraction(sid, data)['valid']
    assert project.submit_extraction(sid, data)['status'] == 'rejected'
    assert project.graph_snapshot() == before


def test_body_supported_title_and_concrete_directed_relationship_are_allowed(project):
    sid = project.import_source('合成原文', '第一卷 接人\n' + TEXT)
    result = project.submit_extraction(sid, draft_result())
    assert result['status'] == 'accepted'
    names = {n['id']: n['name'] for n in project.graph_snapshot()['nodes']}
    friend = next(r for r in project.property_records() if r.get('property_id') == '朋友')
    assert names[friend['node_id']] == '甲' and names[friend['value']] == '乙'


def test_unicode_range_and_structured_values_are_checked_without_hashing(project):
    from narrative.extraction_draft import content_diagnostics
    for value in ({'detail': '合成值'}, ['合成值']):
        data = draft_result()
        data['properties'].append({'node': '甲', 'property': '资料', 'value_type': 'any', 'value': value})
        assert content_diagnostics(data, TEXT) == []
    sid = project.import_source('合成原文', '😀前缀\n' + TEXT)
    assert project.preview_extraction(sid, draft_result(), start=4)['valid']
    assert project.submit_extraction(sid, draft_result(), start=4)['status'] == 'accepted'
    plan = next(row for row in project.property_records() if row.get('property_id') == '计划' and row['kind'] == 'property_value')
    assert plan['disclosure']['start'] == 4
    with pytest.raises(ValueError):
        project.submit_extraction(sid, draft_result(), start=-1)
