"""Deterministic phase-two shared marker and camera tests; no image service."""
import pytest
from PIL import Image
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board
from test_storyboard_timing import change

from narrative.operations import Operations
from narrative.storyboard_camera import CAMERA_FIELDS, PRESETS
from narrative.storyboard_contract import ACTION_FIELDS


def test_marker_move_previews_all_conflicts_preserves_free_items_and_ids(project, tmp_path):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_shot', 'title': '正面', 'duration_ms': 1000, 'duration_locked': True},
                               {'op': 'create_shot', 'title': '重看', 'duration_ms': 1000, 'action_start_ms': 200},
                               {'op': 'create_marker', 'name': '接触', 'time_ms': 600},
                               {'op': 'create_object', 'name': '人物', 'kind': 'person'}])
    first, second = [row['id'] for row in board['shots']]
    marker, obj = board['markers'][0]['id'], board['objects'][0]['id']
    image = tmp_path / 'sketch.png'
    Image.new('RGB', (8, 8)).save(image)
    board = change(ops, unit, [
        {'op': 'set_keyframe', 'shot_id': first, 'offset_ms': 0, 'sketch_path': str(image)},
        {'op': 'set_keyframe', 'shot_id': first, 'offset_ms': 600, 'sketch_path': str(image)},
        {'op': 'set_keyframe', 'shot_id': second, 'offset_ms': 400, 'sketch_path': str(image)},
        {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 600, 'x': .8},
    ])
    free, frame1 = [row['id'] for row in board['shots'][0]['keyframes']]
    frame2 = board['shots'][1]['keyframes'][0]['id']
    key = board['objects'][0]['keyframes'][0]['id']
    board = change(ops, unit, [
        {'op': 'bind_keyframe_marker', 'shot_id': first, 'frame_id': frame1, 'marker_id': marker},
        {'op': 'bind_keyframe_marker', 'shot_id': second, 'frame_id': frame2, 'marker_id': marker},
        {'op': 'bind_action_keyframe_marker', 'object_id': obj, 'keyframe_id': key, 'marker_id': marker},
    ])
    edit = [{'op': 'update_marker', 'marker_id': marker, 'time_ms': 700}]
    preview = ops.preview_storyboard_changes(unit, board['revision'], edit)
    assert preview['valid'] and {row['frame_id'] for row in preview['affected_frames']} >= {frame1, frame2}
    assert ops.read_storyboard(unit) == board
    moved = change(ops, unit, edit)
    assert moved['shots'][0]['keyframes'][0]['id'] == free and moved['shots'][0]['keyframes'][0]['offset_ms'] == 0
    assert moved['shots'][0]['keyframes'][1]['id'] == frame1 and moved['shots'][0]['keyframes'][1]['offset_ms'] == 700
    assert moved['objects'][0]['keyframes'][0]['id'] == key and moved['objects'][0]['keyframes'][0]['time_ms'] == 700
    edit = [{'op': 'update_marker', 'marker_id': marker, 'time_ms': 2000}]
    rejected = ops.preview_storyboard_changes(unit, moved['revision'], edit)
    assert not rejected['valid'] and len(rejected['conflicts']) == 2
    assert any(row['duration_locked'] for row in rejected['conflicts'])
    with pytest.raises(ValueError, match='超出'):
        change(ops, unit, edit)
    assert ops.read_storyboard(unit) == moved
    with pytest.raises(ValueError, match='命名时刻'):
        change(ops, unit, [{'op': 'delete_marker', 'marker_id': marker}])
    unbound = change(ops, unit, [{'op': 'bind_keyframe_marker', 'shot_id': first, 'frame_id': frame1, 'marker_id': None},
                               {'op': 'move_keyframe', 'shot_id': first, 'frame_id': frame1, 'offset_ms': 800}])
    assert unbound['shots'][0]['keyframes'][1]['offset_ms'] == 800


def test_camera_dolly_zoom_projection_and_saved_editable_presets(project, tmp_path):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_object', 'name': '主体', 'kind': 'person', 'x': .5, 'y': .5, 'height': .2},
                               {'op': 'create_shot', 'title': '推进变焦', 'duration_ms': 2001}])
    obj, shot = board['objects'][0]['id'], board['shots'][0]['id']
    board = change(ops, unit, [{'op': 'set_camera_preset', 'shot_id': shot, 'preset': 'dolly_zoom',
                               'target_object_id': obj, 'amount': .5, 'end_ms': 2000,
                               'start': {'x': .5, 'y': -.5, 'z': .1, 'fov_deg': 50}}])
    first, middle, last = [ops.preview_storyboard(unit, time)['camera'] for time in (0, 1000, 2000)]
    assert first['position']['y'] < middle['position']['y'] < last['position']['y']
    assert first['fov_deg'] < middle['fov_deg'] < last['fov_deg']
    assert first['projections'][0]['size_ratio'] == pytest.approx(last['projections'][0]['size_ratio'])
    assert middle['projections'][0]['screen_x'] == pytest.approx(.5)
    assert 'no_mesh' in middle['projection_model']
    key = board['shots'][0]['camera']['keys'][1]['id']
    board = change(ops, unit, [{'op': 'move_camera_keyframe', 'shot_id': shot, 'keyframe_id': key, 'offset_ms': 1800}])
    assert board['shots'][0]['camera']['keys'][1]['id'] == key
    preset = ops.save_storyboard_camera_preset('缓慢推进', {'preset': 'dolly_zoom', 'amount': .2}, 0, '项目复用')
    assert preset['presets'][0]['id'].startswith('camera-preset-')
    reopened = Operations.open_graph_project(tmp_path / 'writing.nproj')
    assert reopened.load_storyboard_camera_presets() == preset
    with pytest.raises(ValueError, match='刷新'):
        ops.save_storyboard_camera_preset('另一条', {'preset': 'fixed'}, 0, '过期')


@pytest.mark.parametrize('preset', PRESETS)
def test_all_presets_expand_to_editable_keys_and_reject_invalid_refs(project, preset):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_shot', 'title': '镜头'}, {'op': 'create_object', 'name': '人物', 'kind': 'person'}])
    shot, obj = board['shots'][0]['id'], board['objects'][0]['id']
    board = change(ops, unit, [{'op': 'set_camera_preset', 'shot_id': shot, 'preset': preset, 'target_object_id': obj}])
    assert len(board['shots'][0]['camera']['keys']) == 2
    assert ops.preview_storyboard(unit, 100)['camera']['frustum']['aspect_ratio'] == 16/9
    assert all(ACTION_FIELDS[op] == fields for op, fields in CAMERA_FIELDS.items())
    with pytest.raises(ValueError, match='不属于本场'):
        change(ops, unit, [{'op': 'delete_object', 'object_id': obj}])
    assert ops.read_storyboard(unit) == board


def test_marker_bound_reaction_entry_and_camera_follow_keep_action_clock(project):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_object', 'name': '行人', 'kind': 'person', 'x': .2, 'y': .5},
                               {'op': 'create_marker', 'name': '反应起点', 'time_ms': 500},
                               {'op': 'create_shot', 'title': '跟拍', 'duration_ms': 1001}])
    shot, obj, marker = board['shots'][0]['id'], board['objects'][0]['id'], board['markers'][0]['id']
    board = change(ops, unit, [
        {'op': 'update_shot', 'shot_id': shot, 'action_start_marker_id': marker},
        {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 0, 'x': 0, 'interpolation': 'linear'},
        {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 2000, 'x': 1},
        {'op': 'set_camera_preset', 'shot_id': shot, 'preset': 'follow', 'target_object_id': obj},
    ])
    first, last = [ops.preview_storyboard(unit, time) for time in (0, 1000)]
    assert first['action_ms'] == 500 and last['action_ms'] == 1500
    assert last['camera']['position']['x'] - first['camera']['position']['x'] == pytest.approx(.5)
    change(ops, unit, [{'op': 'update_marker', 'marker_id': marker, 'time_ms': 700}])
    assert ops.preview_storyboard(unit, 0)['action_ms'] == 700


def test_orbit_uses_arc_and_invalid_camera_edits_roll_back(project):
    import math
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_object', 'name': '主体', 'kind': 'prop', 'x': .5, 'y': .5},
                               {'op': 'create_shot', 'title': '环绕', 'duration_ms': 1001}])
    shot, obj = board['shots'][0]['id'], board['objects'][0]['id']
    board = change(ops, unit, [{'op': 'set_camera_preset', 'shot_id': shot, 'preset': 'orbit', 'amount': 90,
                               'target_object_id': obj, 'end_ms': 1000, 'start': {'x': .5, 'y': -.5, 'z': .1}}])
    mid = ops.preview_storyboard(unit, 500)['camera']['position']
    assert math.hypot(mid['x']-.5, mid['y']-.5) == pytest.approx(1)
    key = board['shots'][0]['camera']['keys'][1]['id']
    edits = [
        [{'op': 'move_camera_keyframe', 'shot_id': shot, 'keyframe_id': key, 'offset_ms': 1001}],
        [{'op': 'set_camera_keyframe', 'shot_id': shot, 'offset_ms': 500, 'fov_deg': 180}],
        [{'op': 'set_camera_preset', 'shot_id': shot, 'preset': 'fixed', 'start': {'invalid': 1}}],
        [{'op': 'update_shot', 'shot_id': shot, 'action_start_marker_id': False}],
    ]
    for edit in edits:
        with pytest.raises(ValueError):
            change(ops, unit, edit)
        assert ops.read_storyboard(unit) == board


def test_dolly_zoom_changes_background_perspective_without_using_render_depth_as_height(project):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_object', 'name': '主体', 'kind': 'person', 'x': .5, 'y': .5, 'height': .2, 'depth': 99},
                               {'op': 'create_object', 'name': '背景', 'kind': 'set', 'x': .5, 'y': 1, 'height': .2},
                               {'op': 'create_shot', 'title': '主体大小不变', 'duration_ms': 1001}])
    shot, obj = board['shots'][0]['id'], board['objects'][0]['id']
    change(ops, unit, [{'op': 'set_camera_preset', 'shot_id': shot, 'preset': 'dolly_zoom', 'target_object_id': obj,
                       'amount': .5, 'end_ms': 1000, 'start': {'x': .5, 'y': -.5, 'z': .1}}])
    first, last = [ops.preview_storyboard(unit, time)['camera']['projections'] for time in (0, 1000)]
    assert first[0]['size_ratio'] == pytest.approx(last[0]['size_ratio'])
    assert first[1]['size_ratio'] != pytest.approx(last[1]['size_ratio'])
    assert first[0]['screen_y'] == pytest.approx(.5)
