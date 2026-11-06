"""Exact flat storyboard edit contracts shared by tools and Operations."""
import json
from copy import deepcopy
from typing import Any

from .storyboard_camera import EASINGS, POSE_FIELDS, PRESETS

_ID = {'type': 'string', 'minLength': 1}
_MS = {'type': 'integer', 'minimum': 0}
_OBJECT = {
    'name': {'type': 'string', 'minLength': 1}, 'kind': {'type': 'string', 'enum': ['person', 'prop', 'set']},
    'parent_id': {'type': ['string', 'null'], 'minLength': 1}, 'asset_id': {'type': ['string', 'null'], 'minLength': 1},
    'x': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'y': {'type': 'number', 'minimum': 0, 'maximum': 1},
    'rotation': {'type': 'number'}, 'depth': {'type': 'number'}, 'elevation': {'type': 'number'}, 'height': {'type': 'number', 'exclusiveMinimum': 0}, 'state': {'type': 'object'},
}
_KEY = {key: value for key, value in _OBJECT.items() if key in ('x', 'y', 'rotation', 'depth', 'elevation', 'height', 'state')}
_KEY.update(time_ms=_MS, interpolation={'type': 'string', 'enum': ['hold', 'linear']})
_VISUAL = {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'name', 'usage', 'asset_id'], 'properties': {'kind': {'type': 'string', 'enum': ['person', 'prop', 'set']}, 'name': _ID, 'usage': {'type': 'string'}, 'asset_id': {'type': ['string', 'null'], 'minLength': 1}}}
_SHOT = {'sound_notes': {'type': 'string'}, 'description': {'type': 'string'}, 'shot_language': {'type': 'string'}, 'visual_elements': {'type': 'array', 'items': _VISUAL}, 'sound_intent_ids': {'type': 'array', 'items': _ID, 'uniqueItems': True}, 'title': {'type': 'string', 'minLength': 1}, 'duration_ms': {'type': 'integer', 'minimum': 1},
         'source_block_ids': {'type': 'array', 'items': _ID, 'uniqueItems': True}, 'action_start_ms': _MS,
         'duration_locked': {'type': 'boolean'}, 'action_start_marker_id': {'type': ['string', 'null']}}


def _action(op, fields, required):
    return {'type': 'object', 'additionalProperties': False,
            'properties': {'op': {'const': op, 'type': 'string'}, **deepcopy(fields)}, 'required': ['op', *required]}


_ACTIONS = [
    _action('create_shot', _SHOT, ['title']),
    _action('update_shot', {'shot_id': _ID, **_SHOT}, ['shot_id']),
    _action('move_shot', {'shot_id': _ID, 'index': _MS}, ['shot_id', 'index']),
    _action('delete_shot', {'shot_id': _ID}, ['shot_id']),
    _action('set_keyframe', {'shot_id': _ID, 'offset_ms': _MS, 'sketch_path': _ID, 'style_path': _ID}, ['shot_id', 'offset_ms']),
    _action('move_keyframe', {'shot_id': _ID, 'frame_id': _ID, 'offset_ms': _MS}, ['shot_id', 'frame_id', 'offset_ms']),
    _action('delete_keyframe', {'shot_id': _ID, 'frame_id': _ID}, ['shot_id', 'frame_id']),
    _action('set_approved', {'approved': {'type': 'boolean'}}, ['approved']),
    _action('set_fps', {'fps': {'type': 'integer', 'minimum': 1, 'maximum': 120}}, ['fps']),
    _action('create_object', _OBJECT, ['name', 'kind']),
    _action('update_object', {'object_id': _ID, **_OBJECT}, ['object_id']),
    _action('delete_object', {'object_id': _ID}, ['object_id']),
    _action('set_action_keyframe', {'object_id': _ID, **_KEY}, ['object_id', 'time_ms']),
    _action('move_action_keyframe', {'object_id': _ID, 'keyframe_id': _ID, 'time_ms': _MS}, ['object_id', 'keyframe_id', 'time_ms']),
    _action('delete_action_keyframe', {'object_id': _ID, 'keyframe_id': _ID}, ['object_id', 'keyframe_id']),
]
_POSE: dict[str, Any] = {field: {'type': 'number'} for field in POSE_FIELDS}
_POSE['fov_deg'] = {'type': 'number', 'minimum': 1, 'exclusiveMaximum': 179}
_POSE['focus_distance'] = {'type': 'number', 'exclusiveMinimum': 0}
_EASING = {'type': 'string', 'enum': list(EASINGS)}
_HANDHELD = {'type': ['object', 'null'], 'additionalProperties': False, 'required': ['amplitude', 'frequency_hz', 'seed'],
             'properties': {'amplitude': {'type': 'number', 'minimum': 0, 'maximum': .05},
                            'frequency_hz': {'type': 'number', 'minimum': .1, 'maximum': 20},
                            'seed': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647}}}
_POSE['pitch'] = {'type': 'number', 'minimum': -90, 'maximum': 90}
_NULL_ID = {'type': ['string', 'null'], 'minLength': 1}
_ACTIONS.extend([
    _action('create_marker', {'name': _ID, 'time_ms': _MS}, ['name', 'time_ms']),
    _action('update_marker', {'marker_id': _ID, 'name': _ID, 'time_ms': _MS}, ['marker_id']),
    _action('delete_marker', {'marker_id': _ID}, ['marker_id']),
    _action('bind_keyframe_marker', {'shot_id': _ID, 'frame_id': _ID, 'marker_id': _NULL_ID}, ['shot_id', 'frame_id', 'marker_id']),
    _action('bind_action_keyframe_marker', {'object_id': _ID, 'keyframe_id': _ID, 'marker_id': _NULL_ID}, ['object_id', 'keyframe_id', 'marker_id']),
    _action('set_camera_keyframe', {'shot_id': _ID, 'offset_ms': _MS, **_POSE, 'interpolation': {'enum': ['hold', 'linear'], 'type': 'string'}, 'easing': _EASING}, ['shot_id', 'offset_ms']),
    _action('move_camera_keyframe', {'shot_id': _ID, 'keyframe_id': _ID, 'offset_ms': _MS}, ['shot_id', 'keyframe_id', 'offset_ms']),
    _action('delete_camera_keyframe', {'shot_id': _ID, 'keyframe_id': _ID}, ['shot_id', 'keyframe_id']),
    _action('set_camera_overlays', {'shot_id': _ID, 'handheld': _HANDHELD, 'focus_target_object_id': _NULL_ID}, ['shot_id']),
    _action('set_camera_preset', {'shot_id': _ID, 'preset': {'type': 'string', 'enum': list(PRESETS)}, 'target_object_id': _NULL_ID,
                                'start_ms': _MS, 'end_ms': _MS, 'amount': {'type': 'number'}, 'easing': _EASING, 'handheld': _HANDHELD,
                                'start': {'type': 'object', 'additionalProperties': False, 'properties': _POSE},
                                'end': {'type': 'object', 'additionalProperties': False, 'properties': _POSE}}, ['shot_id', 'preset']),
])
_INTENT = {'source_block_id': {'type': ['string', 'null'], 'minLength': 1}, 'kind': {'type': 'string', 'enum': ['dialogue', 'effect', 'silence']}, 'speaker': {'type': 'string'}, 'text': {'type': 'string'}}
_ACTIONS.extend([_action('create_sound_intent', _INTENT, ['kind', 'text']), _action('update_sound_intent', {'intent_id': _ID, **_INTENT}, ['intent_id']), _action('delete_sound_intent', {'intent_id': _ID}, ['intent_id'])])
_SOUND = {'intent_id': {'type': ['string', 'null'], 'minLength': 1}, 'kind': {'type': 'string', 'enum': ['dialogue', 'effect', 'silence']}, 'label': {'type': 'string'},
          'time_ms': _MS, 'marker_id': _ID, 'duration_ms': {'type': 'integer', 'minimum': 1}, 'source_in_ms': _MS, 'audio_path': _ID}
_ACTIONS.extend([
    _action('create_sound', {'shot_id': _ID, **_SOUND}, ['shot_id', 'kind']),
    _action('update_sound', {'shot_id': _ID, 'sound_id': _ID, **_SOUND}, ['shot_id', 'sound_id']),
    _action('delete_sound', {'shot_id': _ID, 'sound_id': _ID}, ['shot_id', 'sound_id']),
])
_PANEL = {'intent': {'type': 'string'}, 'offset_ms': _MS, 'frame_id': _NULL_ID,
          'base': {'type': 'object'}, 'marks': {'type': 'array', 'items': {'type': 'object'}}}
_ACTIONS.extend([
    _action('create_panel', {'shot_id': _ID, **_PANEL}, ['shot_id']),
    _action('update_panel', {'shot_id': _ID, 'panel_id': _ID, **_PANEL}, ['shot_id', 'panel_id']),
    _action('use_panel_frame', {'shot_id': _ID, 'panel_id': _ID}, ['shot_id', 'panel_id']),
    _action('delete_panel', {'shot_id': _ID, 'panel_id': _ID}, ['shot_id', 'panel_id']),
    _action('add_panel_candidate', {'shot_id': _ID, 'panel_id': _ID, 'image_path': _ID}, ['shot_id', 'panel_id', 'image_path']),
    _action('adopt_panel_candidate', {'shot_id': _ID, 'panel_id': _ID, 'candidate_id': _NULL_ID}, ['shot_id', 'panel_id', 'candidate_id']),
])
for _schema in _ACTIONS:
    if _schema['properties']['op']['const'] == 'set_keyframe':
        _schema['oneOf'] = [{'required': ['sketch_path'], 'not': {'required': ['style_path']}},
                            {'required': ['style_path'], 'not': {'required': ['sketch_path']}}]
ACTION_SCHEMAS = {row['properties']['op']['const']: row for row in _ACTIONS}
ACTION_FIELDS = {op: set(schema['properties']) - {'op'} for op, schema in ACTION_SCHEMAS.items()}


def storyboard_change_schema():
    """Schema for a single changes item; callers supply their array wrapper."""
    return {'type': 'object', 'oneOf': deepcopy(_ACTIONS), 'description': '每项是扁平动作对象，op必填；字段直接放在动作上，不使用keyframe等嵌套包装。'}


def validate_action_shape(change):
    op = change.get('op') if isinstance(change, dict) else None
    if not isinstance(op, str) or op not in ACTION_SCHEMAS:
        raise ValueError('分镜操作op无效；允许：' + '、'.join(ACTION_SCHEMAS))
    schema = ACTION_SCHEMAS[op]
    extras = set(change) - set(schema['properties'])
    missing = set(schema['required']) - set(change)
    paths_invalid = op == 'set_keyframe' and (('sketch_path' in change) == ('style_path' in change))
    if extras or missing or paths_invalid:
        example: dict[str, Any] = {'op': op}
        for key in schema['required'][1:]:
            field = schema['properties'][key]
            example[key] = field['enum'][0] if 'enum' in field else True if field.get('type') == 'boolean' else max(field.get('minimum', 0), 0) if field.get('type') == 'integer' else key.replace('_id', '-1')
        if op == 'set_keyframe':
            example['sketch_path'] = 'C:/images/sketch.png'
        problems = []
        if extras:
            problems.append('多余字段：' + '、'.join(sorted(extras)))
        if missing:
            problems.append('缺少字段：' + '、'.join(sorted(missing)))
        if paths_invalid:
            problems.append('sketch_path与style_path必须且只能提供一个')
        raise ValueError(f"分镜动作{op}参数无效（{'；'.join(problems)}）。允许字段：{'、'.join(schema['properties'])}；扁平示例：{json.dumps(example, ensure_ascii=False)}")
    return op
