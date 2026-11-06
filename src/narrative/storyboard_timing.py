"""Deterministic millisecond storyboard timing and shared scene staging."""
from __future__ import annotations

import json
import math
from copy import deepcopy


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{label}必须为大于等于{minimum}的整数')
    return value


def number(value, label, bounded=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (bounded and not 0 <= value <= 1):
        raise ValueError(f'{label}须为' + ('0..1之间的' if bounded else '有限') + '数值')
    return value


def validate_timing(board, asset_lookup=None):
    fps = integer(board.get('fps', 24), '预演帧率', 1)
    if fps > 120:
        raise ValueError('预演帧率不能超过120')
    objects = {obj['id']: obj for obj in board.get('objects', [])}
    for obj in objects.values():
        if not isinstance(obj.get('name'), str) or not obj['name'].strip() or obj.get('kind') not in ('person', 'prop', 'set'):
            raise ValueError('场面对象需要名称及person/prop/set类型')
        parent = obj.get('parent_id')
        seen = {obj['id']}
        while parent:
            if parent not in objects:
                raise ValueError('父对象须属于本场')
            if parent in seen:
                raise ValueError('场面对象父级不能形成循环')
            seen.add(parent)
            parent = objects[parent].get('parent_id')
        if obj.get('asset_id') is not None:
            if not isinstance(obj['asset_id'], str) or not obj['asset_id']:
                raise ValueError('资源引用须为实际资产编号或null')
            if asset_lookup:
                asset_lookup(obj['asset_id'])
        times = set()
        for row in [obj, *obj['keyframes']]:
            for field in ('x', 'y', 'rotation', 'depth', 'elevation', 'height'):
                number(row.get(field, .2 if field == 'height' else 0), field, field in ('x', 'y'))
            if row.get('height', .2) <= 0:
                raise ValueError('对象高度须大于0')
            if not isinstance(row['state'], dict):
                raise ValueError('对象状态须为对象')
            try:
                json.dumps(row['state'], allow_nan=False)
            except (TypeError, ValueError) as error:
                raise ValueError('对象状态须为有限JSON对象') from error
            if row is not obj:
                time = integer(row['time_ms'], '动作时间')
                if time in times:
                    raise ValueError('同一对象的动作关键点时间不能重复')
                times.add(time)
                if row['interpolation'] not in ('hold', 'linear'):
                    raise ValueError('过渡仅支持hold或linear')
    for shot in board['shots']:
        integer(shot.get('action_start_ms', 0), '动作起点')
        offsets = set()
        for frame in shot['keyframes']:
            offset = integer(frame['offset_ms'], '关键帧位置')
            if offset >= shot['duration_ms']:
                raise ValueError(f"关键帧{frame['id']}位置{offset}ms超出镜头{shot['id']}时长{shot['duration_ms']}ms；请移动或删除后保存")
            if offset in offsets:
                raise ValueError('同镜头关键帧时间不能重复')
            offsets.add(offset)


def object_states(board, action_ms):
    result = {}
    objects = {row['id']: row for row in board.get('objects', [])}
    def resolve(identifier):
        if identifier in result:
            return result[identifier]
        obj = objects[identifier]
        state = {key: deepcopy(value) for key, value in obj.items() if key != 'keyframes'}
        keys = sorted(obj['keyframes'], key=lambda row: row['time_ms'])
        reached = [key for key in keys if key['time_ms'] <= action_ms]
        if reached:
            left = reached[-1]
            for field in ('x', 'y', 'rotation', 'depth', 'elevation', 'height', 'state'):
                state[field] = deepcopy(left.get(field, state.get(field, .2 if field == 'height' else 0)))
            right = next((key for key in keys if key['time_ms'] > action_ms), None)
            if right and left['interpolation'] == 'linear':
                ratio = (action_ms - left['time_ms']) / (right['time_ms'] - left['time_ms'])
                for field in ('x', 'y', 'rotation', 'depth', 'elevation', 'height'):
                    state[field] += (right.get(field, state[field]) - state[field]) * ratio
        state.setdefault('height', .2)
        state.setdefault('elevation', 0)
        state.update(world_elevation=state['elevation'], world_x=state['x'], world_y=state['y'], world_rotation=state['rotation'], world_depth=state['depth'])
        if obj.get('parent_id'):
            parent = resolve(obj['parent_id'])
            angle = math.radians(parent['world_rotation'])
            state.update(world_x=parent['world_x'] + state['x'] * math.cos(angle) - state['y'] * math.sin(angle),
                         world_y=parent['world_y'] + state['x'] * math.sin(angle) + state['y'] * math.cos(angle),
                         world_rotation=parent['world_rotation'] + state['rotation'], world_depth=parent['world_depth'] + state['depth'], world_elevation=parent.get('world_elevation', 0) + state['elevation'])
        result[identifier] = state
        return state
    return [resolve(identifier) for identifier in objects]


def preview(board, time_ms):
    from .storyboard_audio import preview_audio
    from .storyboard_camera import camera_view
    integer(time_ms, '播放位置')
    total = sum(shot['duration_ms'] for shot in board['shots'])
    time_ms = min(time_ms, total)
    result = {'unit_id': board['unit_id'], 'revision': board['revision'], 'fps': board.get('fps', 24),
              'time_ms': time_ms, 'total_duration_ms': total, 'at_end': time_ms == total,
              'shot_id': None, 'shot_index': None, 'local_ms': None, 'action_ms': None, 'frame_id': None, 'frame': None, 'objects': [], 'camera': None, 'sounds': []}
    start = 0
    for index, shot in enumerate(board['shots']):
        if time_ms < start + shot['duration_ms'] or index == len(board['shots']) - 1:
            local = time_ms - start
            action = shot.get('action_start_ms', 0) + local
            frames = [frame for frame in shot['keyframes'] if frame['offset_ms'] <= local]
            frame = max(frames, key=lambda row: row['offset_ms']) if frames else None
            states = object_states(board, action)
            result.update(shot_id=shot['id'], shot_index=index, local_ms=local, action_ms=action,
                          frame_id=frame['id'] if frame else None, frame=deepcopy(frame), objects=states, camera=camera_view(board, shot, local, states), sounds=preview_audio(board, shot, local))
            break
        start += shot['duration_ms']
    return result

OBJECT_FIELDS = {'name', 'kind', 'parent_id', 'asset_id', 'x', 'y', 'rotation', 'depth', 'elevation', 'height', 'state'}
KEY_FIELDS = {'time_ms', 'x', 'y', 'rotation', 'depth', 'elevation', 'height', 'state', 'interpolation'}
STAGING_FIELDS = {
    'set_fps': {'fps'}, 'create_object': OBJECT_FIELDS, 'update_object': OBJECT_FIELDS | {'object_id'},
    'delete_object': {'object_id'}, 'set_action_keyframe': KEY_FIELDS | {'object_id'},
    'move_action_keyframe': {'object_id', 'keyframe_id', 'time_ms'},
    'delete_action_keyframe': {'object_id', 'keyframe_id'},
}


def apply_staging(board, change, allocate):
    op = change['op']
    if op == 'set_fps':
        board['fps'] = change.get('fps')
        return
    if op == 'create_object':
        row = {'id': allocate('object-'), 'name': '', 'kind': None, 'parent_id': None, 'asset_id': None,
               'x': 0.5, 'y': 0.5, 'rotation': 0, 'depth': 0, 'elevation': 0, 'height': .2, 'state': {}, 'keyframes': []}
        row.update({key: deepcopy(value) for key, value in change.items() if key in OBJECT_FIELDS})
        board['objects'].append(row)
        return
    obj = next((row for row in board['objects'] if row['id'] == change.get('object_id')), None)
    if obj is None:
        raise ValueError('场面对象不属于本场')
    if op == 'delete_object':
        board['objects'].remove(obj)
    elif op == 'update_object':
        obj.update({key: deepcopy(value) for key, value in change.items() if key in OBJECT_FIELDS})
    elif op == 'set_action_keyframe':
        time = integer(change.get('time_ms'), '动作时间')
        keyframe = next((row for row in obj['keyframes'] if row['time_ms'] == time), None)
        if keyframe is None:
            current = next(row for row in object_states(board, time) if row['id'] == obj['id'])
            keyframe = {key: deepcopy(current[key]) for key in ('x', 'y', 'rotation', 'depth', 'elevation', 'height', 'state')}
            previous = [row for row in obj['keyframes'] if row['time_ms'] < time]
            interpolation = max(previous, key=lambda row: row['time_ms'])['interpolation'] if previous else 'hold'
            keyframe.update(id=allocate('action-key-'), time_ms=time, interpolation=interpolation)
            obj['keyframes'].append(keyframe)
        keyframe.update({key: deepcopy(value) for key, value in change.items() if key in KEY_FIELDS})
        obj['keyframes'].sort(key=lambda row: row['time_ms'])
    else:
        keyframe = next((row for row in obj['keyframes'] if row['id'] == change.get('keyframe_id')), None)
        if keyframe is None:
            raise ValueError('动作关键点不属于当前对象')
        if op == 'delete_action_keyframe':
            obj['keyframes'].remove(keyframe)
        elif op == 'move_action_keyframe':
            if keyframe.get('marker_id'):
                raise ValueError('此动作关键点绑定命名时刻，请移动标记或先解除绑定')
            keyframe['time_ms'] = integer(change.get('time_ms'), '动作时间')
            obj['keyframes'].sort(key=lambda row: row['time_ms'])
