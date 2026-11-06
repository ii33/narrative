"""Scene staging and millisecond edit timing use actual Operations transactions."""
import pytest
from PIL import Image
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board

from narrative.operations import Operations


def change(ops, unit, changes):
    return ops.apply_storyboard_changes(unit, ops.read_storyboard(unit)['revision'], changes)


def test_shared_action_preview_cut_hold_linear_reorder_and_reopen(project, tmp_path):
    ops, unit, block = setup_board(project)
    board = change(ops, unit, [
        {'op': 'set_fps', 'fps': 30},
        {'op': 'create_object', 'name': '人物', 'kind': 'person', 'x': 0, 'y': 0},
        {'op': 'create_shot', 'title': '全景', 'duration_ms': 1000, 'source_block_ids': [block]},
        {'op': 'create_shot', 'title': '反打重看', 'duration_ms': 1000, 'action_start_ms': 500},
    ])
    obj = board['objects'][0]['id']
    one, two = [shot['id'] for shot in board['shots']]
    image = tmp_path / 'frame.png'
    Image.new('RGB', (16, 9)).save(image)
    board = change(ops, unit, [
        {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 0, 'x': 0, 'interpolation': 'linear', 'state': {'hand': 'open'}},
        {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 1000, 'x': 1, 'state': {'hand': 'closed'}},
        {'op': 'set_keyframe', 'shot_id': one, 'offset_ms': 0, 'sketch_path': str(image)},
    ])
    start = ops.preview_storyboard(unit, 0)
    midway = ops.preview_storyboard(unit, 500)
    assert start['frame_id'] == midway['frame_id']
    assert midway['objects'][0]['x'] == .5 and midway['objects'][0]['state'] == {'hand': 'open'}
    assert ops.preview_storyboard(unit, 999)['shot_id'] == one
    cut = ops.preview_storyboard(unit, 1000)
    assert (cut['shot_id'], cut['local_ms'], cut['action_ms'], cut['frame_id']) == (two, 0, 500, None)
    assert cut['objects'][0]['x'] == .5
    end = ops.preview_storyboard(unit, 2000)
    assert end['at_end'] and end['local_ms'] == 1000 and end['action_ms'] == 1500
    assert end == ops.preview_storyboard(unit, 5000)
    board = change(ops, unit, [{'op': 'move_shot', 'shot_id': two, 'index': 0}])
    assert ops.preview_storyboard(unit, 0)['action_ms'] == 500
    reopened = Operations.open_graph_project(tmp_path / 'writing.nproj')
    assert reopened.preview_storyboard(unit, 0) == ops.preview_storyboard(unit, 0)
    assert reopened.read_storyboard(unit)['fps'] == 30


def test_frame_and_action_key_move_preserve_identity_and_final_batch_validation(project, tmp_path):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_shot', 'title': '镜头', 'duration_ms': 2000},
                                {'op': 'create_object', 'name': '道具', 'kind': 'prop'}])
    shot, obj = board['shots'][0]['id'], board['objects'][0]['id']
    image = tmp_path / 'frame.png'
    Image.new('RGB', (8, 8)).save(image)
    board = change(ops, unit, [{'op': 'set_keyframe', 'shot_id': shot, 'offset_ms': 1500, 'sketch_path': str(image)},
                               {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 200, 'x': .2}])
    frame, key = board['shots'][0]['keyframes'][0]['id'], board['objects'][0]['keyframes'][0]['id']
    with pytest.raises(ValueError, match=frame + '.*1500.*1000'):
        change(ops, unit, [{'op': 'update_shot', 'shot_id': shot, 'duration_ms': 1000}])
    assert ops.read_storyboard(unit) == board
    board = change(ops, unit, [{'op': 'update_shot', 'shot_id': shot, 'duration_ms': 1000},
                               {'op': 'move_keyframe', 'shot_id': shot, 'frame_id': frame, 'offset_ms': 900},
                               {'op': 'move_action_keyframe', 'object_id': obj, 'keyframe_id': key, 'time_ms': 400}])
    assert board['shots'][0]['keyframes'][0]['id'] == frame
    assert board['objects'][0]['keyframes'][0]['id'] == key
    assert ops.preview_storyboard(unit, 899)['frame_id'] is None
    assert ops.preview_storyboard(unit, 900)['frame_id'] == frame
    with pytest.raises(ValueError, match='刷新'):
        ops.apply_storyboard_changes(unit, 0, [{'op': 'set_fps', 'fps': 24}])


def test_parent_transform_and_invalid_references_are_atomic(project):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_object', 'name': '车', 'kind': 'prop', 'x': .2, 'y': .3, 'rotation': 90},
                               {'op': 'create_shot', 'title': '全景'}])
    parent = board['objects'][0]['id']
    board = change(ops, unit, [{'op': 'create_object', 'name': '乘客', 'kind': 'person', 'x': .1, 'y': 0, 'parent_id': parent}])
    child = board['objects'][1]['id']
    state = ops.preview_storyboard(unit, 0)['objects'][1]
    assert state['world_x'] == pytest.approx(.2) and state['world_y'] == pytest.approx(.4)
    for changes, error in [
        ([{'op': 'update_object', 'object_id': parent, 'parent_id': child}], '循环'),
        ([{'op': 'delete_object', 'object_id': parent}], '本场'),
        ([{'op': 'update_object', 'object_id': child, 'asset_id': 'missing'}], '资产'),
        ([{'op': 'set_fps', 'fps': True}], '整数'),
        ([{'op': 'update_object', 'object_id': child, 'x': 1.1}], '0..1'),
        ([{'op': 'update_object', 'object_id': child, 'state': {'value': float('nan')}}], '有限JSON'),
        ([{'op': 'set_action_keyframe', 'object_id': 'foreign', 'time_ms': 0}], '本场'),
    ]:
        with pytest.raises(ValueError, match=error):
            change(ops, unit, changes)
        assert ops.read_storyboard(unit) == board
    with pytest.raises(ValueError, match='整数'):
        ops.preview_storyboard(unit, -1)


def test_new_rotation_key_samples_current_local_motion_without_jumping(project):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_shot', 'title': '行走'},
                               {'op': 'create_object', 'name': '人物', 'kind': 'person', 'x': 0, 'y': .2}])
    obj = board['objects'][0]['id']
    change(ops, unit, [{'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 0, 'x': 0, 'interpolation': 'linear'},
                       {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 1000, 'x': 1}])
    board = change(ops, unit, [{'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 500, 'rotation': 90}])
    key = board['objects'][0]['keyframes'][1]
    assert (key['x'], key['y'], key['rotation'], key['interpolation']) == (.5, .2, 90, 'linear')
    assert ops.preview_storyboard(unit, 750)['objects'][0]['x'] == .75


def test_duplicate_keys_hold_and_approval_reject_without_partial_writes(project, tmp_path):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_shot', 'title': '停顿', 'duration_ms': 1000},
                               {'op': 'create_object', 'name': '布景', 'kind': 'set', 'x': .1}])
    shot, obj = board['shots'][0]['id'], board['objects'][0]['id']
    image = tmp_path / 'still.png'
    Image.new('RGB', (8, 8)).save(image)
    board = change(ops, unit, [{'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 0, 'x': .2},
                               {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 500, 'x': .8},
                               {'op': 'set_keyframe', 'shot_id': shot, 'offset_ms': 0, 'sketch_path': str(image)}])
    assert ops.preview_storyboard(unit, 499)['objects'][0]['x'] == .2
    assert ops.preview_storyboard(unit, 500)['objects'][0]['x'] == .8
    key = board['objects'][0]['keyframes'][1]['id']
    for action in [
        {'op': 'move_action_keyframe', 'object_id': obj, 'keyframe_id': key, 'time_ms': 0},
        {'op': 'update_shot', 'shot_id': shot, 'action_start_ms': True},
        {'op': 'update_object', 'object_id': obj, 'state': {'bad': float('nan')}},
        {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 250, 'interpolation': 'spline'},
    ]:
        with pytest.raises(ValueError):
            change(ops, unit, [action])
        assert ops.read_storyboard(unit) == board
    board = change(ops, unit, [{'op': 'set_approved', 'approved': True}])
    with pytest.raises(ValueError, match='解除定案'):
        change(ops, unit, [{'op': 'set_fps', 'fps': 25}])
    assert ops.read_storyboard(unit) == board
