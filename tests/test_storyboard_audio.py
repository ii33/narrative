"""Real local audio probes and atomic director-timeline sound edits."""
import math
import struct
import wave
from pathlib import Path

import pytest
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board

from narrative.storyboard_audio import apply_audio, preview_audio, probe_audio, validate_audio


def wav(path, milliseconds=2000):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b''.join(struct.pack('<h', int(6000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(milliseconds * 8)))
    return str(path.resolve())


def test_audio_probe_real_duration_and_malformed_file(tmp_path):
    path = tmp_path / 'source.wav'
    assert probe_audio(wav(path, 1250))['media_duration_ms'] == 1250
    path.write_bytes(b'not audio')
    with pytest.raises(ValueError, match='实际音频时长'):
        probe_audio(str(path.resolve()))
    with pytest.raises(ValueError, match='缺失'):
        probe_audio(str((tmp_path / 'missing.wav').resolve()))


def test_free_sound_source_trim_active_interval_and_silence(tmp_path):
    board = {'shots': [{'id': 'shot-1', 'duration_ms': 3000}]}
    path = wav(tmp_path / 'voice.wav')
    apply_audio(board, {'op': 'create_sound', 'shot_id': 'shot-1', 'kind': 'dialogue', 'label': '等一下', 'time_ms': 500, 'source_in_ms': 250, 'duration_ms': 1000, 'audio_path': path}, lambda _: 'sound-1')
    apply_audio(board, {'op': 'create_sound', 'shot_id': 'shot-1', 'kind': 'silence', 'label': '停顿', 'time_ms': 1500, 'duration_ms': 500}, lambda _: 'sound-2')
    validate_audio(board)
    shot = board['shots'][0]
    assert preview_audio(board, shot, 499) == []
    active = preview_audio(board, shot, 1000)[0]
    assert active['source_position_ms'] == 750 and active['playback_offset_ms'] == 500
    assert preview_audio(board, shot, 1500)[0]['kind'] == 'silence'
    assert preview_audio(board, shot, 2000) == []
    with pytest.raises(ValueError, match='实际音频'):
        apply_audio(board, {'op': 'update_sound', 'shot_id': 'shot-1', 'sound_id': 'sound-1', 'source_in_ms': 1500}, lambda _: '')
        validate_audio(board)


def test_audio_import_copy_and_invalid_batch_rollback(project, tmp_path):
    ops, unit, block = setup_board(project)
    before = ops.apply_storyboard_changes(unit, 0, [{'op': 'create_shot', 'title': '声音', 'duration_ms': 3000, 'source_block_ids': [block]}])
    shot = before['shots'][0]['id']
    source = Path(wav(tmp_path / 'Library-original.wav'))
    raw = source.read_bytes()
    board = ops.apply_storyboard_changes(unit, before['revision'], [{'op': 'create_sound', 'shot_id': shot, 'kind': 'effect', 'time_ms': 250, 'duration_ms': 500, 'audio_path': str(source)}])
    sound = board['shots'][0]['sounds'][0]
    copied = Path(sound['audio_path'])
    assert copied != source and copied.read_bytes() == raw and source.read_bytes() == raw
    assert ops.preview_storyboard(unit, 500)['sounds'][0]['source_position_ms'] == 250
    with pytest.raises(ValueError):
        ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'delete_sound', 'shot_id': shot, 'sound_id': sound['id']}, {'op': 'create_sound', 'shot_id': shot, 'kind': 'effect', 'time_ms': 0, 'audio_path': str(tmp_path / 'missing.wav')}])
    assert ops.read_storyboard(unit) == board
    source.unlink()
    assert ops.preview_storyboard(unit, 500)['sounds'][0]['audio_path'] == str(copied)


@pytest.mark.parametrize('suffix', ['mp3', 'ogg'])
def test_mainstream_audio_actual_header_probe(tmp_path, suffix):
    import subprocess

    import imageio_ffmpeg
    source = wav(tmp_path / 'source.wav', 1200)
    target = tmp_path / ('encoded.' + suffix)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-y', '-i', source, str(target)], check=True, capture_output=True)
    metadata = probe_audio(str(target.resolve()))
    assert 1100 <= metadata['media_duration_ms'] <= 1400


def test_shared_marker_moves_sound_but_free_time_stays_and_conflicts_reject(tmp_path):
    board = {'markers': [{'id': 'moment-1', 'name': '触碰', 'time_ms': 750}],
             'shots': [{'id': 'shot-1', 'duration_ms': 2000, 'action_start_ms': 250}]}
    for identifier, anchor in [('sound-bound', {'marker_id': 'moment-1'}), ('sound-free', {'time_ms': 500})]:
        apply_audio(board, {'op': 'create_sound', 'shot_id': 'shot-1', 'kind': 'silence', 'duration_ms': 200, **anchor}, lambda _, i=identifier:i)
    validate_audio(board)
    assert len(preview_audio(board, board['shots'][0], 500)) == 2
    board['markers'][0]['time_ms'] = 1000
    validate_audio(board)
    assert [s['id'] for s in preview_audio(board, board['shots'][0], 500)] == ['sound-free']
    assert [s['id'] for s in preview_audio(board, board['shots'][0], 750)] == ['sound-bound']
    board['markers'][0]['time_ms'] = 2300
    with pytest.raises(ValueError, match='超出镜头'):
        validate_audio(board)


def test_sound_requires_one_coordinate_and_real_media(tmp_path):
    board = {'shots': [{'id': 'shot-1', 'duration_ms': 3000}]}
    with pytest.raises(ValueError, match='只能选一个'):
        apply_audio(board, {'op': 'create_sound', 'shot_id': 'shot-1', 'kind': 'silence', 'duration_ms': 10, 'time_ms': 0, 'marker_id': 'moment-1'}, lambda _: 'sound-1')
    with pytest.raises(ValueError, match='不属于本场'):
        apply_audio(board, {'op': 'create_sound', 'shot_id': 'another-scene-shot'}, lambda _: 'sound-1')
    apply_audio(board, {'op': 'create_sound', 'shot_id': 'shot-1', 'kind': 'effect', 'time_ms': 0, 'duration_ms': 10}, lambda _: 'sound-1')
    with pytest.raises(ValueError, match='完整路径'):
        validate_audio(board)


def test_sound_conflicts_collect_every_anchored_interval():
    from narrative.storyboard_audio import audio_conflicts
    board = {'markers': [{'id': 'moment-1', 'name': '触碰', 'time_ms': 1800}],
        'shots': [{'id': 'shot-1', 'duration_ms': 1000, 'action_start_ms': 0, 'sounds': [
            {'id': 'sound-1', 'marker_id': 'moment-1', 'duration_ms': 200},
            {'id': 'sound-2', 'marker_id': 'moment-1', 'duration_ms': 400},
            {'id': 'sound-free', 'time_ms': 0, 'duration_ms': 100}]}]}
    conflicts = audio_conflicts(board)
    assert [row['id'] for row in conflicts] == ['sound-1', 'sound-2']
    assert all(row['offset_ms'] == 1800 for row in conflicts)


def test_readonly_audio_validation_does_not_reprobe_large_media(tmp_path, monkeypatch):
    from narrative import storyboard_audio
    board = {'shots': [{'id': 'shot-1', 'duration_ms': 3000}]}
    apply_audio(board, {'op': 'create_sound', 'shot_id': 'shot-1', 'kind': 'effect', 'time_ms': 0, 'audio_path': wav(tmp_path / 'sound.wav')}, lambda _: 'sound-1')
    def forbidden_probe(*args):
        raise AssertionError('Readonly preview must not hash or probe audio again')
    monkeypatch.setattr(storyboard_audio, 'probe_audio', forbidden_probe)
    validate_audio(board, check_media=False)
