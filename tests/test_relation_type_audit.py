"""Relation display warnings distinguish literal IDs from typed references."""
from copy import deepcopy

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project


@pytest.mark.parametrize(('value_type', 'category', 'expected'), [
    ('string', '关系', True), ('any', '关系', True),
    ('string', '其他', False), ('any', '其他', False),
    ('node', '关系', False), ('node', None, False), ('either', '关系', False),
])
def test_relation_id_literal_warns_only_for_explicit_relation_category(project, value_type, category, expected):
    sid = project.import_source('合成关系原文', TEXT)
    assert project.submit_extraction(sid, draft_result())['status'] == 'accepted'
    nodes = {node['name']: node['id'] for node in project.graph_snapshot()['nodes']}
    record = {'kind': 'property_change', 'op': 'set', 'sequence': 0, 'node_id': nodes['甲'], 'property_id': '查证对象',
              'value': nodes['乙'], 'event_id': nodes['甲约好接乙'],
              'evidence': {'source_id': sid, 'quote': '甲约好接乙。'}}
    if category is not None:
        record['category'] = category
    definition = {'kind': 'property_definition', 'id': '查证对象', 'value_type': value_type, 'cardinality': 'one'}
    assert project.apply_changes([], [definition, record], project.graph_project_info()['revision'], reason='明确类型和分类')['status'] == 'accepted'
    before, rows = deepcopy(project.graph_snapshot()), deepcopy(project.property_records())
    report = project.property_audit()
    findings = [row for row in report['issues'] if row['code'] == 'relation_untyped_reference']
    assert bool(findings) == expected
    if expected:
        assert findings[0]['severity'] == 'warning'
        assert findings[0]['node_id'] == nodes['甲'] and findings[0]['record_ids']
        assert '不会显示' in findings[0]['message']
        checks = project.check_project()['issues']
        assert any(row['code'] == 'relation_untyped_reference' for row in checks)
    if value_type in {'node', 'either'}:
        from narrative.project_package import layout
        from narrative.tool_api import call
        result = call('relation_map', project=layout(project.project_info().path).entry)
        assert any(edge['kind'] == '查证对象' for edge in result['edges'])
    assert project.graph_snapshot() == before and project.property_records() == rows
