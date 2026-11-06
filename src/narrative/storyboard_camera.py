"""Shared action markers and deterministic editable 2.5D camera motion."""
from __future__ import annotations

import math
from copy import deepcopy

PRESETS = ('fixed', 'dolly', 'truck', 'crane', 'pan', 'zoom', 'follow', 'orbit', 'reveal', 'dolly_zoom')
POSE_FIELDS = {'x', 'y', 'z', 'yaw', 'pitch', 'fov_deg', 'focus_distance'}
EASINGS = ('linear', 'ease_in', 'ease_out', 'ease_in_out')
CAMERA_FIELDS = {
    'create_marker': {'name', 'time_ms'}, 'update_marker': {'marker_id', 'name', 'time_ms'}, 'delete_marker': {'marker_id'},
    'bind_keyframe_marker': {'shot_id', 'frame_id', 'marker_id'},
    'bind_action_keyframe_marker': {'object_id', 'keyframe_id', 'marker_id'},
    'set_camera_keyframe': {'shot_id', 'offset_ms', 'interpolation', 'easing'} | POSE_FIELDS,
    'move_camera_keyframe': {'shot_id', 'keyframe_id', 'offset_ms'}, 'delete_camera_keyframe': {'shot_id', 'keyframe_id'},
    'set_camera_preset': {'shot_id', 'preset', 'target_object_id', 'start_ms', 'end_ms', 'amount', 'start', 'end', 'easing', 'handheld'},
    'set_camera_overlays': {'shot_id', 'handheld', 'focus_target_object_id'},
}
DEFAULT_POSE = {'x': .5, 'y': -1., 'z': .4, 'yaw': 0., 'pitch': 0., 'fov_deg': 50., 'focus_distance': 1.}


def marker_time(board, identifier):
    if not isinstance(identifier, str) or not identifier:
        raise ValueError('命名时刻引用须为非空编号')
    marker = next((row for row in board.get('markers', []) if row['id'] == identifier), None)
    if marker is None:
        raise ValueError(f'命名时刻{identifier}不属于本场或已删除')
    from .storyboard_timing import integer
    return integer(marker['time_ms'], '命名时刻')


def shot_action_start(board, shot):
    return marker_time(board, shot['action_start_marker_id']) if shot.get('action_start_marker_id') else shot.get('action_start_ms', 0)


def resolve_markers(board):
    for obj in board.get('objects', []):
        for key in obj['keyframes']:
            if key.get('marker_id'):
                key['time_ms'] = marker_time(board, key['marker_id'])
        obj['keyframes'].sort(key=lambda row: row['time_ms'])
    for shot in board['shots']:
        shot['action_start_ms'] = shot_action_start(board, shot)
        for frame in shot['keyframes']:
            if frame.get('marker_id'):
                frame['offset_ms'] = marker_time(board, frame['marker_id']) - shot['action_start_ms']
        shot['keyframes'].sort(key=lambda row: row['offset_ms'])


def timing_conflicts(board):
    """Collect every out-of-range anchor without clipping or stopping at the first."""
    conflicts = []
    for shot in board['shots']:
        for kind, rows, field in [('frame', shot['keyframes'], 'offset_ms'), ('camera', shot.get('camera', {}).get('keys', []), 'offset_ms')]:
            for row in rows:
                if not 0 <= row[field] < shot['duration_ms']:
                    conflicts.append({'code': 'shot_time_conflict', 'shot_id': shot['id'], 'object_type': kind,
                                      'id': row['id'], 'offset_ms': row[field], 'duration_ms': shot['duration_ms'],
                                      'duration_locked': shot.get('duration_locked', False),
                                      'message': f"{kind} {row['id']}位置{row[field]}ms超出镜头{shot['id']}时长{shot['duration_ms']}ms"})
    return conflicts


def ease_ratio(ratio, easing):
    if easing == 'ease_in':
        return ratio * ratio
    if easing == 'ease_out':
        return 1 - (1-ratio) * (1-ratio)
    if easing == 'ease_in_out':
        return 2*ratio*ratio if ratio < .5 else 1-2*(1-ratio)*(1-ratio)
    return ratio


def validate_handheld(value):
    from .storyboard_timing import integer, number
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {'amplitude', 'frequency_hz', 'seed'}:
        raise ValueError('手持叠加须明确amplitude/frequency_hz/seed')
    amplitude = number(value['amplitude'], '手持幅度')
    frequency = number(value['frequency_hz'], '手持频率')
    seed = integer(value['seed'], '手持种子')
    if not 0 <= amplitude <= .05 or not .1 <= frequency <= 20 or seed > 2147483647:
        raise ValueError('手持幅度须0..0.05，频率须0.1..20Hz，seed须0..2147483647')


def handheld_pose(pose, handheld, local_ms):
    pose = dict(pose)
    if handheld:
        amplitude = handheld['amplitude']
        u = 2*math.pi*handheld['frequency_hz']*local_ms/1000
        phase = (handheld['seed'] % 10007)*.61803398875
        pose['x'] += amplitude*math.sin(u+phase)
        pose['y'] += amplitude*.5*math.cos(u*1.31+phase*.7)
        pose['z'] += amplitude*.5*math.sin(u*.79+phase*1.3)
        pose['yaw'] += amplitude*20*math.sin(u*.67+phase*1.7)
        pose['pitch'] += amplitude*12*math.cos(u*.83+phase*1.1)
    return pose


def camera_pose(shot, local_ms):
    keys = sorted(shot.get('camera', {}).get('keys', []), key=lambda row: row['offset_ms'])
    pose = dict(DEFAULT_POSE)
    reached = [row for row in keys if row['offset_ms'] <= local_ms]
    if reached:
        left = reached[-1]
        pose.update({key: left.get(key, DEFAULT_POSE[key]) for key in POSE_FIELDS})
        right = next((row for row in keys if row['offset_ms'] > local_ms), None)
        if right and left['interpolation'] == 'linear':
            ratio = ease_ratio((local_ms - left['offset_ms']) / (right['offset_ms'] - left['offset_ms']), left.get('easing', 'linear'))
            for field in POSE_FIELDS:
                pose[field] += (right.get(field, DEFAULT_POSE[field]) - left.get(field, DEFAULT_POSE[field])) * ratio
            center = shot.get('camera', {}).get('orbit_center')
            if shot.get('camera', {}).get('preset') == 'orbit' and center:
                lx, ly = left['x']-center['x'], left['y']-center['y']
                rx, ry = right['x']-center['x'], right['y']-center['y']
                angle = math.atan2(ly, lx)
                delta = (math.atan2(ry, rx)-angle+math.pi) % (2*math.pi)-math.pi
                radius = math.hypot(lx,ly)+(math.hypot(rx,ry)-math.hypot(lx,ly))*ratio
                pose['x'] = center['x']+radius*math.cos(angle+delta*ratio)
                pose['y'] = center['y']+radius*math.sin(angle+delta*ratio)
    return pose


def _target(states, identifier):
    row = next((row for row in states if row['id'] == identifier), None)
    if row is None:
        raise ValueError('摄影机目标对象不属于本场')
    return row


def _look_at(pose, target):
    dx, dy = target['world_x'] - pose['x'], target['world_y'] - pose['y']
    dz = target.get('world_elevation', 0) + target.get('height', .2) / 2 - pose['z']
    pose['yaw'] = math.degrees(math.atan2(dx, dy))
    pose['pitch'] = math.degrees(math.atan2(dz, math.hypot(dx, dy)))


def camera_view(board, shot, local_ms, states=None):
    from .storyboard_timing import object_states
    action = shot_action_start(board, shot) + local_ms
    states = object_states(board, action) if states is None else states
    camera = shot.get('camera', {})
    pose = camera_pose(shot, local_ms)
    target_id = camera.get('target_object_id')
    if target_id:
        target = _target(states, target_id)
        preset = camera.get('preset')
        if preset in ('follow', 'orbit'):
            base_states = object_states(board, shot_action_start(board, shot) + camera.get('start_ms', 0))
            base = _target(base_states, target_id)
            pose['x'] += target['world_x'] - base['world_x']
            pose['y'] += target['world_y'] - base['world_y']
            pose['z'] += target.get('world_elevation', 0) - base.get('world_elevation', 0)
        if preset in ('follow', 'orbit', 'reveal', 'dolly_zoom'):
            _look_at(pose, target)
        if preset == 'dolly_zoom' and camera.get('subject_size_ratio'):
            distance = math.dist((pose['x'], pose['y'], pose['z']), (target['world_x'], target['world_y'], target.get('world_elevation', 0) + target.get('height', .2)/2))
            if distance <= 1e-6:
                raise ValueError('摄影机与主体重合，无法保持主体比例')
            pose['fov_deg'] = math.degrees(2 * math.atan(target.get('height', .2) / (2 * distance * camera['subject_size_ratio'])))
    pose = handheld_pose(pose, camera.get('handheld'), local_ms)
    focus_id = camera.get('focus_target_object_id')
    if focus_id:
        focus = _target(states, focus_id)
        pose['focus_distance'] = max(.000001, math.dist((pose['x'],pose['y'],pose['z']), (focus['world_x'],focus['world_y'],focus.get('world_elevation',0)+focus.get('height',.2)/2)))
    yaw, pitch = math.radians(pose['yaw']), math.radians(pose['pitch'])
    right = (math.cos(yaw), -math.sin(yaw), 0)
    forward = (math.sin(yaw)*math.cos(pitch), math.cos(yaw)*math.cos(pitch), math.sin(pitch))
    up = (-math.sin(yaw)*math.sin(pitch), -math.cos(yaw)*math.sin(pitch), math.cos(pitch))
    tangent = math.tan(math.radians(pose['fov_deg']) / 2)
    projections = []
    for row in states:
        delta = (row['world_x']-pose['x'], row['world_y']-pose['y'], row.get('world_elevation', 0)+row.get('height', .2)/2-pose['z'])
        depth = sum(a*b for a,b in zip(delta, forward, strict=True))
        horizontal = sum(a*b for a,b in zip(delta, right, strict=True))
        vertical = sum(a*b for a,b in zip(delta, up, strict=True))
        front = depth > .000001
        screen_x = .5 + horizontal/(2*depth*tangent*16/9) if front else None
        screen_y = .5 - vertical/(2*depth*tangent) if front else None
        size = row.get('height', .2)/(2*depth*tangent) if front else None
        visible = screen_x is not None and screen_y is not None and 0 <= screen_x <= 1 and 0 <= screen_y <= 1
        projections.append({'object_id': row['id'], 'screen_x': screen_x, 'screen_y': screen_y, 'size_ratio': size,
                            'visible': visible, 'occluded': False, 'depth': depth,
                            'in_focus': visible and abs(math.sqrt(sum(value*value for value in delta))-pose['focus_distance']) <= max(.01, pose['focus_distance']*.1)})
    # Bounding-circle approximation only; no mesh/hand/contact inference.
    for row in projections:
        if row['visible']:
            row['occluded'] = any(other['visible'] and other['depth'] < row['depth'] and
                                  abs(other['screen_x']-row['screen_x']) < other['size_ratio']/4 and
                                  abs(other['screen_y']-row['screen_y']) < other['size_ratio']/2 for other in projections)
    return {'offset_ms': local_ms, 'action_ms': action, 'position': {key: pose[key] for key in ('x','y','z')},
            'yaw': pose['yaw'], 'pitch': pose['pitch'], 'fov_deg': pose['fov_deg'], 'focus_distance': pose['focus_distance'],
            'focus_model': '2.5d_distance_band_not_optical_dof',
            'frustum': {'near': .000001, 'vertical_fov_deg': pose['fov_deg'], 'aspect_ratio': 16/9, 'forward': list(forward)},
            'projections': projections, 'projection_model': '2.5d_no_mesh_approximate_occlusion'}


def validate_camera(board):
    from .storyboard_timing import integer, number
    ids = {row['id'] for row in board.get('objects', [])}
    for marker in board.get('markers', []):
        integer(marker['time_ms'], '命名时刻')
        if not isinstance(marker['name'], str) or not marker['name'].strip():
            raise ValueError('命名时刻需要名称')
    for shot in board['shots']:
        marker_id = shot.get('action_start_marker_id')
        if marker_id is not None:
            marker_time(board, marker_id)
        if type(shot.get('duration_locked', False)) is not bool:
            raise ValueError('镜头时长锁定须为布尔值')
        camera = shot.get('camera', {})
        if camera.get('preset', 'fixed') not in PRESETS:
            raise ValueError('不支持的运镜预设')
        validate_handheld(camera.get('handheld'))
        if camera.get('focus_target_object_id') is not None and (not isinstance(camera['focus_target_object_id'], str) or camera['focus_target_object_id'] not in ids):
            raise ValueError('对焦目标对象不属于本场')
        if camera.get('target_object_id') is not None and (not isinstance(camera['target_object_id'], str) or camera['target_object_id'] not in ids):
            raise ValueError('摄影机目标对象不属于本场')
        offsets = set()
        for key in camera.get('keys', []):
            integer(key['offset_ms'], '摄影机镜内时间')
            if key['offset_ms'] in offsets:
                raise ValueError('摄影机关键点时间不能重复')
            offsets.add(key['offset_ms'])
            for field in POSE_FIELDS:
                number(key.get(field, DEFAULT_POSE[field]), field)
            if not 1 <= key['fov_deg'] < 179 or not -90 <= key['pitch'] <= 90:
                raise ValueError('摄影机视野须在[1,179)度，俯仰须在[-90,90]度')
            if key.get('focus_distance', 1) <= 0:
                raise ValueError('对焦距离须大于0')
            if key.get('easing', 'linear') not in EASINGS:
                raise ValueError('摄影机速度曲线无效')
            if key['interpolation'] not in ('hold', 'linear'):
                raise ValueError('摄影机过渡仅支持hold/linear')
    conflicts = timing_conflicts(board)
    if conflicts:
        raise ValueError('；'.join(row['message'] for row in conflicts))


def apply_camera(board, change, allocate):
    from .storyboard_timing import integer, number, object_states
    op = change['op']
    if op == 'create_marker':
        board.setdefault('markers', []).append({'id': allocate('moment-'), 'name': change['name'], 'time_ms': integer(change['time_ms'], '命名时刻')})
        return
    if op in ('update_marker', 'delete_marker'):
        marker = next((row for row in board.get('markers', []) if row['id'] == change['marker_id']), None)
        if marker is None:
            raise ValueError('命名时刻不属于本场')
        if op == 'delete_marker':
            board['markers'].remove(marker)
        else:
            marker.update({key: change[key] for key in ('name','time_ms') if key in change})
        return
    if op == 'bind_action_keyframe_marker':
        obj = next((row for row in board['objects'] if row['id'] == change['object_id']), None)
        key = next((row for row in (obj or {}).get('keyframes', []) if row['id'] == change['keyframe_id']), None)
        if key is None:
            raise ValueError('动作关键点不属于本场对象')
        if change['marker_id'] is not None:
            marker_time(board, change['marker_id'])
        key['marker_id'] = change['marker_id']
        return
    shot = next((row for row in board['shots'] if row['id'] == change['shot_id']), None)
    if shot is None:
        raise ValueError('镜头不属于本场')
    if op == 'bind_keyframe_marker':
        frame = next((row for row in shot['keyframes'] if row['id'] == change['frame_id']), None)
        if frame is None:
            raise ValueError('关键帧不属于本镜')
        if change['marker_id'] is not None:
            marker_time(board, change['marker_id'])
        frame['marker_id'] = change['marker_id']
        return
    camera = shot.setdefault('camera', {'preset': 'fixed', 'target_object_id': None, 'subject_size_ratio': None, 'keys': []})
    if op == 'set_camera_overlays':
        for field in ('handheld', 'focus_target_object_id'):
            if field in change:
                camera[field] = deepcopy(change[field])
        return
    if op == 'set_camera_preset':
        if change.get('easing', 'linear') not in EASINGS:
            raise ValueError('摄影机速度曲线无效')
        if 'handheld' in change:
            validate_handheld(change['handheld'])
            camera['handheld'] = deepcopy(change['handheld'])
        preset = change['preset']
        if preset not in PRESETS:
            raise ValueError('不支持的运镜预设')
        start = integer(change.get('start_ms', 0), '运镜起点')
        end = integer(change.get('end_ms', shot['duration_ms'] - 1), '运镜终点')
        if end <= start:
            raise ValueError('运镜终点须晚于起点')
        for field in ('start', 'end'):
            if field in change and (not isinstance(change[field], dict) or set(change[field]) - POSE_FIELDS):
                raise ValueError('摄影机姿态须为有效扁平位置/朝向/视野字段')
        first = camera_pose(shot, start) | change.get('start', {})
        last = dict(first)
        amount = number(change.get('amount', .25 if preset not in ('pan','orbit','zoom') else 30), '运镜幅度')
        target_id = change.get('target_object_id')
        target = None
        if preset in ('follow','orbit','reveal','dolly_zoom'):
            target = _target(object_states(board, shot_action_start(board, shot)+start), target_id)
        angle = math.radians(first['yaw'])
        if preset in ('dolly','dolly_zoom','reveal'):
            last['x'] += amount*math.sin(angle)
            last['y'] += amount*math.cos(angle)
        elif preset == 'truck':
            last['x'] += amount*math.cos(angle)
            last['y'] -= amount*math.sin(angle)
        elif preset == 'crane':
            last['z'] += amount
        elif preset == 'pan':
            last['yaw'] += amount
        elif preset == 'zoom':
            last['fov_deg'] += amount
        elif preset == 'orbit':
            assert target is not None
            radians = math.radians(amount)
            dx, dy = first['x']-target['world_x'], first['y']-target['world_y']
            last['x'] = target['world_x']+dx*math.cos(radians)-dy*math.sin(radians)
            last['y'] = target['world_y']+dx*math.sin(radians)+dy*math.cos(radians)
        last.update(change.get('end', {}))
        camera.update(preset=preset, target_object_id=target_id, start_ms=start, end_ms=end, subject_size_ratio=None)
        if preset == 'dolly_zoom':
            assert target is not None
            distance = math.dist((first['x'],first['y'],first['z']), (target['world_x'],target['world_y'],target.get('world_elevation',0)+target.get('height',.2)/2))
            if distance <= 1e-6:
                raise ValueError('摄影机与主体重合')
            camera['subject_size_ratio'] = target.get('height', .2)/(2*distance*math.tan(math.radians(first['fov_deg'])/2))
        camera['keys'] = [dict(id=allocate('camera-'), offset_ms=time, interpolation='linear' if time == start else 'hold', easing=change.get('easing', 'linear'), **pose)
                          for time,pose in [(start,first),(end,last)]]
        camera.pop('orbit_center', None)
        if preset == 'orbit':
            assert target is not None
            if abs(amount) > 360:
                raise ValueError('基础环绕幅度须在-360..360度')
            camera['orbit_center'] = {'x': target['world_x'], 'y': target['world_y']}
            segments = max(1, math.ceil(abs(amount)/90))
            if end-start < segments:
                raise ValueError('环绕区间过短，无法放置有序摄影机关键点')
            for step in range(1, segments):
                ratio = step/segments
                radians = math.radians(amount*ratio)
                dx, dy = first['x']-target['world_x'], first['y']-target['world_y']
                pose = {field: first[field]+(last[field]-first[field])*ratio for field in POSE_FIELDS}
                pose.update(x=target['world_x']+dx*math.cos(radians)-dy*math.sin(radians),
                            y=target['world_y']+dx*math.sin(radians)+dy*math.cos(radians))
                camera['keys'].append(dict(id=allocate('camera-'), offset_ms=start+round((end-start)*ratio), interpolation='linear', easing=change.get('easing', 'linear'), **pose))

    elif op == 'set_camera_keyframe':
        offset = integer(change['offset_ms'], '摄影机镜内时间')
        key = next((row for row in camera['keys'] if row['offset_ms'] == offset), None)
        if key is None:
            previous = [row for row in camera['keys'] if row['offset_ms'] < offset]
            prior = max(previous, key=lambda row: row['offset_ms']) if previous else {}
            key = dict(id=allocate('camera-'), offset_ms=offset, interpolation=prior.get('interpolation', 'hold'),
                       easing=prior.get('easing', 'linear'), **camera_pose(shot, offset))
            camera['keys'].append(key)
        key.update({field: change[field] for field in POSE_FIELDS | {'interpolation', 'easing'} if field in change})
    else:
        key = next((row for row in camera['keys'] if row['id'] == change['keyframe_id']), None)
        if key is None:
            raise ValueError('摄影机关键点不属于本镜')
        if op == 'delete_camera_keyframe':
            camera['keys'].remove(key)
        else:
            key['offset_ms'] = integer(change['offset_ms'], '摄影机镜内时间')
    camera['keys'].sort(key=lambda row: row['offset_ms'])
