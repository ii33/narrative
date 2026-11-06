"""Camera overlays remain deterministic and independent of underlying editable keys."""
import copy

import pytest
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board
from test_storyboard_timing import change


@pytest.mark.parametrize('easing,quarter,mid', [('linear', .25, .5), ('ease_in', .0625, .25),
                                             ('ease_out', .4375, .75), ('ease_in_out', .125, .5)])
def test_left_camera_key_curve_controls_position_and_focus(project, easing, quarter, mid):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_shot', 'title': '运动', 'duration_ms': 1001}])
    shot = board['shots'][0]['id']
    change(ops, unit, [{'op': 'set_camera_keyframe', 'shot_id': shot, 'offset_ms': 0, 'x': 0, 'focus_distance': 1,
                       'interpolation': 'linear', 'easing': easing},
                      {'op': 'set_camera_keyframe', 'shot_id': shot, 'offset_ms': 1000, 'x': 1, 'focus_distance': 3}])
    first = ops.preview_storyboard(unit, 250)['camera']
    middle = ops.preview_storyboard(unit, 500)['camera']
    assert first['position']['x'] == pytest.approx(quarter)
    assert middle['position']['x'] == pytest.approx(mid)
    assert first['focus_distance'] == pytest.approx(1 + 2*quarter)


def test_repeatable_handheld_and_focus_do_not_change_preset_keys_or_aim(project):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_shot', 'title': '对焦', 'duration_ms': 1001},
                               {'op': 'create_object', 'name': '近处', 'kind': 'prop', 'x': .5, 'y': .5},
                               {'op': 'create_object', 'name': '远处', 'kind': 'prop', 'x': .5, 'y': 1}])
    shot = board['shots'][0]['id']
    near = board['objects'][0]['id']
    board = change(ops, unit, [{'op': 'set_camera_preset', 'shot_id': shot, 'preset': 'truck', 'amount': .2,
                               'start': {'x': .5, 'y': -1, 'z': .1}}])
    keys = copy.deepcopy(board['shots'][0]['camera']['keys'])
    baseline = ops.preview_storyboard(unit, 0)['camera']
    board = change(ops, unit, [{'op': 'set_camera_overlays', 'shot_id': shot, 'focus_target_object_id': near}])
    focus = ops.preview_storyboard(unit, 0)['camera']
    assert focus['yaw'] == baseline['yaw'] and focus['pitch'] == baseline['pitch']
    assert focus['focus_distance'] == pytest.approx(1.5)
    assert focus['projections'][0]['in_focus'] and not focus['projections'][1]['in_focus']
    assert 'not_optical_dof' in focus['focus_model']
    handheld = {'amplitude': .01, 'frequency_hz': 2, 'seed': 12}
    board = change(ops, unit, [{'op': 'set_camera_overlays', 'shot_id': shot, 'handheld': handheld}])
    one = ops.preview_storyboard(unit, 250)['camera']
    assert ops.preview_storyboard(unit, 250)['camera'] == one
    assert ops.preview_storyboard(unit, 251)['camera']['position'] != one['position']
    assert board['shots'][0]['camera']['keys'] == keys and board['shots'][0]['camera']['preset'] == 'truck'
    with pytest.raises(ValueError, match='对焦目标'):
        change(ops, unit, [{'op': 'delete_object', 'object_id': near}])
    assert ops.read_storyboard(unit) == board
    board = change(ops, unit, [{'op': 'set_camera_overlays', 'shot_id': shot, 'focus_target_object_id': None, 'handheld': None}])
    assert board['shots'][0]['camera']['keys'] == keys
    assert ops.preview_storyboard(unit, 0)['camera']['position'] == baseline['position']


def test_overlay_presets_persist_and_invalid_values_reject_atomically(project):
    ops, unit, _ = setup_board(project)
    handheld = {'amplitude': .02, 'frequency_hz': 1.5, 'seed': 4}
    settings = {'preset': 'dolly', 'amount': .3, 'easing': 'ease_in_out', 'handheld': handheld,
                'start': {'focus_distance': 2}, 'end': {'focus_distance': 3}}
    saved = ops.save_storyboard_camera_preset('手持推进', settings, 0, '复用')
    assert saved['presets'][0]['settings'] == settings
    board = change(ops, unit, [{'op': 'create_shot', 'title': '应用'}])
    shot = board['shots'][0]['id']
    board = change(ops, unit, [{'op': 'set_camera_preset', 'shot_id': shot, **settings}])
    assert board['shots'][0]['camera']['handheld'] == handheld
    assert board['shots'][0]['camera']['keys'][0]['easing'] == 'ease_in_out'
    for invalid in [
        {'op': 'set_camera_overlays', 'shot_id': shot, 'focus_target_object_id': 'foreign-object'},
        {'op': 'set_camera_overlays', 'shot_id': shot, 'handheld': handheld | {'amplitude': .1}},
        {'op': 'set_camera_overlays', 'shot_id': shot, 'handheld': handheld | {'seed': True}},
        {'op': 'set_camera_keyframe', 'shot_id': shot, 'offset_ms': 0, 'focus_distance': 0},
        {'op': 'set_camera_keyframe', 'shot_id': shot, 'offset_ms': 0, 'easing': 'bounce'},
    ]:
        with pytest.raises(ValueError):
            change(ops, unit, [invalid])
        assert ops.read_storyboard(unit) == board
    with pytest.raises(ValueError, match='场内目标'):
        ops.save_storyboard_camera_preset('不应保存引用', settings | {'focus_target_object_id': 'object-1'}, 1, '非法')


def test_inserting_focus_only_camera_key_preserves_linear_motion(project):
    ops, unit, _ = setup_board(project)
    board = change(ops, unit, [{'op': 'create_shot', 'title': '运动中移焦', 'duration_ms': 1001}])
    shot = board['shots'][0]['id']
    change(ops, unit, [{'op': 'set_camera_keyframe', 'shot_id': shot, 'offset_ms': 0, 'x': 0, 'interpolation': 'linear'},
                      {'op': 'set_camera_keyframe', 'shot_id': shot, 'offset_ms': 1000, 'x': 1}])
    board = change(ops, unit, [{'op': 'set_camera_keyframe', 'shot_id': shot, 'offset_ms': 500, 'focus_distance': 2}])
    key = board['shots'][0]['camera']['keys'][1]
    assert key['x'] == .5 and key['interpolation'] == 'linear'
    assert ops.preview_storyboard(unit, 750)['camera']['position']['x'] == .75
