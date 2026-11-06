"""Deterministic, annotation-free storyboard movie rendering and media probing."""
from __future__ import annotations

import hashlib
import math
import subprocess
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps


def probe_video(path):
    file = Path(path).resolve()
    if not file.is_file():
        raise ValueError('视频文件不存在或为空')
    stat = file.stat()
    return deepcopy(_probe_video(str(file), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))


@lru_cache(maxsize=128)
def _probe_video(path, modified, created, size):
    import imageio_ffmpeg
    file = Path(path)
    if not file.is_file() or file.stat().st_size == 0:
        raise ValueError('视频文件不存在或为空')
    reader = imageio_ffmpeg.read_frames(str(file), pix_fmt='rgb24', input_params=['-protocol_whitelist', 'file,pipe'])
    try:
        metadata = next(reader)
        if not next(reader):
            raise ValueError('视频无法解码出画面')
    except (OSError, RuntimeError, StopIteration) as error:
        raise ValueError('视频文件无法解码') from error
    finally:
        reader.close()
    duration = metadata.get('duration', 0)
    fps = metadata.get('fps', 0)
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0 or not fps:
        raise ValueError('视频缺少有效时长或帧率')
    checked = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-xerror', '-err_detect', 'explode',
                              '-protocol_whitelist', 'file,pipe', '-i', str(file), '-map', '0:v:0', '-f', 'null', '-'],
                             capture_output=True)
    if checked.returncode:
        raise ValueError('视频含不可解码的画面数据：' + checked.stderr.decode(errors='replace')[-800:])
    with file.open('rb') as source:
        checksum = hashlib.file_digest(source, 'sha256').hexdigest()
    stat = file.stat()
    if (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size) != (modified, created, size):
        raise ValueError('解析期间视频源文件已变化，请重新导入')
    return {'duration_ms': round(duration * 1000), 'fps': fps, 'width': metadata['size'][0],
            'height': metadata['size'][1], 'codec': metadata.get('codec'), 'media_sha256': checksum}


def render_frame(board, shot, local_ms, variant='sketch', size=(960, 540)):
    from .storyboard_camera import camera_view
    from .storyboard_timing import object_states

    frames = [frame for frame in shot['keyframes'] if frame['offset_ms'] <= local_ms]
    if not frames:
        raise ValueError('该镜头起始画面缺失，不能导出待补画面冒充交付')
    frame = max(frames, key=lambda value: value['offset_ms'])
    path = frame.get('style_path') if variant == 'style' else frame.get('sketch_path')
    if not path or not Path(path).is_file():
        raise ValueError('导出所需的' + ('风格图' if variant == 'style' else '草图') + '缺失')
    with Image.open(path) as source:
        canvas = ImageOps.pad(source.convert('RGB'), size, color='black')
    animation = shot.get('animation', {})
    states = object_states(board, shot.get('action_start_ms', 0) + local_ms)
    if animation.get('mode') == 'camera':
        current = camera_view(board, shot, local_ms, states)
        initial = camera_view(board, shot, 0)
        current_pose, initial_pose = current.get('position', {}), initial.get('position', {})
        # A flat drawing permits pan/zoom only; it cannot synthesize unseen sides.
        fov = current.get('fov_deg', 50)
        initial_fov = initial.get('fov_deg', 50)
        scale = max(.05, math.tan(math.radians(initial_fov / 2)) / math.tan(math.radians(fov / 2)))
        dx = (current_pose.get('x', 0) - initial_pose.get('x', 0)) * size[0]
        dy = (current_pose.get('z', 0) - initial_pose.get('z', 0)) * size[1]
        horizontal_fov = math.degrees(2 * math.atan(math.tan(math.radians(fov / 2)) * size[0] / size[1]))
        dx += (current.get('yaw', 0) - initial.get('yaw', 0)) * size[0] / horizontal_fov
        dy += (current.get('pitch', 0) - initial.get('pitch', 0)) * size[1] / fov
        canvas = canvas.transform(size, Image.Transform.AFFINE,
                                  (1 / scale, 0, size[0] * (1 - 1 / scale) / 2 + dx,
                                   0, 1 / scale, size[1] * (1 - 1 / scale) / 2 - dy),
                                  resample=Image.Resampling.BICUBIC)
    if animation.get('mode') != 'layers':
        return canvas
    projections = {row['object_id']: row for row in camera_view(board, shot, local_ms, states).get('projections', [])}
    base_projections = {row['object_id']: row for row in camera_view(board, shot, 0).get('projections', [])}
    for layer in animation.get('layers', []) if animation.get('mode') == 'layers' else []:
        if not layer['start_ms'] <= local_ms < layer['end_ms']:
            continue
        state = next((row for row in states if row['id'] == layer.get('object_id')), None)
        projection = projections.get(layer.get('object_id'))
        x, y = layer.get('x', .5), layer.get('y', .5)
        if state:
            x, y = state['world_x'], state['world_y']
        if projection:
            if not projection.get('visible', False):
                continue
            x, y = projection['screen_x'], projection['screen_y']
        with Image.open(layer['path']) as source:
            sprite = source.convert('RGBA')
        scale = layer.get('scale', 1)
        initial_projection = base_projections.get(layer.get('object_id'))
        if projection and initial_projection and initial_projection.get('size_ratio'):
            scale *= projection['size_ratio'] / initial_projection['size_ratio']
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError('图层投影缩放超出可表示范围')
        opacity = layer.get('opacity', 1)
        if opacity != 1:
            sprite.putalpha(sprite.getchannel('A').point(lambda alpha, opacity=opacity: round(alpha * opacity)))
        left, top = x * size[0] - sprite.width * scale / 2, y * size[1] - sprite.height * scale / 2
        overlay = sprite.transform(size, Image.Transform.AFFINE,
                                   (1 / scale, 0, -left / scale, 0, 1 / scale, -top / scale),
                                   resample=Image.Resampling.BICUBIC)
        canvas.paste(overlay, (0, 0), overlay)
    return canvas


def render_movie(board, shot, destination, variant='sketch', include_audio=False):
    import imageio_ffmpeg
    fps = board.get('fps', 24)
    writer = imageio_ffmpeg.write_frames(str(destination), (960, 540), fps=fps,
                                         codec='libx264', pix_fmt_in='rgb24', pix_fmt_out='yuv420p',
                                         macro_block_size=1, output_params=['-movflags', '+faststart'])
    count = math.ceil(shot['duration_ms'] * fps / 1000)
    try:
        writer.send(None)
        for index in range(count):
            image = render_frame(board, shot, min(shot['duration_ms'] - 1, round(index * 1000 / fps)), variant)
            writer.send(image.tobytes())
    finally:
        writer.close()
    if include_audio:
        from .storyboard_audio import sound_time
        sounds = [sound for sound in shot.get('sounds', []) if sound['kind'] != 'silence']
        if sounds:
            muxed = Path(destination).with_name(Path(destination).stem + '-sound.mp4')
            command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-i', str(destination)]
            filters, labels = [], []
            for index, sound in enumerate(sounds, 1):
                command += ['-i', sound['audio_path']]
                start = sound_time(board, shot, sound)
                source_in = sound.get('source_in_ms', 0) / 1000
                filters.append(f'[{index}:a]atrim=start={source_in}:duration={sound["duration_ms"] / 1000},asetpts=PTS-STARTPTS,adelay={start}:all=1[a{index}]')
                labels.append(f'[a{index}]')
            filters.append(''.join(labels) + f'amix=inputs={len(sounds)}:normalize=0[mixed]')
            command += ['-filter_complex', ';'.join(filters), '-map', '0:v', '-map', '[mixed]', '-c:v', 'copy',
                        '-c:a', 'aac', '-t', str(shot['duration_ms'] / 1000), '-y', str(muxed)]
            result = subprocess.run(command, capture_output=True)
            if result.returncode:
                muxed.unlink(missing_ok=True)
                raise ValueError('临时声轨合成失败：' + result.stderr.decode(errors='replace')[-1000:])
            muxed.replace(destination)
    return probe_video(destination)
