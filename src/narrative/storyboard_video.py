"""Reviewed generation inputs, real local animatics and explicit video selection."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

from .content_errors import (
    ContentConflictError,
    ContentGuardError,
    ContentValidationError,
    content_write,
)
from .graph_store import GraphStore
from .storyboard_media import probe_video, render_movie
from .storyboard_video_provider import MODELS, build_request, validate_options


def media_paths(board):
    for shot in board['shots']:
        for layer in shot.get('animation', {}).get('layers', []):
            yield layer, 'path'
        if shot.get('selected_video'):
            yield shot['selected_video'], 'path'
        for generation in shot.get('generations', []):
            for item in generation.get('inputs', []):
                yield item, 'path'
            if generation.get('output_path'):
                yield generation, 'output_path'
            if generation.get('request_path'):
                yield generation, 'request_path'


def validate_media(board):
    from .storyboard_timing import integer, number
    ids = {obj['id'] for obj in board.get('objects', [])}
    for shot in board['shots']:
        animation = shot.get('animation', {})
        if animation.get('mode', 'hold') not in ('hold', 'layers', 'camera'):
            raise ValueError('草图动画模式须为hold、layers或camera')
        layer_ids = [layer.get('id') for layer in animation.get('layers', [])]
        if any(not isinstance(identifier, str) or not identifier for identifier in layer_ids) or len(layer_ids) != len(set(layer_ids)):
            raise ValueError('动画图层须有不重复的稳定编号')
        for layer in animation.get('layers', []):
            start, end = integer(layer['start_ms'], '图层起点'), integer(layer['end_ms'], '图层终点', 1)
            if not start < end <= shot['duration_ms']:
                raise ValueError('图层时间须在本镜内，缩短镜头前请显式调整图层区间')
            if layer.get('object_id') is not None and layer['object_id'] not in ids:
                raise ValueError('动画图层对象不属于本场')
            for field in ('x', 'y', 'scale', 'opacity'):
                number(layer.get(field, 1 if field in ('scale', 'opacity') else .5), field)
            if layer.get('scale', 1) <= 0 or not 0 <= layer.get('opacity', 1) <= 1:
                raise ValueError('图层缩放须为正数，不透明度须为0..1')
        video = shot.get('selected_video')
        if video:
            start, end = integer(video['in_ms'], '视频入点'), integer(video['out_ms'], '视频出点', 1)
            if not start < end <= video['media']['duration_ms']:
                raise ValueError('采用视频的入出点超出实际素材')
            if video['fit'] == 'trim' and end - start != shot['duration_ms']:
                raise ValueError('采用视频选段与镜头时长不一致；请显式修改选段或选择变速适配')


def _shot(board, shot_id):
    shot = next((shot for shot in board['shots'] if shot['id'] == shot_id), None)
    if shot is None:
        raise ContentGuardError('镜头不属于当前场')
    return shot


def _generation(shot, identifier):
    generation = next((row for row in shot.get('generations', []) if row['id'] == identifier), None)
    if generation is None:
        raise ValueError('生成记录不属于当前镜头')
    return generation


def _revision(board, revision):
    if type(revision) is not int or board['revision'] != revision:
        raise ContentConflictError('分镜已修改，请刷新')


def _profile(ops):
    from .provider_store import ProviderStore
    defaults = {'revision': 0, 'base_url': 'https://api.minimax.io', 'model': 'MiniMax-H3',
                'resolution': '768P', 'key_env': 'MINIMAX_API_KEY', 'api_key': ''}
    return defaults | (ProviderStore().get_json(ops.graph_project_info()['project_id'], 'storyboard_video') or {})


def _sound_design(board, shot):
    intents = {row['id']: row for row in board.get('sound_intents', [])}
    return [intents[key] for key in shot.get('sound_intent_ids', []) if key in intents]


def _signature(ops, board, shot):
    context = ops.read_script_unit_context(board['unit_id'])
    basis, _ = ops._storyboard_sketch_basis(board, shot, 0, context)
    # A different body block in the same scene is not a dependency of this shot.
    basis.pop('body_revision', None)
    plan = {key: value for key, value in shot.items() if key not in ('generations', 'selected_video', 'panels')}
    images = {frame['id'] + ':' + field: hashlib.sha256(Path(frame[field]).read_bytes()).hexdigest()
              for frame in shot['keyframes'] for field in ('sketch_path', 'style_path') if frame.get(field)}
    layers = {layer['id']: hashlib.sha256(Path(layer['path']).read_bytes()).hexdigest()
              for layer in shot.get('animation', {}).get('layers', [])}
    return hashlib.sha256(json.dumps({'plan': plan, 'basis': basis, 'images': images, 'layers': layers,
                                    'sound_design': _sound_design(board, shot),
                                    'objects': board.get('objects', []), 'markers': board.get('markers', []),
                                    'fps': board.get('fps', 24), 'approved': board['approved']},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class StoryboardVideoMixin:
    _store: GraphStore
    if TYPE_CHECKING:
        def read_storyboard(self, unit_id) -> dict: ...
        def graph_project_info(self) -> dict: ...
        def read_script_unit_context(self, unit_id, dependency_refs=None, target_block_ids=None) -> dict: ...
        def _storyboard_sketch_basis(self, board, shot, offset_ms, context, asset_db=None) -> tuple[dict, list[Path]]: ...
        def _write_storyboard(self, unit_id, revision, changes, *, render_receipt=None, expected_style=None,
                              expected_body_revision=None, sketch_receipt=None, sketch_basis=None,
                              media_patch=None, media_audit=None) -> dict: ...

    def read_storyboard_video_settings(self):
        settings = _profile(self)
        key = settings.pop('api_key', '') or os.environ.get(settings['key_env'], '')
        return settings | {'has_api_key': bool(key), 'models': list(MODELS)}

    def _storyboard_video_receipt(self, unit_id, shot_id, generation_id, response):
        from .readable_ids import allocate_id
        with self._store.connect(write=True) as db:
            summary = {'unit_id': unit_id, 'shot_id': shot_id, 'generation_id': generation_id, 'response': response}
            revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES(?,?,?,?,?)',
                       (allocate_id(db, 'audit', 'video-receipt-'), revision, 'storyboard_video_receipt',
                        '保存实际视频服务回执', json.dumps(summary, ensure_ascii=False)))

    def read_storyboard_video_receipts(self, unit_id, shot_id=None):
        result = []
        with self._store.connect() as db:
            for row in db.execute("SELECT id,summary FROM actions WHERE action='storyboard_video_receipt' ORDER BY rowid"):
                record = json.loads(row['summary'])
                if record['unit_id'] == unit_id and (shot_id is None or record['shot_id'] == shot_id):
                    result.append({'id': row['id'], **record})
        return {'unit_id': unit_id, 'receipts': result}

    def save_storyboard_video_settings(self, settings, expected_revision, api_key=None, reason='修改视频生成服务'):
        from .provider_store import ProviderStore
        from .storyboard_video_provider import validate_base_url
        allowed = {'base_url', 'model', 'resolution', 'key_env'}
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise ValueError('视频服务设置字段无效')
        current = _profile(self)
        if type(expected_revision) is not int or current['revision'] != expected_revision:
            raise ValueError('视频服务设置已变化，请刷新')
        value = current | settings | {'revision': expected_revision + 1}
        if value['model'] not in MODELS or not isinstance(value['key_env'], str) or not value['key_env'].isidentifier():
            raise ValueError('视频服务模型或密钥环境变量名无效')
        validate_base_url(value['base_url'])
        validate_options(value['model'], value['resolution'], 5000)
        if api_key is not None:
            if not isinstance(api_key, str):
                raise ValueError('密钥须为文本')
            value['api_key'] = api_key.strip()
        project_id = self.graph_project_info()['project_id']
        with ProviderStore()._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM provider_settings WHERE project_id=? AND key='storyboard_video'", (project_id,)).fetchone()
            stored_revision = json.loads(row[0])['revision'] if row else 0
            if stored_revision != expected_revision:
                raise ValueError('视频服务设置已变化，请刷新')
            db.execute("INSERT INTO provider_settings(project_id,key,value) VALUES(?,'storyboard_video',?) "
                       "ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value", (project_id, json.dumps(value)))
        return self.read_storyboard_video_settings()

    def _save_storyboard_media(self, board, shot, values, action, basis=None):
        return self._write_storyboard(board['unit_id'], board['revision'], [],
            media_patch={'shot_id': shot['id'], 'values': values}, media_audit={'operation': action, 'shot_id': shot['id']},
            sketch_basis=basis)

    @content_write
    def set_storyboard_animation(self, unit_id, shot_id, revision, mode, layers=None):
        from PIL import Image
        board = self.read_storyboard(unit_id)
        shot = _shot(board, shot_id)
        _revision(board, revision)
        if board['approved']:
            raise ContentGuardError('请刷新并解除定案后编辑草图动画')
        rows = deepcopy(shot.get('animation', {}).get('layers', []) if layers is None else layers)
        if not isinstance(rows, list):
            raise ValueError('动画图层须为列表')
        allowed = {'id', 'object_id', 'path', 'start_ms', 'end_ms', 'x', 'y', 'scale', 'opacity'}
        next_number = shot.get('animation', {}).get('next_layer_number', 1)
        for index, layer in enumerate(rows):
            if not isinstance(layer, dict) or set(layer) - allowed or not {'path', 'start_ms', 'end_ms'} <= set(layer):
                raise ContentValidationError('图层需要path/start_ms/end_ms，可选object_id/x/y/scale/opacity', problems=[{'code': 'CONTENT_VALIDATION', 'message': '图层字段无效', 'path': ['layers', index] if layers is not None else []}])
            path = Path(layer['path'])
            if not path.is_absolute():
                raise ValueError('图层需要本地图片完整路径')
            try:
                with Image.open(path) as image:
                    image.verify()
            except (OSError, SyntaxError, ValueError) as error:
                raise OSError('图层图片无法读取') from error
            if not layer.get('id'):
                layer['id'] = f'{shot_id}-layer-{next_number}'
                next_number += 1
        if mode not in ('hold', 'layers', 'camera'):
            raise ContentValidationError('草图动画模式须为hold、layers或camera', problems=[{'code': 'CONTENT_VALIDATION', 'message': '动画模式无效', 'path': ['mode']}])
        animation = {'mode': mode, 'layers': rows, 'next_layer_number': next_number}
        result = deepcopy(board)
        _shot(result, shot_id)['animation'] = animation
        validate_media(result)
        return self._save_storyboard_media(board, shot, {'animation': animation}, 'set_animation')

    def render_storyboard_animatic(self, unit_id, shot_id, output_path, variant='sketch', include_audio=False):
        if variant not in ('sketch', 'style') or type(include_audio) is not bool:
            raise ValueError('请选择sketch或style，并明确是否合成临时声音')
        board = self.read_storyboard(unit_id)
        shot = _shot(board, shot_id)
        destination = Path(output_path)
        if not destination.is_absolute() or destination.suffix.lower() != '.mp4' or destination.exists():
            raise ValueError('请指定未存在的MP4完整路径')
        with tempfile.TemporaryDirectory(prefix='storyboard-movie-') as folder:
            movie = Path(folder) / 'animatic.mp4'
            media = render_movie(board, shot, movie, variant, include_audio)
            created = False
            try:
                output = destination.open('xb')
                created = True
                with output, movie.open('rb') as source:
                    shutil.copyfileobj(source, output)
            except BaseException:
                if created:
                    destination.unlink(missing_ok=True)
                raise
        return {'path': str(destination), 'shot_id': shot_id, 'variant': variant, 'media': media,
                'annotation_free': True, 'include_audio': include_audio}

    def attach_storyboard_video(self, unit_id, shot_id, revision, video_path, in_ms, out_ms, fit='trim'):
        board = self.read_storyboard(unit_id)
        shot = _shot(board, shot_id)
        _revision(board, revision)
        if fit not in ('trim', 'retime') or type(in_ms) is not int or type(out_ms) is not int:
            raise ValueError('请刷新并选择trim或retime适配方式')
        path = Path(video_path)
        if not path.is_absolute():
            raise ValueError('请指定本地视频完整路径')
        media = probe_video(path)
        selected = {'path': str(path), 'in_ms': in_ms, 'out_ms': out_ms, 'fit': fit, 'media': media,
                    'speed': 1}
        temporary = deepcopy(board)
        _shot(temporary, shot_id)['selected_video'] = selected
        validate_media(temporary)
        selected['speed'] = (out_ms - in_ms) / shot['duration_ms']
        return self._save_storyboard_media(board, shot, {'selected_video': selected}, 'attach_video')

    def detach_storyboard_video(self, unit_id, shot_id, revision):
        board = self.read_storyboard(unit_id)
        shot = _shot(board, shot_id)
        _revision(board, revision)
        return self._save_storyboard_media(board, shot, {'selected_video': None}, 'detach_video')

    def prepare_storyboard_video(self, unit_id, shot_id, revision, mode, duration_ms, prompt='', frame_ids=None, model=None, resolution=None):
        board = self.read_storyboard(unit_id)
        shot = _shot(board, shot_id)
        _revision(board, revision)
        if not board['approved']:
            raise ValueError('请刷新并先将本场分镜定案')
        if not isinstance(prompt, str):
            raise ValueError('视频生成说明须为文本')
        settings = _profile(self)
        model, resolution = model or settings['model'], resolution or settings['resolution']
        validate_options(model, resolution, duration_ms)
        frames = [frame for frame in shot['keyframes'] if frame.get('style_path')]
        if frame_ids is not None:
            if not isinstance(frame_ids, list) or any(not isinstance(identifier, str) for identifier in frame_ids) or len(set(frame_ids)) != len(frame_ids):
                raise ValueError('请指定无重复的风格帧编号列表')
            frame_map = {frame['id']: frame for frame in frames}
            if any(identifier not in frame_map for identifier in frame_ids):
                raise ValueError('所选风格帧缺失或不属于本镜')
            frames = [frame_map[identifier] for identifier in frame_ids]
        elif mode == 'endpoints' and len(frames) > 2:
            frames = [frames[0], frames[-1]]
        if not frames or (mode == 'reference_motion' and len(frames) > 9) or (mode == 'endpoints' and len(frames) > 2):
            raise ValueError('请先生成风格帧，参考运动模式选择1..9张，首尾帧模式选择1..2张')
        if mode not in ('reference_motion', 'endpoints'):
            raise ValueError('请选择参考运动或首尾帧模式')
        identifier = f'{shot_id}-generation-{len(shot.get("generations", [])) + 1}'
        basis = _signature(self, board, shot)
        write_basis, _ = self._storyboard_sketch_basis(board, shot, 0, self.read_script_unit_context(unit_id))
        paragraphs = '\n'.join(block.get('payload', {}).get('text', '') for block in write_basis['blocks'])
        moments = [{'name': moment['name'], 'shot_offset_ms': moment['time_ms'] - shot.get('action_start_ms', 0)}
                   for moment in board.get('markers', [])
                   if shot.get('action_start_ms', 0) <= moment['time_ms'] <= shot.get('action_start_ms', 0) + shot['duration_ms']]
        design = '\n'.join(filter(None, [shot.get('description'), shot.get('shot_language'), shot.get('sound_notes'),
                                         *(element['name'] + '：' + element.get('usage', '') for element in shot.get('visual_elements', [])),
                                         *((cue.get('speaker') or cue['kind']) + '：' + cue['text'] for cue in _sound_design(board, shot))]))
        text = ((prompt.strip() or shot['title']) + ('\n本镜分镜稿：\n' + design if design else '') + '\n本镜当前剧本内容：\n' + paragraphs
                + '\n项目创作约定：' + write_basis['project_style'].get('text', '')
                + '\n镜头剪辑时长：' + str(shot['duration_ms']) + 'ms；生成时长：' + str(duration_ms) + 'ms。'
                + '\n场内动作起点：' + str(shot.get('action_start_ms', 0)) + 'ms；镜内命名时刻：' + json.dumps(moments, ensure_ascii=False)
                + '\n运镜意图：' + shot.get('camera', {}).get('preset', 'fixed')
                + '\n遵循参考中的动作因果、揭露顺序、停顿与切点，不为填满时长添加独立剧情。输出不要带编辑编号、标签或运动箭头。')
        with tempfile.TemporaryDirectory(prefix='storyboard-input-') as folder:
            inputs = [{'kind': 'style', 'frame_id': frame['id'], 'path': frame['style_path']} for frame in frames]
            if mode == 'reference_motion':
                if not 2000 <= shot['duration_ms'] <= 15000 or not 24 <= board.get('fps', 24) <= 60:
                    raise ValueError('运动参考需要2..15秒、24..60fps；请显式调整镜头或使用首尾帧模式')
                movie = Path(folder) / 'motion.mp4'
                render_movie(board, shot, movie)
                inputs.insert(0, {'kind': 'motion', 'path': str(movie)})
            # Validate actual inputs and provider constraints before saving the review.
            request = build_request(model=model, resolution=resolution, duration_ms=duration_ms, mode=mode, prompt=text,
                                    images=[item['path'] for item in inputs if item['kind'] == 'style'],
                                    motion_path=next((item['path'] for item in inputs if item['kind'] == 'motion'), None))
            request_file = Path(folder) / 'reviewed-request.json'
            raw_request = json.dumps(request).encode()
            request_file.write_bytes(raw_request)
            generation = {'id': identifier, 'mode': mode, 'model': model, 'resolution': resolution,
                          'duration_ms': duration_ms, 'prompt': text, 'inputs': inputs, 'basis_hash': basis,
                          'base_url': settings['base_url'], 'key_env': settings['key_env'],
                          'status': 'prepared', 'provider_task_id': None, 'usage': None,
                          'request_path': str(request_file), 'request_sha256': hashlib.sha256(raw_request).hexdigest()}
            if _signature(self, self.read_storyboard(unit_id), shot) != basis:
                raise ValueError('准备期间生成依据已变化，请重新准备')
            return self._save_storyboard_media(board, shot, {'generations': shot.get('generations', []) + [generation]}, 'prepare_generation', write_basis)

    def submit_storyboard_video(self, unit_id, shot_id, generation_id, revision):
        from .storyboard_video_provider import submit
        board = self.read_storyboard(unit_id)
        shot = _shot(board, shot_id)
        generation = deepcopy(_generation(shot, generation_id))
        _revision(board, revision)
        if not board['approved'] or generation['status'] != 'prepared' or generation['basis_hash'] != _signature(self, board, shot):
            raise ValueError('生成输入已变化或已提交，请重新核对；不会重复提交任务')
        settings = _profile(self)
        if settings['base_url'] != generation['base_url'] or settings['key_env'] != generation['key_env']:
            raise ValueError('视频服务地址或密钥来源已变化，请重新准备输入')
        key = settings.get('api_key') or os.environ.get(settings['key_env'], '')
        if not key:
            raise ValueError('尚未配置视频生成密钥，请先打开视频服务设置')
        raw_request = Path(generation['request_path']).read_bytes()
        if hashlib.sha256(raw_request).hexdigest() != generation['request_sha256']:
            raise ValueError('已审阅的生成请求文件发生变化，请重新准备')
        request = json.loads(raw_request)
        generation['status'] = 'submitting'
        generations = [generation if row['id'] == generation_id else row for row in shot['generations']]
        write_basis, _ = self._storyboard_sketch_basis(board, shot, 0, self.read_script_unit_context(unit_id))
        if generation['basis_hash'] != _signature(self, board, shot):
            raise ValueError('提交前生成依据已变化，请重新准备')
        self._save_storyboard_media(board, shot, {'generations': generations}, 'begin_generation_submission', write_basis)
        try:
            result = submit(settings['base_url'], key, request)
            self._storyboard_video_receipt(unit_id, shot_id, generation_id, result)
            generation.update(status='submitted', provider_task_id=result['task_id'], provider_response=result)
        except Exception as error:
            generation.update(status='failed', error=str(error).replace(key, '[REDACTED]'))
            current = self.read_storyboard(unit_id)
            current_shot = _shot(current, shot_id)
            rows = [generation if row['id'] == generation_id else row for row in current_shot['generations']]
            self._save_storyboard_media(current, current_shot, {'generations': rows}, 'generation_submission_failed')
            raise
        current = self.read_storyboard(unit_id)
        current_shot = _shot(current, shot_id)
        generation['stale'] = generation['basis_hash'] != _signature(self, current, current_shot)
        rows = [generation if row['id'] == generation_id else row for row in current_shot['generations']]
        return self._save_storyboard_media(current, current_shot, {'generations': rows}, 'generation_submitted')

    def refresh_storyboard_video(self, unit_id, shot_id, generation_id, revision):
        from .storyboard_video_provider import download, query
        board = self.read_storyboard(unit_id)
        shot = _shot(board, shot_id)
        generation = deepcopy(_generation(shot, generation_id))
        _revision(board, revision)
        if not generation.get('provider_task_id'):
            raise ValueError('请刷新并选择已提交的生成记录')
        if generation['status'] == 'succeeded' and generation.get('output_path') and Path(generation['output_path']).is_file():
            stale = generation['basis_hash'] != _signature(self, board, shot)
            if stale == generation.get('stale', False):
                return board
            generation['stale'] = stale
            rows = [generation if row['id'] == generation_id else row for row in shot['generations']]
            return self._save_storyboard_media(board, shot, {'generations': rows}, 'generation_review_changed')
        settings = _profile(self)
        if settings['base_url'] != generation['base_url'] or settings['key_env'] != generation['key_env']:
            raise ValueError('请使用该生成任务原服务地址及密钥来源查询')
        key = settings.get('api_key') or os.environ.get(settings['key_env'], '')
        response = query(settings['base_url'], key, generation['provider_task_id'])
        self._storyboard_video_receipt(unit_id, shot_id, generation_id, response)
        task = response['task']
        status = task.get('status')
        if status not in ('queued', 'running', 'succeeded', 'failed', 'cancelled'):
            raise ValueError('视频服务返回未知状态，不自动恢复')
        generation.update(status=status, provider_response=response, usage=task.get('usage'))
        generation['stale'] = generation['basis_hash'] != _signature(self, board, shot)
        with tempfile.TemporaryDirectory(prefix='storyboard-result-') as folder:
            if status == 'succeeded':
                movie = Path(folder) / 'generated.mp4'
                download(task.get('content', {}).get('url', ''), movie)
                generation.update(output_path=str(movie), media=probe_video(movie))
            rows = [generation if row['id'] == generation_id else row for row in shot['generations']]
            return self._save_storyboard_media(board, shot, {'generations': rows}, 'generation_refreshed')
