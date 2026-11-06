"""Scene-local temporary audio: real media duration, clip bounds and shared timing."""
from __future__ import annotations

import hashlib
import math
import wave
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from mutagen import File, MutagenError

from .storyboard_timing import integer

_FIELDS = {'intent_id', 'shot_id', 'kind', 'label', 'time_ms', 'marker_id', 'duration_ms', 'source_in_ms', 'audio_path'}
AUDIO_FIELDS = {'create_sound': _FIELDS, 'update_sound': _FIELDS | {'sound_id'}, 'delete_sound': {'shot_id', 'sound_id'}}


def probe_audio(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('音频需要本地文件的完整路径')
    path = Path(value).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('音频文件缺失或为空')
    stat = path.stat()
    return deepcopy(_probe_audio(str(path), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))


@lru_cache(maxsize=128)
def _probe_audio(value, modified, created, size):
    path = Path(value)
    try:
        if path.suffix.lower() in {'.wav', '.wave'}:
            with wave.open(str(path), 'rb') as audio:
                frames, rate = audio.getnframes(), audio.getframerate()
                # Read declared samples to reject a truncated WAV header/body.
                expected = frames * audio.getnchannels() * audio.getsampwidth()
                actual = 0
                remaining = frames
                while remaining:
                    chunk = audio.readframes(min(65536, remaining))
                    if not chunk:
                        break
                    actual += len(chunk)
                    remaining -= len(chunk) // (audio.getnchannels() * audio.getsampwidth())
                if actual != expected:
                    raise ValueError('WAV样本被截断')
                seconds = frames / rate
        else:
            audio = File(path)
            if audio is None or not getattr(audio, 'info', None):
                raise ValueError('无法识别音频格式')
            seconds = audio.info.length
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('音频时长无效')
    except (OSError, EOFError, wave.Error, ValueError, MutagenError, ZeroDivisionError) as exc:
        raise ValueError(f'无法读取实际音频时长：{exc}') from exc
    # Floor milliseconds so a requested clip never exceeds actual samples.
    duration = math.floor(seconds * 1000 + 1e-7)
    if duration < 1:
        raise ValueError('音频短于1毫秒')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    stat = path.stat()
    if (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size) != (modified, created, size):
        raise ValueError('解析期间音频源文件已变化，请重新导入')
    return {'audio_path': str(path), 'media_duration_ms': duration, 'media_sha256': digest.hexdigest()}


def sound_time(board, shot, sound):
    if sound.get('marker_id') is not None:
        from .storyboard_camera import marker_time, shot_action_start
        return marker_time(board, sound['marker_id']) - shot_action_start(board, shot)
    return integer(sound.get('time_ms'), '声音镜内时间')


def audio_paths(board):
    for shot in board['shots']:
        for sound in shot.get('sounds', []):
            if sound.get('audio_path'):
                yield sound, 'audio_path'


def apply_audio(board, change, allocate):
    shot = next((row for row in board['shots'] if row['id'] == change.get('shot_id')), None)
    if shot is None:
        raise ValueError('声音所属镜头不属于本场')
    sounds = shot.setdefault('sounds', [])
    op = change['op']
    if op == 'create_sound':
        sound = {'id': allocate('sound-'), 'kind': None, 'label': '', 'source_in_ms': 0}
    else:
        sound = next((row for row in sounds if row['id'] == change.get('sound_id')), None)
        if sound is None:
            raise ValueError('声音不属于指定镜头')
        if op == 'delete_sound':
            sounds.remove(sound)
            return
    if 'time_ms' in change and 'marker_id' in change:
        raise ValueError('声音time_ms与marker_id只能选一个')
    if 'time_ms' in change:
        sound.pop('marker_id', None)
    elif 'marker_id' in change:
        sound.pop('time_ms', None)
    sound.update({key: deepcopy(value) for key, value in change.items() if key in _FIELDS - {'shot_id'}})
    if sound.get('kind') == 'silence':
        if change.get('audio_path'):
            raise ValueError('静默不能绑定音频文件')
        for key in ('audio_path', 'media_duration_ms', 'media_sha256'):
            sound.pop(key, None)
    elif sound.get('audio_path'):
        sound.update(probe_audio(sound['audio_path']))
        if 'duration_ms' not in sound:
            sound['duration_ms'] = sound['media_duration_ms'] - integer(sound.get('source_in_ms', 0), '音频源入点')
    if op == 'create_sound':
        sounds.append(sound)


def validate_audio(board, *, check_media=True):
    probed = {}
    for shot in board['shots']:
        for sound in shot.get('sounds', []):
            if sound.get('kind') not in {'dialogue', 'effect', 'silence'}:
                raise ValueError('声音类型须为dialogue、effect或silence')
            if not isinstance(sound.get('label'), str):
                raise ValueError('声音说明须为文本')
            if ('time_ms' in sound) == ('marker_id' in sound):
                raise ValueError('声音须明确time_ms或marker_id且不能同时使用')
            start = sound_time(board, shot, sound)
            duration = integer(sound.get('duration_ms'), '声音片段时长', 1)
            if start < 0 or start + duration > shot['duration_ms']:
                raise ValueError(f"声音{sound['id']}区间超出镜头时长，请显式移动、裁短或删除后保存")
            source_in = integer(sound.get('source_in_ms', 0), '音频源入点')
            if sound['kind'] == 'silence':
                if sound.get('audio_path') or source_in:
                    raise ValueError('静默不能绑定音频或源入点')
            else:
                path = sound.get('audio_path')
                if check_media:
                    if path not in probed:
                        probed[path] = probe_audio(path)
                    media = probed[path]
                else:
                    if not isinstance(path, str) or not Path(path).is_file():
                        raise ValueError('音频文件缺失')
                    media = sound
                if source_in + duration > integer(media.get('media_duration_ms'), '实际音频时长', 1):
                    raise ValueError('声音源入点与片段时长超出实际音频')
                if sound.get('media_sha256') != media['media_sha256']:
                    raise ValueError('音频源文件已变化，请重新导入')


def preview_audio(board, shot, local_ms):
    active = []
    for sound in shot.get('sounds', []):
        start = sound_time(board, shot, sound)
        if start <= local_ms < start + sound['duration_ms']:
            offset = local_ms - start
            active.append(deepcopy(sound) | {'effective_time_ms': start, 'playback_offset_ms': offset,
                'source_position_ms': sound.get('source_in_ms', 0) + offset})
    return active


def audio_conflicts(board):
    """List every invalid sound interval for marker-move previews, without writes."""
    conflicts = []
    for shot in board['shots']:
        for sound in shot.get('sounds', []):
            try:
                start = sound_time(board, shot, sound)
                duration = integer(sound.get('duration_ms'), '声音片段时长', 1)
                if 0 <= start and start + duration <= shot['duration_ms']:
                    continue
                message = f"声音{sound['id']}区间{start}..{start + duration}ms超出镜头{shot['id']}时长{shot['duration_ms']}ms"
            except ValueError as exc:
                start, duration, message = None, sound.get('duration_ms'), str(exc)
            conflicts.append({'code': 'shot_time_conflict', 'shot_id': shot['id'], 'object_type': 'sound',
                'id': sound['id'], 'offset_ms': start, 'clip_duration_ms': duration,
                'duration_ms': shot['duration_ms'], 'duration_locked': shot.get('duration_locked', False), 'message': message})
    return conflicts
