"""Synthetic legacy event duplicates and references, created only through Operations."""
from __future__ import annotations

import copy

from test_extraction_identity import Q1, Q2, Q3, TEXT, draft, node


def seed_event_duplicates(ops):
    sid = ops.import_source('合成旧重复事件原文', TEXT)
    assert ops.submit_extraction(sid, draft(), end=TEXT.index('翌日'))['status'] == 'accepted'
    target = copy.deepcopy(node(ops, '甲乙握手'))
    source = copy.deepcopy(target)
    target['source_marks'] = [{'source_id': sid, 'role': 'start', 'label': '原始开头'}]
    source.update(id='E2', evidence={'source_id': sid, 'quote': Q2},
                  disclosure={'source_id': sid, 'start': TEXT.index(Q2)},
                  evidence_spans=[{'source_id': sid, 'start': TEXT.index(Q2), 'end': TEXT.index(Q2) + len(Q2), 'quote': Q2}],
                  source_marks=[{'source_id': sid, 'role': 'end', 'label': '结尾保留E2文本'}])
    third = copy.deepcopy(target)
    third.update(id='E3', name='甲向乙点头', predecessors=['E2'],
                 evidence={'source_id': sid, 'quote': Q3}, disclosure={'source_id': sid, 'start': TEXT.index(Q3)},
                 evidence_spans=[{'source_id': sid, 'start': TEXT.index(Q3), 'end': TEXT.index(Q3) + len(Q3), 'quote': Q3}])
    third['source_marks'] = []
    source['successors'] = ['E3']
    scene = copy.deepcopy(node(ops, '会面'))
    scene['events'] = [target['id'], source['id'], third['id']]
    person_a, person_b = node(ops, '甲')['id'], node(ops, '乙')['id']
    definitions = [
        {'kind': 'property_definition', 'id': '经历', 'value_type': 'string', 'cardinality': 'one'},
        {'kind': 'property_definition', 'id': '标注', 'value_type': 'string', 'cardinality': 'one'},
        {'kind': 'property_definition', 'id': '事件指向', 'value_type': 'node', 'cardinality': 'one'},
        {'kind': 'property_definition', 'id': '原编号文字', 'value_type': 'string', 'cardinality': 'one'},
        {'kind': 'property_definition', 'id': '看法', 'value_type': 'string', 'cardinality': 'one'},
        {'kind': 'property_definition', 'id': '结构信息', 'value_type': 'any', 'cardinality': 'one'},
    ]
    disclosure = {'source_id': sid, 'start': TEXT.index(Q1)}
    base = {'kind': 'property_value', 'event_id': source['id'], 'disclosure': disclosure,
            'evidence': {'source_id': sid, 'quote': Q1 + Q2 + Q3}}
    records = [
        base | {'id': 'v_anchor', 'node_id': person_a, 'property_id': '经历', 'value': '与乙握手',
                'applies_to': source['id'], 'related_nodes': [person_b, source['id']]},
        base | {'id': 'v_subject', 'node_id': source['id'], 'property_id': '标注', 'value': '同一事件旧副本'},
        base | {'id': 'v_target', 'node_id': person_a, 'property_id': '事件指向', 'value': source['id']},
        base | {'id': 'v_literal', 'node_id': person_a, 'property_id': '原编号文字', 'value': source['id']},
        base | {'id': 'v_json', 'node_id': person_a, 'property_id': '结构信息',
                'value': {'event_id': 'E2', 'holder_id': 'E2', 'events': ['E2'], 'nested': [{'node_id': 'E2'}]}},
        base | {'id': 'v_cognition', 'node_id': person_a, 'property_id': '看法', 'value': 'E2 是文字，不应替换',
                'scope': 'cognition', 'holder_id': person_b},
    ]
    rev = ops.graph_project_info()['revision']
    committed = ops.apply_changes([target, source, third, scene], definitions + records, rev,
                                  reason='隔离工程验收种子：模拟历史重复，不是模型输出')
    assert committed['status'] == 'accepted'
    return {'source_id': sid, 'target': target['id'], 'source': source['id'], 'third': third['id'],
            'scene': scene['id'], 'person_a': person_a, 'person_b': person_b,
            'revision': ops.graph_project_info()['revision'],
            'proof': [{'source_id': sid, 'start': TEXT.index(Q2), 'end': TEXT.index(Q2) + len(Q2), 'quote': Q2}]}
