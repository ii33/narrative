"""Real media metadata caches and atomic verification of imported file bytes."""
import shutil
from pathlib import Path

import imageio_ffmpeg
import pytest
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board

from narrative import storyboard_media
from narrative.project_package import layout


def movie(path, color):
    writer = imageio_ffmpeg.write_frames(str(path), (64, 64), fps=5, macro_block_size=1)
    try:
        writer.send(None)
        for _ in range(5):
            writer.send(bytes(color) * (64 * 64))
    finally:
        writer.close()
    return path


def test_probe_cache_returns_copies_and_invalidates_changed_file(tmp_path, monkeypatch):
    red = movie(tmp_path / 'red.mp4', (220, 20, 20))
    blue = movie(tmp_path / 'blue.mp4', (20, 20, 220))
    storyboard_media._probe_video.cache_clear()
    original = storyboard_media.subprocess.run
    calls = []
    def checked(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(storyboard_media.subprocess, 'run', checked)
    first = storyboard_media.probe_video(red)
    checksum = first['media_sha256']
    first['duration_ms'] = 0
    assert storyboard_media.probe_video(red)['duration_ms'] == 1000
    assert len(calls) == 1
    shutil.copyfile(blue, red)
    assert storyboard_media.probe_video(red)['media_sha256'] != checksum
    assert len(calls) == 2


def test_import_copied_video_bytes_must_match_validated_source(project, tmp_path, monkeypatch):
    ops, unit, _ = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{'op': 'create_shot', 'title': '视频选段', 'duration_ms': 1000}])
    red = movie(tmp_path / 'red.mp4', (220, 20, 20))
    blue = movie(tmp_path / 'blue.mp4', (20, 20, 220)).read_bytes()
    before = red.read_bytes()
    original = shutil.copyfileobj
    def tamper(source, target, *args, **kwargs):
        original(source, target, *args, **kwargs)
        if Path(source.name).suffix == '.mp4':
            target.seek(0)
            target.write(blue)
            target.truncate()
    monkeypatch.setattr(shutil, 'copyfileobj', tamper)
    with pytest.raises(ValueError, match='复制期间视频源文件已变化'):
        ops.attach_storyboard_video(unit, board['shots'][0]['id'], board['revision'], str(red), 0, 1000)
    assert ops.read_storyboard(unit) == board and red.read_bytes() == before
    assert not list((layout(ops.project_info().path).material / 'storyboard').glob('*.mp4'))
