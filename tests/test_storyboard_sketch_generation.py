"""Provider stub tests validate atomic image binding, never real image quality."""
import json
from pathlib import Path

import pytest
from PIL import Image
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board


def setup(project):
    ops, unit, block = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{'op': 'create_shot', 'title': '窗边手接道具', 'source_block_ids': [block]}])
    return ops, unit, board['shots'][0]['id']


def test_provider_gets_real_basis_and_generated_image_not_guide(project, monkeypatch):
    ops, unit, shot = setup(project)
    observed = {}
    def generate(guide, prompt, copies_to, reference_images):
        observed.update(guide=guide.read_bytes(), prompt=prompt, refs=reference_images)
        Image.new('RGB', (31, 19), 'red').save(copies_to)
        return {'provider': 'stub-only', 'job_id': 'job-1', 'usage': None, 'response': {'raw': 'audit-only'}}
    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', generate, raising=False)
    board = ops.generate_storyboard_sketch(unit, shot, 0, {}, 1, prompt='手部接触清楚', simple_color=True)
    path = Path(board['shots'][0]['keyframes'][0]['sketch_path'])
    assert path.read_bytes() != observed['guide']
    with Image.open(path) as image:
        assert image.size == (31, 19)
    assert '甲等待。' in observed['prompt'] and '手部接触清楚' in observed['prompt']
    assert '遮挡' in observed['prompt'] and '简洁色彩' in observed['prompt']
    assert 'raw' not in json.dumps(board['sketch_render'])
    with ops._store.connect() as db:
        audit = json.loads(db.execute("SELECT summary FROM actions WHERE action='apply_storyboard_changes' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    assert audit['sketch_generation']['provider']['response']['raw'] == 'audit-only'
    assert audit['sketch_generation']['prompt'] == observed['prompt']
    assert audit['sketch_basis']['body_revision'] == 1


@pytest.mark.parametrize('failure', ['provider', 'invalid_image', 'board_changed', 'style_changed'])
def test_failure_never_falls_back_or_replaces_old_image(project, monkeypatch, tmp_path, failure):
    ops, unit, shot = setup(project)
    old = tmp_path / 'old.png'
    Image.new('RGB', (8, 8), 'blue').save(old)
    board = ops.apply_storyboard_changes(unit, 1, [{'op': 'set_keyframe', 'shot_id': shot, 'offset_ms': 0, 'sketch_path': str(old)}])
    saved = Path(board['shots'][0]['keyframes'][0]['sketch_path'])
    original = saved.read_bytes()
    def generate(guide, prompt, copies_to, reference_images):
        if failure == 'provider':
            raise RuntimeError('offline provider failure')
        if failure == 'invalid_image':
            copies_to.write_text('not image', encoding='utf-8')
        else:
            Image.new('RGB', (12, 12)).save(copies_to)
        if failure == 'board_changed':
            ops.apply_storyboard_changes(unit, 2, [{'op': 'update_shot', 'shot_id': shot, 'title': '新标题'}])
        if failure == 'style_changed':
            ops.save_project_style('新的光影', 0)
        return {'provider': 'stub-only'}
    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', generate, raising=False)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        ops.generate_storyboard_sketch(unit, shot, 0, {}, 2)
    current = ops.read_storyboard(unit)
    assert current['shots'][0]['keyframes'][0]['sketch_path'] == str(saved)
    assert saved.read_bytes() == original
    assert current['revision'] == (3 if failure == 'board_changed' else 2)


def test_preflight_rejects_without_provider_request(project, monkeypatch):
    ops, unit, shot = setup(project)
    def fail(*args, **kwargs):
        pytest.fail('preflight must not contact provider')
    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', fail, raising=False)
    for identifier, offset, revision in [(shot, 0, 0), ('foreign', 0, 1), (shot, 3000, 1)]:
        with pytest.raises(ValueError):
            ops.generate_storyboard_sketch(unit, identifier, offset, {}, revision)


@pytest.mark.parametrize('mutate', [False, True])
def test_actual_asset_references_are_sent_and_change_rejects_stale_binding(project, monkeypatch, tmp_path, mutate):
    ops, unit, shot = setup(project)
    image = tmp_path / 'reference.png'
    Image.new('RGB', (10, 10), 'green').save(image)
    asset = ops.save_asset({'category': 'material', 'title': '磨砂表面', 'detail': '粗糙且不反光', 'reference_images': [{'path': str(image)}]})
    ops.apply_storyboard_changes(unit, 1, [{'op': 'create_object', 'name': '道具', 'kind': 'prop', 'asset_id': asset['id']}])
    seen = []
    def generate(guide, prompt, copies_to, reference_images):
        seen.extend(reference_images)
        assert '粗糙且不反光' in prompt
        assert reference_images[0].read_bytes() == image.read_bytes()
        Image.new('RGB', (12, 12)).save(copies_to)
        if mutate:
            ops.save_asset({'id': asset['id'], 'detail': '已变更'})
        return {'provider': 'stub-only', 'usage': None}
    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', generate)
    if mutate:
        with pytest.raises(ValueError, match='依据已变化'):
            ops.generate_storyboard_sketch(unit, shot, 0, {}, 2)
        assert ops.read_storyboard(unit)['shots'][0]['keyframes'] == []
    else:
        assert ops.generate_storyboard_sketch(unit, shot, 0, {}, 2)['shots'][0]['keyframes']
    assert seen
