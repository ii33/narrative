"""Evidence-based event identity through public Operations, with synthetic sources."""
from __future__ import annotations

import copy
import json

import pytest
from agent_fixtures import project as project

Q1 = '甲握住乙的手。'
Q2 = '两人握手致意。'
Q3 = '甲向乙点头。'
PREFIX = '😀序。\n'
TEXT = PREFIX + Q1 + Q2 + Q3 + '翌日' + Q1 + Q2
FIRST_DAY_END = TEXT.index('翌日')


def draft(quotes=None, *, name='甲乙握手', new_occurrence=False, existing_id=None, extra_event=False):
    event = {'type': 'event', 'name': name, 'scene': '会面', 'participants': ['甲', '乙'],
             'quotes': quotes or [Q1, Q2]}
    if new_occurrence:
        event['new_occurrence'] = True
    if existing_id:
        event['existing_id'] = existing_id
    nodes = [{'type': 'person', 'name': '甲', 'quotes': [Q1]},
             {'type': 'person', 'name': '乙', 'quotes': [Q1]},
             {'type': 'scene', 'name': '会面', 'quotes': [Q1]}, event]
    if extra_event:
        nodes.append({'type': 'event', 'name': '甲向乙点头', 'scene': '会面',
                      'participants': ['甲', '乙'], 'quotes': [Q3]})
    return {'nodes': nodes, 'properties': [], 'uncertainties': [], 'exclusions': []}


def node(ops, name):
    return next(row for row in ops.graph_snapshot()['nodes'] if row['name'] == name)


def events(ops):
    return [row for row in ops.graph_snapshot()['nodes'] if row['node_type'] == 'event']


def seed(ops, payload=None):
    sid = ops.import_source('合成身份原文', TEXT)
    assert ops.submit_extraction(sid, payload or draft(), end=FIRST_DAY_END)['status'] == 'accepted'
    return sid



def submit_first_day(ops, sid, payload):
    return ops.submit_extraction(sid, payload, end=FIRST_DAY_END)


def preview_first_day(ops, sid, payload):
    return ops.preview_extraction(sid, payload, end=FIRST_DAY_END)

def test_new_task_reuses_event_with_different_first_quote_and_exact_shared_span(project):
    sid = seed(project)
    original = copy.deepcopy(node(project, '甲乙握手'))
    # The first task is over: a fresh Operations instance represents a later task.
    from narrative.operations import Operations
    from narrative.project_package import layout

    later = Operations.open_graph_project(layout(project.project_info().path).entry)
    result = later.submit_extraction(sid, draft([Q2, '乙的手']), end=FIRST_DAY_END)
    assert result['status'] == 'accepted'
    updated = node(later, '甲乙握手')
    assert len(events(later)) == 1 and updated['id'] == original['id']
    assert updated['evidence'] == original['evidence'] and updated['disclosure'] == original['disclosure']
    spans = updated['evidence_spans']
    assert {span['quote'] for span in spans} == {Q1, Q2, '乙的手'}
    assert all(TEXT[span['start']:span['end']] == span['quote'] and span['source_id'] == sid for span in spans)
    assert next(span for span in spans if span['quote'] == Q1)['start'] == len(PREFIX)


def test_explicit_existing_id_can_enrich_without_shared_quote_and_scene_keeps_children(project):
    sid = seed(project)
    event_id = node(project, '甲乙握手')['id']
    result = submit_first_day(project, sid, draft(['乙的手'], existing_id=event_id, extra_event=True))
    assert result['status'] == 'accepted'
    assert len(events(project)) == 2
    other_id = node(project, '甲向乙点头')['id']
    assert set(node(project, '会面')['events']) == {event_id, other_id}
    assert submit_first_day(project, sid, draft([Q2], existing_id=event_id))['status'] in {'accepted', 'unchanged'}
    assert set(node(project, '会面')['events']) == {event_id, other_id}
    assert len(events(project)) == 2


def test_same_name_at_a_later_occurrence_needs_explicit_new_occurrence(project):
    sid = seed(project)
    begin = TEXT.index('翌日')
    before = copy.deepcopy(project.graph_snapshot())
    payload = draft(['翌日' + Q1, Q2])
    assert not project.preview_extraction(sid, payload, start=begin)['valid']
    assert project.submit_extraction(sid, payload, start=begin)['status'] == 'staged'
    assert project.graph_snapshot() == before
    payload['nodes'][-1]['new_occurrence'] = True
    assert project.submit_extraction(sid, payload, start=begin)['status'] == 'accepted'
    assert len(events(project)) == 2
    positions = [{span['start'] for span in event['evidence_spans']} for event in events(project)]
    assert any(all(start < begin for start in group) for group in positions)
    assert any(all(start >= begin for start in group) for group in positions)


def test_different_event_names_sharing_the_same_paragraph_are_not_merged(project):
    paragraph = Q1 + Q2 + Q3
    sid = seed(project, draft([paragraph]))
    assert submit_first_day(project, sid, draft([paragraph], name='甲向乙点头'))['status'] == 'accepted'
    assert {event['name'] for event in events(project)} == {'甲乙握手', '甲向乙点头'}
    assert len({event['id'] for event in events(project)}) == 2


def test_ambiguous_exact_evidence_requires_an_explicit_existing_id_without_writing(project):
    sid = seed(project)
    duplicate = copy.deepcopy(node(project, '甲乙握手'))
    duplicate['id'] = 'E99'
    scene = copy.deepcopy(node(project, '会面'))
    scene['events'].append('E99')
    assert project.apply_changes([duplicate, scene], [], project.graph_project_info()['revision'], reason='合成工程修订')['status'] == 'accepted'
    assert len(events(project)) == 2
    before = copy.deepcopy(project.graph_snapshot())
    # A new evidence variant avoids the original submission-id idempotence cache.
    payload = draft([Q2, '乙的手'])
    preview = preview_first_day(project, sid, payload)
    assert not preview['valid'] and preview['diagnostics']
    assert submit_first_day(project, sid, payload)['status'] == 'staged'
    assert project.graph_snapshot() == before
    target = events(project)[0]['id']
    payload['nodes'][-1]['existing_id'] = target
    assert submit_first_day(project, sid, payload)['status'] == 'accepted'
    assert len(events(project)) == 2


def test_old_nodes_without_full_spans_do_not_read_or_backfill_historical_raw(project):
    from narrative.project_package import layout

    sid = seed(project, draft([Q1]))
    legacy = copy.deepcopy(node(project, '甲乙握手'))
    legacy.pop('evidence_spans', None)
    assert project.apply_changes([legacy], [], project.graph_project_info()['revision'], reason='合成工程修订')['status'] == 'accepted'
    assert not node(project, '甲乙握手').get('evidence_spans')
    raw = layout(project.project_info().path).extraction / 'assistant/tasks/synthetic-legacy/raw.txt'
    raw.parent.mkdir(parents=True)
    content = json.dumps(draft([Q1, Q2, '乙的手']), ensure_ascii=False).encode()
    raw.write_bytes(content)
    before = copy.deepcopy(project.graph_snapshot())
    assert submit_first_day(project, sid, draft([Q2]))['status'] == 'staged'
    assert project.graph_snapshot() == before and raw.read_bytes() == content
    assert submit_first_day(project, sid, draft([Q2], existing_id=legacy['id']))['status'] == 'accepted'
    assert not any(span['quote'] == '乙的手' for span in node(project, '甲乙握手')['evidence_spans'])


@pytest.mark.parametrize('problem', ['missing', 'wrong_type', 'wrong_context', 'retracted'])
def test_invalid_existing_id_rejects_the_whole_extraction_without_pending(project, problem):
    sid = seed(project)
    target = copy.deepcopy(node(project, '甲乙握手'))
    payload = draft([Q2, '乙的手'])
    if problem == 'missing':
        payload['nodes'][-1]['existing_id'] = 'E-missing'
    elif problem == 'wrong_type':
        payload['nodes'][-1]['existing_id'] = node(project, '甲')['id']
    elif problem == 'wrong_context':
        payload['nodes'][-1].update(existing_id=target['id'], context_id='other-context')
    else:
        target['retracted'] = True
        assert project.apply_changes([target], [], project.graph_project_info()['revision'], reason='合成工程修订')['status'] == 'accepted'
        payload['nodes'][-1]['existing_id'] = target['id']
    payload['nodes'].append({'type': 'item', 'name': '手', 'quotes': ['手']})
    before = copy.deepcopy(project.graph_snapshot())
    records = copy.deepcopy(project.property_records())
    assert not preview_first_day(project, sid, payload)['valid']
    assert submit_first_day(project, sid, payload)['status'] == 'staged'
    assert project.graph_snapshot() == before
    assert project.property_records() == records


@pytest.mark.parametrize('problem', ['quote', 'range', 'source'])
def test_formal_evidence_span_precheck_rejects_graph_and_property_batch_atomically(project, problem):
    seed(project)
    bad = copy.deepcopy(node(project, '甲乙握手'))
    span = bad['evidence_spans'][0]
    if problem == 'quote':
        span['quote'] = '原文没有这句'
    elif problem == 'range':
        span['end'] = len(TEXT) + 1
    else:
        span['source_id'] = 'missing-source'
    good = copy.deepcopy(node(project, '甲'))
    good.update(id='P-new', name='另一个合成人物')
    records = [{'kind': 'property_definition', 'id': 'rollback-probe', 'value_type': 'string', 'cardinality': 'one'}]
    revision = project.graph_project_info()['revision']
    before = copy.deepcopy(project.graph_snapshot())
    properties = copy.deepcopy(project.property_records())
    assert any(row['severity'] == 'error' for row in project.change_precheck([bad, good], records, revision))
    staged = project.apply_changes([bad, good], records, revision, reason="非法证据原子暂存")
    assert staged["status"] == "staged" and staged["problems"]
    assert project.graph_snapshot() == before
    assert project.property_records() == properties
    assert project.graph_project_info()['revision'] == revision


def test_new_occurrence_conflicting_with_one_exact_existing_event_is_not_silently_ignored(project):
    sid = seed(project)
    payload = draft(new_occurrence=True)
    before = copy.deepcopy(project.graph_snapshot())
    preview = preview_first_day(project, sid, payload)
    assert not preview['valid'] and preview['diagnostics']
    assert submit_first_day(project, sid, payload)['status'] == 'staged'
    assert project.graph_snapshot() == before


@pytest.mark.parametrize('context_input', ['omitted', 'scene_conflict', 'participants_conflict'])
def test_legacy_primary_exact_match_does_not_invent_missing_or_override_conflicting_context(project, context_input):
    sid = seed(project, draft([Q1]))
    legacy = copy.deepcopy(node(project, '甲乙握手'))
    legacy.pop('evidence_spans', None)
    assert project.apply_changes([legacy], [], project.graph_project_info()['revision'], reason='合成工程修订')['status'] == 'accepted'
    event = {'type': 'event', 'name': '甲乙握手', 'quotes': [Q1]}
    nodes = [event]
    if context_input == 'scene_conflict':
        event['scene'] = '另一场景'
        nodes.append({'type': 'scene', 'name': '另一场景', 'quotes': [Q1]})
    elif context_input == 'participants_conflict':
        event['participants'] = ['甲']
    payload = {'nodes': nodes, 'properties': []}
    before = copy.deepcopy(project.graph_snapshot())
    result = submit_first_day(project, sid, payload)
    if context_input == 'omitted':
        assert result['status'] in {'accepted', 'unchanged'}
        assert len(events(project)) == 1 and events(project)[0]['id'] == legacy['id']
        assert events(project)[0]['evidence'] == legacy['evidence']
    else:
        assert result['status'] == 'staged'
        assert project.graph_snapshot() == before


def test_repeated_quote_in_a_wide_task_is_not_arbitrarily_used_as_shared_event_identity(project):
    sid = project.import_source('重复原句合成原文', TEXT)
    assert project.submit_extraction(sid, draft(), end=TEXT.index('翌日'))['status'] == 'accepted'
    before = copy.deepcopy(project.graph_snapshot())
    later = draft(['翌日' + Q1, Q2])
    # Q2 exists twice: selecting its first occurrence is not proof that the
    # clearly later occurrence is the same event as the previously stored one.
    preview = project.preview_extraction(sid, later)
    assert not preview['valid'] and preview['diagnostics']
    assert project.submit_extraction(sid, later)['status'] == 'staged'
    assert project.graph_snapshot() == before
    later['nodes'][-1]['new_occurrence'] = True
    assert project.submit_extraction(sid, later, start=FIRST_DAY_END)['status'] == 'accepted'
    assert len(events(project)) == 2
    new_event = next(event for event in events(project) if event['id'] != next(event['id'] for event in before['nodes'] if event['node_type'] == 'event'))
    assert all(span['start'] >= FIRST_DAY_END for span in new_event['evidence_spans'])
