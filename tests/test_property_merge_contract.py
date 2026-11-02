"""Physical property merges preserve the definition contract in both directions."""
from copy import deepcopy

import pytest
from agent_fixtures import project as project


@pytest.mark.parametrize(('field', 'left', 'right'), [
    ('symmetric', False, True), ('symmetric', True, False),
    ('value_type', 'node', 'either'), ('cardinality', 'one', 'many'),
    ('semantic', None, 'item_holder'), ('semantic', 'item_holder', None),
    ('target_node_types', ['person'], ['group']),
    ('target_node_types', [], ['person']),
])
def test_conflicting_definition_contract_is_atomically_rejected(project, field, left, right):
    definitions = [{'kind': 'property_definition', 'id': identifier,
                    'value_type': 'node', 'cardinality': 'one', field: value}
                   for identifier, value in [('甲关系', left), ('乙关系', right)]]
    assert project.apply_changes([], definitions, project.graph_project_info()['revision'], reason='合成定义')['status'] == 'accepted'
    snapshot, records = deepcopy(project.graph_snapshot()), deepcopy(project.property_records())
    with pytest.raises(ValueError, match='属性定义不兼容.*' + field):
        project.merge_properties('甲关系', '乙关系', '尝试合并不同定义', snapshot['revision'])
    assert project.graph_snapshot() == snapshot
    assert project.property_records() == records


def test_same_effective_contract_can_merge_with_default_false_and_unordered_target_types(project):
    definitions = [
        {'kind': 'property_definition', 'id': '甲关系', 'value_type': 'node', 'cardinality': 'one',
         'target_node_types': ['person', 'group']},
        {'kind': 'property_definition', 'id': '乙关系', 'value_type': 'node', 'cardinality': 'one',
         'symmetric': False, 'target_node_types': ['group', 'person']},
    ]
    assert project.apply_changes([], definitions, project.graph_project_info()['revision'], reason='同定义不同名称')['status'] == 'accepted'
    result = project.merge_properties('甲关系', '乙关系', '同义且同结构可合并', project.graph_project_info()['revision'])
    assert result['status'] == 'accepted'
    rows = {row['id']: row for row in project.property_records()}
    assert '甲关系' not in rows and rows['乙关系']['symmetric'] is False


def test_explicit_definition_correction_can_precede_merge(project):
    source = {'kind': 'property_definition', 'id': '甲关系', 'value_type': 'node',
              'cardinality': 'one', 'symmetric': False}
    target = source | {'id': '乙关系', 'symmetric': True}
    assert project.apply_changes([], [source, target], project.graph_project_info()['revision'], reason='两种原始定义')['status'] == 'accepted'
    revision = project.graph_project_info()['revision']
    with pytest.raises(ValueError, match='symmetric'):
        project.merge_properties('甲关系', '乙关系', '不能隐式更改方向性', revision)
    assert project.apply_changes([], [source | {'symmetric': True}], revision, reason='已查证后明确修正定义')['status'] == 'accepted'
    assert project.merge_properties('甲关系', '乙关系', '结构已显式统一', project.graph_project_info()['revision'])['status'] == 'accepted'
