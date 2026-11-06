"""Content errors are repairable; conflicts/guards and external failures are not."""
import json
from pathlib import Path

import pytest
from PIL import Image
from test_screenplay_writing import body, table
from test_screenplay_writing import project as project

from narrative.asset_operations import AssetOperationsMixin
from narrative.content_errors import ContentConflictError, ContentGuardError, ContentValidationError
from narrative.graph_store import atomic_write_scope
from narrative.operations import Operations
from narrative.screenplay_operations import ScreenplayOperationsMixin
from narrative.screenplay_writing import WritingOperationsMixin
from narrative.storyboard_operations import StoryboardOperationsMixin


def test_screenplay_content_errors_do_not_hide_revision_or_target_guards(project):
    ops, _screenplay, segment = project
    table_state = table(ops, segment)
    one, two = table_state['units']
    before = ops.read_screenplay(_screenplay)
    with pytest.raises(ContentValidationError):
        ScreenplayOperationsMixin.update_script_segment(ops, segment, {'unsupported': True}, 1, '非法内容字段')
    with pytest.raises(ContentConflictError):
        WritingOperationsMixin.apply_scene_table_changes(ops, segment, [{'op': 'update', 'unit_id': one['id'], 'changes': {'plan': {'title': 'x'}}}], 0, reason='旧修订不能暂存')
    written_one, written_two = body(ops, one['id']), body(ops, two['id'])
    with pytest.raises(ContentGuardError):
        WritingOperationsMixin.apply_script_block_changes(ops, one['id'], [{'op': 'update', 'block_id': written_two['blocks'][0]['id'], 'block': {'type': 'action', 'payload': {'text': '跨场写入'}}}],
            [written_one['blocks'][0]['id']], written_one['body_revision'], reason='不能把跨目标变成待办')
    assert ops.read_screenplay(_screenplay)['screenplay']['title'] == before['screenplay']['title']
    assert ops.read_script_unit_context(two['id'])['unit']['blocks'] == written_two['blocks']


def test_storyboard_content_vs_approval_revision_and_foreign_target(project, tmp_path):
    ops, _screenplay, segment = project
    one, two = table(ops, segment)['units']
    first = StoryboardOperationsMixin.apply_storyboard_changes(ops, one['id'], 0, [{'op': 'create_shot', 'title': '本场镜头'}])
    foreign = StoryboardOperationsMixin.apply_storyboard_changes(ops, two['id'], 0, [{'op': 'create_shot', 'title': '另一场镜头'}])
    shot = first['shots'][0]['id']
    with pytest.raises(ContentValidationError):
        StoryboardOperationsMixin.apply_storyboard_changes(ops, one['id'], 1, [{'op': 'update_shot', 'shot_id': shot, 'title': ''}])
    with pytest.raises(ContentConflictError):
        StoryboardOperationsMixin.apply_storyboard_changes(ops, one['id'], 0, [{'op': 'update_shot', 'shot_id': shot, 'title': '过期'}])
    with pytest.raises(ContentGuardError):
        StoryboardOperationsMixin.apply_storyboard_changes(ops, one['id'], 1, [{'op': 'update_shot', 'shot_id': foreign['shots'][0]['id'], 'title': '跨场'}])
    assert ops.read_storyboard(one['id']) == first
    image = tmp_path / 'sketch.png'
    Image.new('RGB', (8, 8), 'white').save(image)
    drawn = ops.apply_storyboard_changes(one['id'], 1, [{'op': 'set_keyframe', 'shot_id': shot, 'offset_ms': 0, 'sketch_path': str(image)}])
    approved = ops.apply_storyboard_changes(one['id'], drawn['revision'], [{'op': 'set_approved', 'approved': True}])
    with pytest.raises(ContentGuardError):
        StoryboardOperationsMixin.apply_storyboard_changes(ops, one['id'], approved['revision'], [{'op': 'update_shot', 'shot_id': shot, 'title': '不可改'}])
    assert ops.read_storyboard(one['id']) == approved


@pytest.fixture
def asset_project(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    return Operations.create_graph_project(tmp_path / 'asset-atomic.nproj')


def media_files(ops):
    return [p for p in ops._asset_root().rglob('*') if p.is_file() and p.suffix != '.sqlite3']


def test_asset_both_image_groups_are_checked_before_any_copy(asset_project, tmp_path, monkeypatch):
    ops = asset_project
    source = tmp_path / 'source.png'
    source.write_bytes(b'Library source bytes')
    copied = []
    monkeypatch.setattr('narrative.asset_operations.shutil.copyfileobj', lambda *args: copied.append(args))
    with pytest.raises(ContentValidationError):
        AssetOperationsMixin.save_asset(ops, {'category': 'material', 'title': '资产', 'images': [{'path': str(source)}],
            'reference_images': [{'path': str(tmp_path / 'missing.png')}]})
    assert not copied and not media_files(ops)
    assert source.read_bytes() == b'Library source bytes'
    assert ops.list_assets('material')['assets'] == []


def test_asset_copy_failure_rolls_back_new_files_and_rows(asset_project, tmp_path, monkeypatch):
    ops = asset_project
    first, second = tmp_path / 'first.png', tmp_path / 'second.png'
    first.write_bytes(b'first Library bytes')
    second.write_bytes(b'second Library bytes')
    import shutil
    copy = shutil.copyfileobj
    calls = []
    def fail_second(source, output):
        calls.append(source.name)
        if len(calls) == 2:
            output.write(b'partial')
            raise OSError('isolated copy failure')
        return copy(source, output)
    monkeypatch.setattr('narrative.asset_operations.shutil.copyfileobj', fail_second)
    with pytest.raises(OSError, match='isolated'):
        AssetOperationsMixin.save_asset(ops, {'category': 'material', 'title': '资产', 'images': [{'path': str(first)}], 'reference_images': [{'path': str(second)}]})
    assert len(calls) == 2 and not media_files(ops)
    assert ops.list_assets('material')['assets'] == []
    assert first.read_bytes() == b'first Library bytes' and second.read_bytes() == b'second Library bytes'


def test_asset_attachment_and_files_follow_outer_graph_commit_or_rollback(asset_project, tmp_path):
    ops = asset_project
    source = tmp_path / 'reference.png'
    source.write_bytes(b'unchanged Library')
    with atomic_write_scope(ops._store) as db:
        saved = AssetOperationsMixin.save_asset(ops, {'category': 'material', 'title': 'literal FROM assets meta audit', 'reference_images': [{'path': str(source)}]})
        db.execute("INSERT INTO meta(key,value) VALUES('outer_asset_marker','committed')")
        assert saved['title'] == 'literal FROM assets meta audit'
        assert db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0] == '0'
    old_path = Path(saved['reference_images'][0]['path'])
    assert old_path.is_file() and ops.get_asset(saved['id'])['title'] == saved['title']
    with pytest.raises(RuntimeError, match='outer CAS'):
        with atomic_write_scope(ops._store):
            AssetOperationsMixin.save_asset(ops, {'id': saved['id'], 'title': '不能留下修改', 'images': [{'path': str(source)}]}, expected_revision=1)
            raise RuntimeError('outer CAS failure')
    assert ops.get_asset(saved['id'])['title'] == saved['title']
    assert media_files(ops) == [old_path] and old_path.read_bytes() == source.read_bytes()


def test_content_diagnostics_point_into_original_blocks_units_and_changes(project):
    ops, _screenplay, segment = project
    context = ops.read_scene_table_context(segment)
    with pytest.raises(ContentValidationError) as table_error:
        WritingOperationsMixin.submit_scene_table(ops, segment, [{'plan': {'title': 7}}],
            context['expected_outline_revision'], context['span_fingerprint'], context['context_fingerprint'], reason='场表字段定位')
    assert table_error.value.problems[0]['path'] == ['units', 0, 'plan', 'title']
    unit = table(ops, segment)['units'][0]
    context = ops.read_script_unit_context(unit['id'])
    with pytest.raises(ContentValidationError) as body_error:
        WritingOperationsMixin.submit_script_unit(ops, unit['id'], [{'type': 'action', 'payload': {'text': 7}}],
            context['expected_plan_revision'], context['expected_body_revision'], context['span_fingerprint'], context['context_fingerprint'],
            context['plan_fingerprint'], reason='正文payload真实路径')
    assert body_error.value.problems[0]['path'] == ['blocks', 0, 'payload', 'text']
    with pytest.raises(ContentValidationError) as edits_error:
        WritingOperationsMixin.apply_script_block_changes(ops, unit['id'],
            [{'op': 'insert', 'block': {'type': 'action', 'payload': {'text': 7}}}], [], unit['body_revision'], reason='去除union伪层级')
    assert edits_error.value.problems[0]['path'] == ['changes', 0, 'block', 'payload', 'text']
    board = StoryboardOperationsMixin.apply_storyboard_changes(ops, unit['id'], 0, [{'op': 'create_shot', 'title': '镜头'}])
    with pytest.raises(ContentValidationError) as shot_error:
        StoryboardOperationsMixin.apply_storyboard_changes(ops, unit['id'], board['revision'],
            [{'op': 'update_shot', 'shot_id': board['shots'][0]['id'], 'title': ''}])
    assert shot_error.value.problems[0]['path'] == ['changes', 0]


@pytest.mark.parametrize('error', [json.JSONDecodeError('damaged stored JSON', 'x', 0), UnicodeError('stored bytes cannot decode')])
def test_stored_data_decode_errors_are_not_repairable_content(error):
    from narrative.content_errors import content_guard, content_item, content_write
    @content_write
    def write():
        raise error
    @content_guard
    def guard():
        raise error
    for function in (write, guard):
        with pytest.raises(type(error)) as caught:
            function()
        assert caught.value is error
    with pytest.raises(type(error)) as caught:
        with content_item(['blocks', 0]):
            raise error
    assert caught.value is error


def test_revision_conflict_precedes_bad_body_payload(project):
    ops, _screenplay, segment = project
    unit = table(ops, segment)['units'][0]
    stale = ops.read_script_unit_context(unit['id'])
    body(ops, unit['id'])
    with pytest.raises(ContentConflictError):
        WritingOperationsMixin.submit_script_unit(ops, unit['id'], [{'type': 'action', 'payload': {'text': 7}}],
            stale['expected_plan_revision'], stale['expected_body_revision'], stale['span_fingerprint'], stale['context_fingerprint'],
            stale['plan_fingerprint'], reason='过期不能伪装成可修内容')


def test_screenplay_and_storyboard_do_not_commit_a_shared_outer_transaction(project):
    ops, _screenplay, segment = project
    unit = table(ops, segment)['units'][0]
    before = ops.read_script_unit_context(unit['id'])['unit']
    context = ops.read_script_unit_context(unit['id'])
    with pytest.raises(RuntimeError, match='stage CAS'):
        with atomic_write_scope(ops._store):
            written = WritingOperationsMixin.submit_script_unit(ops, unit['id'], [{'type': 'action', 'payload': {'text': '尚未提交的正文'}}],
                context['expected_plan_revision'], context['expected_body_revision'], context['span_fingerprint'], context['context_fingerprint'],
                context['plan_fingerprint'], reason='共享正文写入')
            StoryboardOperationsMixin.apply_storyboard_changes(ops, unit['id'], 0,
                [{'op': 'create_shot', 'title': '尚未提交的镜头', 'source_block_ids': [written['blocks'][0]['id']]}])
            raise RuntimeError('stage CAS failure')
    assert ops.read_script_unit_context(unit['id'])['unit'] == before
    assert ops.read_storyboard(unit['id'])['shots'] == []


def test_animation_metadata_can_be_repaired_but_image_io_is_not_staged(project, tmp_path):
    from narrative.storyboard_video import StoryboardVideoMixin
    ops, _screenplay, segment = project
    unit = table(ops, segment)['units'][0]
    board = StoryboardOperationsMixin.apply_storyboard_changes(ops, unit['id'], 0, [{'op': 'create_shot', 'title': '动画'}])
    shot = board['shots'][0]['id']
    with pytest.raises(ContentValidationError) as invalid:
        StoryboardVideoMixin.set_storyboard_animation(ops, unit['id'], shot, 1, 'invalid')
    assert invalid.value.problems[0]['path'] == ['mode']
    image = tmp_path / 'unreadable.png'
    image.write_bytes(b'not actually a readable image')
    with pytest.raises(OSError):
        StoryboardVideoMixin.set_storyboard_animation(ops, unit['id'], shot, 1, 'layers', [{'path': str(image), 'start_ms': 0, 'end_ms': 500}])
    assert ops.read_storyboard(unit['id']) == board
