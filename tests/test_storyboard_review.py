"""Explicit review reads actual media/current dependencies without changing them."""
import copy
import hashlib
import json
from pathlib import Path

from PIL import Image
from test_screenplay_writing import body
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board
from test_storyboard_video_acceptance import ready, video


def test_sketch_compact_source_reopens_and_related_basis_changes_only(project, monkeypatch):
    ops, unit, block = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{'op': 'create_shot', 'title': '等待', 'source_block_ids': [block]}])
    shot = board['shots'][0]['id']
    def generate(guide, prompt, copies_to, reference_images):
        Image.new('RGB', (20, 12)).save(copies_to)
        return {'provider': 'stub-only', 'job_id': 'job-local', 'raw_response': {'large': 'audit only'}}
    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', generate)
    board = ops.generate_storyboard_sketch(unit, shot, 0, {'framing': 'close'}, 1, '视线朝门口', True)
    frame = board['shots'][0]['keyframes'][0]
    assert frame['sketch_source'] == {'user_prompt': '视线朝门口', 'layout': {'framing': 'close'}, 'simple_color': True,
                                      'provider': 'stub-only', 'job_id': 'job-local', 'body_revision': 1, 'asset_ids': []}
    assert 'raw_response' not in json.dumps(frame)
    before = ops.read_storyboard(unit)
    dbpath = Path(ops.project_info().path)
    digest = hashlib.sha256(dbpath.read_bytes()).hexdigest()
    checked = ops.check_storyboard(unit)
    assert checked['ok'] and not checked['needs_review']
    assert ops.read_storyboard(unit) == before and hashlib.sha256(dbpath.read_bytes()).hexdigest() == digest
    ops.save_asset({'category': 'material', 'title': '未关联材质'})
    assert not ops.check_storyboard(unit)['needs_review']
    body(ops, unit, [{'id': block, 'type': 'action', 'payload': {'text': '甲走到门口。'}}])
    checked = ops.check_storyboard(unit)
    assert any(row['code'] == 'sketch_basis_changed' for row in checked['diagnostics'])
    assert checked['affected_frames'] == [{'shot_id': shot, 'frame_id': frame['id']}]
    assert Path(frame['sketch_path']).is_file()


def test_successful_video_still_reports_current_basis_change_and_missing_media(project, tmp_path):
    ops, unit, shot_id, board, _, _ = ready(project, tmp_path)
    board = ops.prepare_storyboard_video(unit, shot_id, board['revision'], 'endpoints', 4000)
    shot = board['shots'][0]
    generation = copy.deepcopy(shot['generations'][0])
    movie = video(tmp_path / 'result.mp4', 1)
    generation.update(status='succeeded', provider_task_id='stub-task', output_path=str(movie))
    board = ops._save_storyboard_media(board, shot, {'generations': [generation]}, 'offline_test_fixture')
    assert ops.check_storyboard(unit)['generations'][0]['basis_status'] == 'current'
    # An unrelated asset does not invalidate this generation.
    ops.save_asset({'category': 'material', 'title': '另一个场的材质'})
    assert not ops.check_storyboard(unit)['needs_review']
    ops.save_project_style('新的创作约定', 0)
    before = ops.read_storyboard(unit)
    checked = ops.check_storyboard(unit)
    assert checked['generations'][0]['status'] == 'succeeded' and checked['generations'][0]['stale']
    assert any(row['code'] == 'generation_basis_changed' for row in checked['diagnostics'])
    assert ops.read_storyboard(unit) == before
    Path(before['shots'][0]['keyframes'][0]['sketch_path']).unlink()
    missing = ops.check_storyboard(unit)
    assert not missing['ok'] and any(row['code'] == 'frame_image_missing_or_invalid' for row in missing['diagnostics'])
    assert missing['affected_shots'] == [{'shot_id': shot_id, 'title': '验收镜头'}]


def test_unreferenced_body_block_does_not_make_sketch_stale(project, monkeypatch):
    ops, unit, block = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{'op': 'create_shot', 'title': '单独依据', 'source_block_ids': [block]}])
    def generate(guide, prompt, copies_to, reference_images):
        Image.new('RGB', (10, 10)).save(copies_to)
        return {'provider': 'stub-only'}
    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', generate)
    ops.generate_storyboard_sketch(unit, board['shots'][0]['id'], 0, {}, 1)
    body(ops, unit, [{'id': block, 'type': 'action', 'payload': {'text': '甲等待。'}},
                     {'type': 'action', 'payload': {'text': '另一区域的乙收拾文件。'}}])
    assert not any(row['code'] == 'sketch_basis_changed' for row in ops.check_storyboard(unit)['diagnostics'])


def test_related_asset_change_marks_only_its_frame_basis(project, monkeypatch):
    ops, unit, block = setup_board(project)
    asset = ops.save_asset({'category': 'material', 'title': '纸面', 'detail': '粗糙'})
    board = ops.apply_storyboard_changes(unit, 0, [{'op': 'create_object', 'name': '文件', 'kind': 'prop', 'asset_id': asset['id']},
                                                   {'op': 'create_shot', 'title': '文件', 'source_block_ids': [block]}])
    def generate(guide, prompt, copies_to, reference_images):
        Image.new('RGB', (10, 10)).save(copies_to)
        return {'provider': 'stub-only'}
    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', generate)
    board = ops.generate_storyboard_sketch(unit, board['shots'][0]['id'], 0, {}, 1)
    assert not ops.check_storyboard(unit)['needs_review']
    ops.save_asset({'id': asset['id'], 'detail': '光滑'})
    assert any(row['code'] == 'sketch_basis_changed' for row in ops.check_storyboard(unit)['diagnostics'])
    assert ops.read_storyboard(unit)['shots'] == board['shots']


def test_missing_audio_and_selected_video_are_independent_diagnostics(project, tmp_path):
    from test_storyboard_audio import wav
    ops, unit, shot, board, _, _ = ready(project, tmp_path)
    board = ops.apply_storyboard_changes(unit, board['revision'], [
        {'op': 'set_approved', 'approved': False},
        {'op': 'create_sound', 'shot_id': shot, 'kind': 'effect', 'time_ms': 0, 'duration_ms': 500, 'audio_path': wav(tmp_path / 'sound.wav')},
    ])
    source = video(tmp_path / 'selected.mp4', 1)
    board = ops.attach_storyboard_video(unit, shot, board['revision'], str(source), 0, 1000, 'retime')
    saved = ops.read_storyboard(unit)
    Path(saved['shots'][0]['sounds'][0]['audio_path']).unlink()
    Path(saved['shots'][0]['selected_video']['path']).unlink()
    result = ops.check_storyboard(unit)
    codes = {row['code'] for row in result['diagnostics']}
    assert not result['ok'] and {'sound_media_missing_or_changed', 'selected_video_missing_or_changed'} <= codes
    assert ops.read_storyboard(unit) == saved
