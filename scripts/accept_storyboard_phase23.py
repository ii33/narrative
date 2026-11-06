"""Two distinct real image stages plus local media closure; H3 remains explicit."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import threading
import time
import wave
from pathlib import Path

from accept_storyboard import seed as base_seed
from PIL import Image, ImageDraw


def seed(ops, root):
    f = base_seed(ops)
    reference = root / 'synthetic-blue-fabric-reference.png'
    image = Image.new('RGB', (256, 256), (35, 80, 150))
    draw = ImageDraw.Draw(image)
    for x in range(0, 256, 4):
        draw.line((x, 0, x, 256), fill=(60, 110, 180))
        draw.line((0, x, 256, x), fill=(25, 65, 125))
    image.save(reference)
    material = ops.save_asset({'category': 'material', 'title': '蓝色布纹参考', 'detail': '人物外套使用低饱和蓝色布面；这是程序合成的隔离测试参考图。', 'reference_images': [{'path': str(reference), 'caption': '程序合成参考，非模型生成'}]})
    ops.save_project_style('写实电影剧照，低饱和冷蓝色布面外套，暖侧光，细腻但清晰的形体与材质。', 0)
    audio = root / 'controlled-tone.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b''.join(struct.pack('<h', int(6000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(8000)))
    board = ops.apply_storyboard_changes(f['unit_id'], 0, [
        {'op': 'set_fps', 'fps': 25},
        {'op': 'create_shot', 'title': '旧车站门口，近距离递交铜钥匙，手指与钥匙接触清晰', 'duration_ms': 2000, 'source_block_ids': [f['block']]},
        {'op': 'create_object', 'name': '递钥匙者', 'kind': 'person', 'x': .42, 'y': .5, 'asset_id': material['id']},
        {'op': 'create_object', 'name': '接钥匙者', 'kind': 'person', 'x': .58, 'y': .5},
        {'op': 'create_marker', 'name': '钥匙接触', 'time_ms': 500},
    ])
    shot, marker = board['shots'][0]['id'], board['markers'][0]['id']
    board = ops.apply_storyboard_changes(f['unit_id'], board['revision'], [
        {'op': 'set_camera_preset', 'shot_id': shot, 'preset': 'fixed', 'start_ms': 0, 'end_ms': 1999},
        {'op': 'create_sound', 'shot_id': shot, 'kind': 'effect', 'label': '布料摩擦临时音', 'time_ms': 0, 'duration_ms': 200, 'audio_path': str(audio)},
        {'op': 'create_sound', 'shot_id': shot, 'kind': 'effect', 'label': '钥匙接触临时音', 'marker_id': marker, 'duration_ms': 500, 'audio_path': str(audio)},
        {'op': 'create_sound', 'shot_id': shot, 'kind': 'silence', 'label': '等待停顿', 'time_ms': 1200, 'duration_ms': 300},
    ])
    f.update(shot_id=shot, marker_id=marker, asset_id=material['id'])
    return f, {'screenplay': ops.read_screenplay(f['screenplay_id']), 'graph': ops.graph_snapshot(), 'assets': {c:ops.list_assets(c) for c in ('appearance', 'item', 'location', 'material')}}


def execute(ops, f, before, root, report, stage):
    unit, shot = f['unit_id'], f['shot_id']
    board = ops.read_storyboard(unit)
    stage[0] = 'sketch'
    board = ops.generate_storyboard_sketch(unit, shot, 0, {'framing': 'medium', 'light': 'left'}, board['revision'], prompt='用近距离接触表现交接，不让人物相距过远；参考蓝色布料外观，素描保持黑白。')
    frame = board['shots'][0]['keyframes'][0]
    sketch = Path(frame['sketch_path'])
    shutil.copyfile(sketch, root / 'actual-sketch.png')
    report['sketch_receipt'] = board.get('sketch_render')
    board = ops.apply_storyboard_changes(unit, board['revision'], [{'op': 'set_approved', 'approved': True}])
    stage[0] = 'style'
    board = ops.render_storyboard_style(unit, shot, frame['id'], board['revision'])
    style = Path(board['shots'][0]['keyframes'][0]['style_path'])
    shutil.copyfile(style, root / 'actual-style.png')
    report['style_receipt'] = board.get('style_render')
    stage[0] = 'local_media'
    preview = ops.preview_storyboard(unit, 750)
    clean = ops.render_storyboard_animatic(unit, shot, str(root / 'clean-animatic.mp4'), include_audio=False)
    with_audio = ops.render_storyboard_animatic(unit, shot, str(root / 'temporary-audio-review.mp4'), include_audio=True)
    imported = ops.attach_storyboard_video(unit, shot, board['revision'], clean['path'], 0, 2000, 'trim')
    retimed = ops.attach_storyboard_video(unit, shot, imported['revision'], clean['path'], 500, 1500, 'retime')
    endpoint = ops.prepare_storyboard_video(unit, shot, retimed['revision'], 'endpoints', 4000, prompt='保持构图与手和钥匙接触关系')
    reference = ops.prepare_storyboard_video(unit, shot, endpoint['revision'], 'reference_motion', 4000, prompt='按干净草图动画保持交接动作')
    settings = ops.read_storyboard_video_settings()
    report.update(video_key_configured=settings['has_api_key'], video_service_real_validation='not_completed', video_usage=None, video_provider_posts=0,
        video_not_submitted_reason='Missing video service credential; prepared inputs only, no video submission.' if not settings['has_api_key'] else 'This QA authorizes only image jobs and local media; no H3 submission authorized.',
        phase2_preview=preview, clean_export=clean, audio_export=with_audio, selected_video=retimed['shots'][0]['selected_video'], generations=reference['shots'][0]['generations'])
    checks = {'two_distinct_image_stages': bool(report.get('sketch_receipt')) and bool(report.get('style_receipt')),
        'style_not_manual_sketch_copy': sketch.read_bytes() != style.read_bytes(),
        'marker_sound_seek': any(sound['source_position_ms'] == 250 for sound in preview['sounds']),
        'clean_export_real_video': clean['media']['duration_ms'] == 2000 and clean['annotation_free'],
        'audio_export_real_video': with_audio['media']['duration_ms'] == 2000,
        'retime_preserves_timeline': retimed['shots'][0]['duration_ms'] == 2000 and retimed['shots'][0]['selected_video']['speed'] == .5,
        'body_unchanged': ops.read_screenplay(f['screenplay_id']) == before['screenplay'],
        'graph_unchanged': ops.graph_snapshot() == before['graph'],
        'assets_unchanged_after_seed': {c:ops.list_assets(c) for c in before['assets']} == before['assets'],
        'two_intents_prepared_not_submitted': [g['mode'] for g in reference['shots'][0]['generations']] == ['endpoints', 'reference_motion'] and all(g['status'] == 'prepared' for g in reference['shots'][0]['generations'])}
    report.update(checks=checks, status='local_passed_h3_not_completed' if all(checks.values()) else 'not_passed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=180)
    args = parser.parse_args()
    started = time.monotonic()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ['NARRATIVE_PROVIDER_DB'] = str(root / 'providers.sqlite3')
    from narrative.operations import Operations
    from narrative.storyboard_style_provider import ComfyClient
    ops = Operations.create_graph_project(root / '导演台完整验收.nproj')
    f, before = seed(ops, root)
    baseline = json.dumps(before, ensure_ascii=False, sort_keys=True, indent=2)
    (root / 'baseline.json').write_text(baseline, encoding='utf-8')
    (root / 'source-context.json').write_text(json.dumps(ops.read_script_unit_context(f['unit_id']), ensure_ascii=False, indent=2), encoding='utf-8')
    report = {'status': 'running', 'agent_version': 'conversation-agent/39', 'storyboard_prompt_version': '4', 'qa_version': 'director-phase23-image-chain/1', 'fixture': f, 'input_sha256': hashlib.sha256(baseline.encode()).hexdigest(),
        'fixture_origin': 'Controlled source/scene, synthetic blue-fabric reference and audible test tone; none claimed as AI-generated.',
        'automatic_retry': False, 'llm_requests': 0, 'image_usage': None, 'video_usage': None, 'video_provider_posts': 0,
        'external_observation_seconds': args.seconds, 'requests': [], 'uploads': [], 'video_service_real_validation': 'not_completed'}
    stage = ['preparation']
    stop, settled = threading.Event(), threading.Event()
    original_request, original_upload = ComfyClient.request, ComfyClient.upload
    def upload(client, path):
        destination = root / f"{stage[0]}-input-{len(report['uploads']) + 1}.png"
        shutil.copyfile(path, destination)
        report['uploads'].append({'stage': stage[0], 'path': str(destination)})
        return original_upload(client, path)
    def request(client, method, route, body=None, content_type=None):
        if stop.is_set():
            raise ValueError('External QA observation budget reached, no further job')
        jobs = sum(row['method'] == 'POST' and row['route'] == '/prompt' for row in report['requests'])
        if method == 'POST' and route == '/prompt' and jobs >= 2:
            raise ValueError('QA permits only one sketch job and one style job')
        index = len(report['requests']) + 1
        row = {'stage': stage[0], 'method': method, 'route': route, 'index': index}
        report['requests'].append(row)
        if body is not None:
            payload = body.encode() if isinstance(body, str) else body
            (root / f'request-{index}.bin').write_bytes(payload)
            if route == '/prompt':
                (root / f'{stage[0]}-provider-workflow.json').write_bytes(payload)
        try:
            raw = original_request(client, method, route, body, content_type)
            (root / f'response-{index}.bin').write_bytes(raw)
            return raw
        except Exception as exc:
            row['error'] = str(exc)
            raise
    ComfyClient.request, ComfyClient.upload = request, upload
    def work():
        try:
            execute(ops, f, before, root, report, stage)
        except Exception as exc:
            report.update(status='not_passed', error_type=type(exc).__name__, error=str(exc), failed_stage=stage[0])
        finally:
            settled.set()
    threading.Thread(target=work, daemon=True).start()
    settled.wait(max(0, args.seconds - (time.monotonic() - started)))
    if not settled.is_set():
        stop.set()
        report.update(status='not_completed', stop_reason='External observation budget exhausted; no retry or substitute image')
    report['elapsed_seconds'] = time.monotonic() - started
    report['image_jobs'] = sum(row['method'] == 'POST' and row['route'] == '/prompt' for row in report['requests'])
    if settled.is_set() and report.get('checks') is not None:
        report['checks']['exactly_one_job_per_image_stage'] = [row['stage'] for row in report['requests'] if row['method'] == 'POST' and row['route'] == '/prompt'] == ['sketch', 'style']
        if not all(report['checks'].values()):
            report['status'] = 'not_passed'
    (root / 'phase2-3-assessment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if settled.is_set():
        ComfyClient.request, ComfyClient.upload = original_request, original_upload
    print(json.dumps({'status': report['status'], 'directory': str(root)}, ensure_ascii=False))
    return 0 if report['status'] == 'local_passed_h3_not_completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
