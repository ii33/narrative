"""Storyboard tool actions state the same strict flat contract as Operations."""
import copy

import pytest
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board

from narrative.storyboard_contract import (
    ACTION_FIELDS,
    ACTION_SCHEMAS,
    storyboard_change_schema,
    validate_action_shape,
)
from narrative.storyboard_operations import _FIELDS
from narrative.storyboard_timing import STAGING_FIELDS


def test_contract_is_typed_closed_and_fields_match_business_operations():
    schema = storyboard_change_schema()
    assert len(schema['oneOf']) == len(ACTION_SCHEMAS)
    assert _FIELDS == ACTION_FIELDS
    assert all(ACTION_FIELDS[op] == fields for op, fields in STAGING_FIELDS.items())
    for action in schema['oneOf']:
        assert action['additionalProperties'] is False
        assert action['properties']['op']['const'] in _FIELDS
        assert 'op' in action['required']
    assert ACTION_SCHEMAS['set_action_keyframe']['required'] == ['op', 'object_id', 'time_ms']
    assert 'kind' not in ACTION_SCHEMAS['set_action_keyframe']['properties']
    assert 'keyframe' not in ACTION_SCHEMAS['set_keyframe']['properties']
    schema['oneOf'].clear()
    assert len(storyboard_change_schema()['oneOf']) == len(ACTION_SCHEMAS)


@pytest.mark.parametrize('invalid,diagnostic', [
    ({'op': 'set_action_keyframe', 'object_id': 'object-1', 'time_ms': 0, 'kind': 'action_keyframe'}, 'kind'),
    ({'op': 'set_keyframe', 'shot_id': 'shot-1', 'keyframe': {'offset_ms': 0, 'sketch_path': 'C:/x.png'}}, 'offset_ms'),
    ({'op': 'set_keyframe', 'shot_id': 'shot-1', 'offset_ms': 0}, '只能提供一个'),
    ({'op': 'create_object', 'name': '人物'}, 'kind'),
])
def test_flat_field_errors_are_specific_and_do_not_execute_any_batch_write(project, invalid, diagnostic):
    ops, unit, _ = setup_board(project)
    before = ops.read_storyboard(unit)
    original = copy.deepcopy(invalid)
    with pytest.raises(ValueError) as error:
        ops.apply_storyboard_changes(unit, before['revision'], [{'op': 'create_shot', 'title': '不应写入'}, invalid])
    message = str(error.value)
    assert invalid['op'] in message and diagnostic in message
    assert '允许字段' in message and '扁平示例' in message
    assert invalid == original and ops.read_storyboard(unit) == before


def test_flat_valid_shapes_are_not_rewritten():
    for change in [
        {'op': 'set_keyframe', 'shot_id': 'shot-1', 'offset_ms': 0, 'sketch_path': 'C:/x.png'},
        {'op': 'set_action_keyframe', 'object_id': 'object-1', 'time_ms': 0, 'x': .2},
    ]:
        before = copy.deepcopy(change)
        assert validate_action_shape(change) == change['op'] and change == before
