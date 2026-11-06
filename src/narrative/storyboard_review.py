"""Read-only storyboard integrity and current-dependency review."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PIL import Image


def sketch_signature(basis):
    import hashlib
    import json
    current = deepcopy(basis)
    current.pop('body_revision', None)
    return hashlib.sha256(json.dumps(current, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def check_storyboard(ops, board):
    from .storyboard_audio import probe_audio, validate_audio
    from .storyboard_camera import resolve_markers, validate_camera
    from .storyboard_media import probe_video
    from .storyboard_timing import validate_timing
    from .storyboard_video import _signature, validate_media

    diagnostics = []
    affected = set()
    frames = set()
    generations = []
    def issue(code, message, *, severity='error', shot=None, frame=None, **extra):
        row = {'code': code, 'severity': severity, 'message': str(message), **extra}
        if shot:
            row['shot_id'] = shot['id']
            affected.add(shot['id'])
        if frame and shot:
            row['frame_id'] = frame['id']
            frames.add((shot['id'], frame['id']))
        diagnostics.append(row)
    context = ops.read_script_unit_context(board['unit_id'])
    block_ids = {block['id'] for block in context['unit']['blocks']}
    resolved = deepcopy(board)
    for code, validate in [('marker_reference_invalid', resolve_markers), ('camera_invalid', validate_camera),
                           ('timing_invalid', validate_timing), ('audio_invalid', lambda value: validate_audio(value, check_media=False)),
                           ('media_invalid', validate_media)]:
        try:
            validate(resolved)
        except (ValueError, TypeError, KeyError) as error:
            issue(code, error)
    for obj in board.get('objects', []):
        if obj.get('asset_id'):
            try:
                card = ops.get_asset(obj['asset_id'])
                for field in ('images', 'reference_images'):
                    for image in card.get(field, []):
                        if not Path(image['path']).is_file():
                            issue('asset_image_missing', '关联资产图片缺失', object_id=obj['id'], asset_id=obj['asset_id'], image_id=image['id'])
                            affected.update(shot['id'] for shot in board['shots'])
            except ValueError as error:
                issue('asset_reference_missing', error, object_id=obj['id'], asset_id=obj['asset_id'])
                affected.update(shot['id'] for shot in board['shots'])
    from .storyboard_script import validate_script
    try:
        validate_script(board, ops.get_asset, {row['id']: row for row in context['unit']['blocks']})
    except (ValueError, TypeError) as error:
        issue('shot_script_invalid', error)
    for shot in board['shots']:
        for element in shot.get('visual_elements', []):
            if element.get('asset_id'):
                try:
                    card = ops.get_asset(element['asset_id'])
                    for field in ('images', 'reference_images'):
                        for image in card.get(field, []):
                            if not Path(image['path']).is_file():
                                issue('asset_image_missing', '本镜视觉资源图片缺失', shot=shot, asset_id=element['asset_id'])
                except ValueError as error:
                    issue('asset_reference_missing', error, shot=shot, asset_id=element['asset_id'])
    decoded = {}
    def image_ok(path):
        if path not in decoded:
            try:
                with Image.open(path) as image:
                    image.verify()
                decoded[path] = None
            except (OSError, ValueError, SyntaxError) as error:
                decoded[path] = str(error)
        return decoded[path]
    for shot in board['shots']:
        for identifier in shot['source_block_ids']:
            if identifier not in block_ids:
                issue('source_block_missing', '本镜正文依据已删除或不属于本场', shot=shot, block_id=identifier)
        for frame in shot['keyframes']:
            for field in ('sketch_path', 'style_path'):
                path = frame.get(field)
                if path and image_ok(path):
                    issue('frame_image_missing_or_invalid', f'{field}图片缺失或无法解码', shot=shot, frame=frame, field=field)
            if not frame.get('sketch_path'):
                issue('frame_sketch_missing', '关键帧未绑定草图', shot=shot, frame=frame)
            if frame.get('sketch_basis_hash'):
                try:
                    basis, _ = ops._storyboard_sketch_basis(resolved, shot, frame['offset_ms'], context)
                    if sketch_signature(basis) != frame['sketch_basis_hash']:
                        issue('sketch_basis_changed', '草图关联正文、创作约定、调度、摄影机或资产依据已变化', severity='warning', shot=shot, frame=frame)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    issue('sketch_basis_unavailable', error, shot=shot, frame=frame)
        from .storyboard_panels import panel_generation_basis
        for panel in shot.get('panels', []):
            for candidate in panel.get('candidates', []):
                if image_ok(candidate['path']):
                    issue('panel_image_missing_or_invalid', '画格候选图片缺失或无法解码', shot=shot, panel_id=panel['id'], candidate_id=candidate['id'])
                if candidate.get('generation_basis'):
                    try:
                        basis, _ = ops._storyboard_sketch_basis(resolved, shot, panel['offset_ms'], context)
                        current = panel_generation_basis(basis, shot, panel)
                        if current != candidate['generation_basis']:
                            issue('panel_basis_changed', '此格意图、底图或本镜视觉依据已变化，候选待复核／待更新', severity='warning', shot=shot, panel_id=panel['id'], candidate_id=candidate['id'])
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        issue('panel_basis_unavailable', error, shot=shot, panel_id=panel['id'], candidate_id=candidate['id'])
        for sound in shot.get('sounds', []):
            if sound.get('kind') != 'silence':
                try:
                    actual = probe_audio(sound.get('audio_path'))
                    if actual['media_sha256'] != sound.get('media_sha256'):
                        raise ValueError('音频文件内容已变化')
                except (OSError, ValueError) as error:
                    issue('sound_media_missing_or_changed', error, shot=shot, sound_id=sound['id'])
        for layer in shot.get('animation', {}).get('layers', []):
            if not layer.get('path') or image_ok(layer['path']):
                issue('animation_layer_missing_or_invalid', '动画图层图片缺失或无法解码', shot=shot, layer_id=layer['id'])
        selected = shot.get('selected_video')
        if selected:
            try:
                actual = probe_video(Path(selected['path']))
                if actual['duration_ms'] != selected['media']['duration_ms'] or actual['media_sha256'] != selected['media'].get('media_sha256'):
                    raise ValueError('采用视频内容或实际时长已变化，请重新选择区间')
            except (OSError, ValueError, KeyError) as error:
                issue('selected_video_missing_or_changed', error, shot=shot)
        signature = None
        signature_error = None
        if shot.get('generations'):
            try:
                signature = _signature(ops, resolved, shot)
            except (OSError, ValueError, TypeError, KeyError) as error:
                signature_error = str(error)
        for generation in shot.get('generations', []):
            row = {'shot_id': shot['id'], 'generation_id': generation['id'], 'status': generation['status'],
                   'basis_status': 'unavailable' if signature_error else 'current' if generation.get('basis_hash') == signature else 'changed',
                   'stale': None if signature_error else generation.get('basis_hash') != signature}
            generations.append(row)
            if signature_error:
                issue('generation_basis_unavailable', signature_error, shot=shot, generation_id=generation['id'])
            elif row['stale']:
                issue('generation_basis_changed', '视频生成依据已变化，已成功产物也需复核', severity='warning', shot=shot, generation_id=generation['id'])
            for source in generation.get('inputs', []):
                if not Path(source['path']).is_file():
                    issue('generation_input_missing', '视频生成输入文件缺失', shot=shot, generation_id=generation['id'], input_kind=source.get('kind'))
            if generation.get('request_path'):
                import hashlib
                request = Path(generation['request_path'])
                if not request.is_file():
                    issue('generation_request_missing', '已审阅的视频请求文件缺失', shot=shot, generation_id=generation['id'])
                else:
                    with request.open('rb') as source:
                        checksum = hashlib.file_digest(source, 'sha256').hexdigest()
                    if checksum != generation.get('request_sha256'):
                        issue('generation_request_changed', '已审阅的视频请求文件内容变化', shot=shot, generation_id=generation['id'])
            output = generation.get('output_path')
            if output and not Path(output).is_file():
                issue('generation_output_missing', '视频生成输出文件缺失', shot=shot, generation_id=generation['id'])
            if generation.get('status') == 'succeeded' and not output:
                issue('generation_output_missing', '成功记录未保存实际返回视频', shot=shot, generation_id=generation['id'])
    return {'unit_id': board['unit_id'], 'revision': board['revision'], 'ok': not any(row['severity'] == 'error' for row in diagnostics),
            'needs_review': bool(diagnostics), 'diagnostics': diagnostics,
            'affected_shots': [{'shot_id': shot['id'], 'title': shot['title']} for shot in board['shots'] if shot['id'] in affected],
            'affected_frames': [{'shot_id': shot, 'frame_id': frame} for shot, frame in sorted(frames)], 'generations': generations,
            'dependency_scope': 'shot source blocks and bound assets; scene objects/markers conservatively shared; 2.5D is not mesh occlusion'}
