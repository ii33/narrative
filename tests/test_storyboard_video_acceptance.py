"""Independent local-media/provider review; HTTP service is an explicit test stub."""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board

from narrative.storyboard_media import probe_video
from narrative.storyboard_video_provider import build_request


def ready(project, tmp_path):
    ops, unit, block = setup_board(project)
    before_body = ops.read_script_unit_context(unit)['unit']
    image = tmp_path / 'source.png'
    Image.new('RGB', (960, 540), (40, 100, 160)).save(image)
    board = ops.apply_storyboard_changes(unit, 0, [{'op': 'set_fps', 'fps': 25}, {'op': 'create_shot', 'title': '验收镜头', 'duration_ms': 2000, 'source_block_ids': [block]}])
    shot = board['shots'][0]['id']
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_keyframe', 'shot_id': shot, 'offset_ms': 0, 'sketch_path': str(image)}])
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_approved', 'approved': True}])
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_keyframe', 'shot_id': shot, 'offset_ms': 0, 'style_path': str(image)}])
    return ops, unit, shot, board, image, before_body


def video(path, seconds=3):
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25', '-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True)
    return path


def test_actual_clean_export_import_trim_retime_and_preserve_timeline(project, tmp_path):
    ops, unit, shot, board, _, body = ready(project, tmp_path)
    path = tmp_path / 'clean.mp4'
    exported = ops.render_storyboard_animatic(unit, shot, str(path))
    media = probe_video(path)
    assert exported['annotation_free'] and not exported['include_audio']
    assert media['duration_ms'] == 2000 and media['fps'] == 25
    reader = imageio_ffmpeg.read_frames(str(path), pix_fmt='rgb24')
    try:
        meta, frame = next(reader), next(reader)
        picture = Image.frombytes('RGB', meta['size'], frame)
        assert all(abs(actual - expected) <= 6 for actual, expected in zip(picture.getpixel((480, 270)), (40, 100, 160), strict=True))
    finally:
        reader.close()
    source = video(tmp_path / 'original.mp4')
    raw = source.read_bytes()
    with pytest.raises(ValueError, match='时长不一致'):
        ops.attach_storyboard_video(unit, shot, board['revision'], str(source), 500, 1500, 'trim')
    assert ops.read_storyboard(unit) == board
    selected = ops.attach_storyboard_video(unit, shot, board['revision'], str(source), 500, 1500, 'retime')
    clip = selected['shots'][0]['selected_video']
    assert clip['in_ms'] == 500 and clip['out_ms'] == 1500 and clip['speed'] == .5
    assert selected['shots'][0]['duration_ms'] == 2000
    assert Path(clip['path']) != source and Path(clip['path']).read_bytes() == raw and source.read_bytes() == raw
    assert ops.read_script_unit_context(unit)['unit'] == body
    with pytest.raises(ValueError, match='超出实际素材'):
        ops.attach_storyboard_video(unit, shot, selected['revision'], str(source), 0, 4000, 'retime')
    assert ops.read_storyboard(unit) == selected


def test_h3_intents_are_mutually_exclusive_actual_inputs_only(tmp_path):
    image = tmp_path / 'frame.png'
    Image.new('RGB', (960, 540), 'gray').save(image)
    motion = video(tmp_path / 'motion.mp4', 2)
    kwargs = {'model': 'MiniMax-H3', 'resolution': '768P', 'duration_ms': 4000, 'prompt': '保持手与物件的关系', 'images': [image]}
    endpoints = build_request(**kwargs, mode='endpoints')
    reference = build_request(**kwargs, mode='reference_motion', motion_path=motion)
    assert [r.get('role') for r in endpoints['content'][1:]] == ['first_frame']
    assert {r.get('role') for r in reference['content'][1:]} == {'reference_video', 'reference_image'}
    with pytest.raises(ValueError, match='不与运动参考混用'):
        build_request(**kwargs, mode='endpoints', motion_path=motion)
    with pytest.raises(ValueError, match='干净草图动画'):
        build_request(**kwargs, mode='reference_motion')


def test_prepare_zero_network_missing_key_and_submit_at_most_once(project, tmp_path, monkeypatch):
    ops, unit, shot, board, _, _ = ready(project, tmp_path)
    requests = []
    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            payload = json.dumps({'task_id': 'local-video-job'}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.delenv('MINIMAX_API_KEY', raising=False)
    try:
        settings = ops.read_storyboard_video_settings()
        ops.save_storyboard_video_settings({'base_url': f'http://127.0.0.1:{server.server_port}'}, settings['revision'], api_key='')
        prepared = ops.prepare_storyboard_video(unit, shot, board['revision'], 'endpoints', 4000, prompt='隔离准备，不提交')
        generation = prepared['shots'][0]['generations'][0]
        assert not requests and generation['status'] == 'prepared'
        with pytest.raises(ValueError, match='密钥'):
            ops.submit_storyboard_video(unit, shot, generation['id'], prepared['revision'])
        assert not requests and ops.read_storyboard(unit) == prepared
        settings = ops.read_storyboard_video_settings()
        ops.save_storyboard_video_settings({}, settings['revision'], api_key='test-only-local-key')
        submitted = ops.submit_storyboard_video(unit, shot, generation['id'], prepared['revision'])
        assert len(requests) == 1
        assert submitted['shots'][0]['generations'][0]['provider_task_id'] == 'local-video-job'
        with pytest.raises(ValueError, match='不会重复提交'):
            ops.submit_storyboard_video(unit, shot, generation['id'], submitted['revision'])
        assert len(requests) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_phase23_script_two_image_stages_with_explicit_offline_transport(tmp_path, monkeypatch):
    import importlib
    import sys
    from io import BytesIO

    from narrative.storyboard_style_provider import ComfyClient
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    script = importlib.import_module('accept_storyboard_phase23')
    output = tmp_path / 'offline-script'
    monkeypatch.delenv('MINIMAX_API_KEY', raising=False)
    jobs, views = [], []
    monkeypatch.setattr(ComfyClient, 'upload', lambda *args: 'offline-upload.png')
    def offline_transport(client, method, route, body=None, content_type=None):
        if method == 'POST' and route == '/prompt':
            workflow = json.loads(body)['prompt']
            output_node = next(key for key, node in workflow.items() if node.get('_meta', {}).get('title') == 'Storyboard Output')
            jobs.append(output_node)
            return json.dumps({'prompt_id': f'offline-job-{len(jobs)}'}).encode()
        if route.startswith('/history/'):
            job = route.split('/')[-1]
            node = jobs[int(job.rsplit('-', 1)[1]) - 1]
            return json.dumps({job: {'status': {'status_str': 'success', 'completed': True}, 'outputs': {node: {'images': [{'filename': 'offline.png', 'type': 'output'}]}}}}).encode()
        if route.startswith('/view?'):
            views.append(route)
            stream = BytesIO()
            Image.new('RGB', (960, 544), 'gray' if len(views) == 1 else 'blue').save(stream, format='PNG')
            return stream.getvalue()
        raise AssertionError(f'Unexpected offline transport: {method} {route}')
    monkeypatch.setattr(ComfyClient, 'request', offline_transport)
    monkeypatch.setattr(sys, 'argv', ['offline-script', '--output', str(output)])
    assert script.main() == 0
    result = json.loads((output / 'phase2-3-assessment.json').read_text(encoding='utf-8'))
    assert all(result['checks'].values()) and result['video_service_real_validation'] == 'not_completed'
    assert result['image_jobs'] == 2 and result['video_provider_posts'] == 0
    assert [item['stage'] for item in result['uploads']].count('sketch') == 2
    assert [item['stage'] for item in result['uploads']].count('style') == 2


def test_png_layer_follows_action_hold_keeps_static_and_camera_exports(project, tmp_path):
    from PIL import ImageChops, ImageDraw

    from narrative.storyboard_media import render_frame
    ops, unit, shot, board, _, _ = ready(project, tmp_path)
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_approved', 'approved': False}, {'op': 'create_object', 'name': '活动图层', 'kind': 'prop', 'x': .2, 'y': .5}])
    obj = board['objects'][0]['id']
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 0, 'x': .2, 'interpolation': 'linear'}, {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 1999, 'x': .8}])
    sprite = tmp_path / 'sprite.png'
    Image.new('RGBA', (40, 40), (255, 0, 0, 255)).save(sprite)
    board = ops.set_storyboard_animation(unit, shot, board['revision'], 'layers', [{'path': str(sprite), 'object_id': obj, 'start_ms': 0, 'end_ms': 2000}])
    first, last = [render_frame(board, board['shots'][0], t) for t in (0, 1500)]
    assert ImageChops.difference(first, last).getbbox() is not None
    board = ops.set_storyboard_animation(unit, shot, board['revision'], 'hold')
    assert board['shots'][0]['animation']['layers']
    assert ImageChops.difference(render_frame(board, board['shots'][0], 0), render_frame(board, board['shots'][0], 1500)).getbbox() is None
    background = tmp_path / 'grid.png'
    image = Image.new('RGB', (960, 540), 'white')
    draw = ImageDraw.Draw(image)
    for x in range(0, 960, 60):
        draw.line((x, 0, x, 540), fill='black', width=3)
    image.save(background)
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_keyframe', 'shot_id': shot, 'offset_ms': 0, 'sketch_path': str(background)}, {'op': 'set_camera_preset', 'shot_id': shot, 'preset': 'zoom', 'amount': -20, 'start_ms': 0, 'end_ms': 1999}])
    board = ops.set_storyboard_animation(unit, shot, board['revision'], 'camera')
    assert ImageChops.difference(render_frame(board, board['shots'][0], 0), render_frame(board, board['shots'][0], 1500)).getbbox() is not None
    exported = ops.render_storyboard_animatic(unit, shot, str(tmp_path / 'camera.mp4'))
    assert probe_video(exported['path'])['duration_ms'] == 2000


@pytest.mark.parametrize('change', ['request_file', 'future_action'])
def test_reviewed_request_and_future_shared_action_tampering_send_zero_posts(project, tmp_path, monkeypatch, change):
    from narrative import storyboard_video_provider
    ops, unit, shot, board, _, _ = ready(project, tmp_path)
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_approved', 'approved': False}, {'op': 'create_object', 'name': '未来动作', 'kind': 'person', 'x': .2, 'y': .5}])
    obj = board['objects'][0]['id']
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 0, 'x': .2}, {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 1500, 'x': .5}, {'op': 'set_approved', 'approved': True}])
    settings = ops.read_storyboard_video_settings()
    ops.save_storyboard_video_settings({}, settings['revision'], api_key='local-unit-only')
    prepared = ops.prepare_storyboard_video(unit, shot, board['revision'], 'endpoints', 4000, prompt='隔离请求')
    generation = prepared['shots'][0]['generations'][0]
    if change == 'request_file':
        Path(generation['request_path']).write_text('{}', encoding='utf-8')
    else:
        prepared = ops.apply_storyboard_changes(unit, prepared['revision'], [{'op': 'set_approved', 'approved': False}, {'op': 'set_action_keyframe', 'object_id': obj, 'time_ms': 1500, 'x': .8}, {'op': 'set_approved', 'approved': True}])
    calls = []
    def forbidden_submit(*args):
        calls.append(args)
        raise AssertionError('Review drift must be rejected before provider transport')
    monkeypatch.setattr(storyboard_video_provider, 'submit', forbidden_submit)
    with pytest.raises(ValueError, match='变化'):
        ops.submit_storyboard_video(unit, shot, generation['id'], prepared['revision'])
    assert not calls


def test_provider_500_failed_state_and_no_second_submission(project, tmp_path, monkeypatch):
    from narrative import storyboard_video_provider
    ops, unit, shot, board, _, _ = ready(project, tmp_path)
    settings = ops.read_storyboard_video_settings()
    ops.save_storyboard_video_settings({}, settings['revision'], api_key='local-unit-only')
    prepared = ops.prepare_storyboard_video(unit, shot, board['revision'], 'endpoints', 4000)
    generation = prepared['shots'][0]['generations'][0]
    calls = []
    def failed_transport(*args):
        calls.append(args)
        raise ValueError('视频服务返回500：isolated stub failure')
    monkeypatch.setattr(storyboard_video_provider, '_request', failed_transport)
    with pytest.raises(ValueError, match='返回500'):
        ops.submit_storyboard_video(unit, shot, generation['id'], prepared['revision'])
    current = ops.read_storyboard(unit)
    assert current['shots'][0]['generations'][0]['status'] == 'failed' and len(calls) == 1
    with pytest.raises(ValueError, match='不会重复提交'):
        ops.submit_storyboard_video(unit, shot, generation['id'], current['revision'])
    assert len(calls) == 1


def test_success_refresh_downloads_real_video_without_automatically_selecting(project, tmp_path, monkeypatch):
    import shutil

    from narrative import storyboard_video_provider
    ops, unit, shot, board, _, _ = ready(project, tmp_path)
    settings = ops.read_storyboard_video_settings()
    ops.save_storyboard_video_settings({}, settings['revision'], api_key='local-unit-only')
    prepared = ops.prepare_storyboard_video(unit, shot, board['revision'], 'endpoints', 4000)
    generation = prepared['shots'][0]['generations'][0]
    monkeypatch.setattr(storyboard_video_provider, 'submit', lambda *args: {'task_id': 'local-result-job'})
    submitted = ops.submit_storyboard_video(unit, shot, generation['id'], prepared['revision'])
    real_video = video(tmp_path / 'service-output.mp4')
    queries = []
    def query(*args):
        queries.append(args)
        return {'task': {'id': 'local-result-job', 'status': 'succeeded', 'content': {'url': 'https://example.invalid/video.mp4'}, 'usage': None}}
    monkeypatch.setattr(storyboard_video_provider, 'query', query)
    monkeypatch.setattr(storyboard_video_provider, 'download', lambda url, destination: shutil.copyfile(real_video, destination))
    finished = ops.refresh_storyboard_video(unit, shot, generation['id'], submitted['revision'])
    result = finished['shots'][0]['generations'][0]
    assert result['status'] == 'succeeded' and result['media']['duration_ms'] == 3000
    assert Path(result['output_path']).read_bytes() == real_video.read_bytes()
    assert not finished['shots'][0].get('selected_video')
    assert ops.refresh_storyboard_video(unit, shot, generation['id'], finished['revision']) == finished
    assert len(queries) == 1
