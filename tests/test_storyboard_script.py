import pytest
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board


def test_shot_writing_and_shared_intents_are_atomic(project):
    ops, unit, block = setup_board(project)
    change = ops.apply_storyboard_changes
    board = change(unit, 0, [{'op': 'create_sound_intent', 'kind': 'silence', 'text': '两镜间持续静默'}])
    intent = board['sound_intents'][0]['id']
    board = change(unit, 1, [{'op': 'create_shot', 'title': title, 'description': '窗边等候', 'shot_language': '中景',
                            'visual_elements': [{'kind': 'set', 'name': '窗', 'usage': '人物身后', 'asset_id': None}],
                            'sound_intent_ids': [intent]} for title in ['一', '二']])
    assert all(shot['sound_intent_ids'] == [intent] for shot in board['shots'])
    shot = board['shots'][0]['id']
    board = change(unit, 2, [{'op': 'create_sound', 'shot_id': shot, 'kind': 'silence', 'intent_id': intent, 'time_ms': 0, 'duration_ms': 1000}])
    for edits in [[{'op': 'delete_sound_intent', 'intent_id': intent}],
                  [{'op': 'update_shot', 'shot_id': shot, 'sound_intent_ids': []}],
                  [{'op': 'update_shot', 'shot_id': shot, 'visual_elements': [{'kind': 'set', 'name': '窗', 'usage': '', 'asset_id': 'missing'}]}]]:
        with pytest.raises(ValueError):
            change(unit, 3, edits)
        assert ops.read_storyboard(unit) == board
    final = change(unit, 3, [{'op': 'update_sound_intent', 'intent_id': intent, 'text': '安静'}])
    assert final['sound_intents'][0]['text'] == '安静'


def test_source_sound_reference_and_shot_notes(project):
    from test_screenplay_writing import body
    ops, unit, _ = setup_board(project)
    written = body(ops, unit, [{'type': 'dialogue', 'payload': {'speaker_label': '甲', 'text': '等等'}}])
    source = written['blocks'][0]['id']
    board = ops.apply_storyboard_changes(unit, 0, [{'op': 'create_sound_intent', 'kind': 'dialogue', 'text': '', 'source_block_id': source},
                                               {'op': 'create_shot', 'title': '回头', 'sound_notes': '画外续音，停顿后入'}])
    assert board['sound_intents'][0]['source_block_id'] == source
    assert board['shots'][0]['sound_notes'] == '画外续音，停顿后入'
    assert ops.read_script_unit_context(unit)['unit']['blocks'][0]['payload']['text'] == '等等'
    with pytest.raises(ValueError, match='本场对白或声音'):
        ops.apply_storyboard_changes(unit, 1, [{'op': 'update_sound_intent', 'intent_id': board['sound_intents'][0]['id'], 'source_block_id': 'missing'}])


def test_visual_asset_delete_requires_unbinding(project):
    ops, unit, _ = setup_board(project)
    asset = ops.save_asset({'category': 'material', 'title': '窗格参考'})
    board = ops.apply_storyboard_changes(unit, 0, [{'op': 'create_shot', 'title': '窗前', 'visual_elements': [{'kind': 'set', 'name': '窗', 'usage': '背景', 'asset_id': asset['id']}]}])
    with pytest.raises(ValueError, match='分镜引用'):
        ops.delete_asset(asset['id'])
    ops.apply_storyboard_changes(unit, 1, [{'op': 'update_shot', 'shot_id': board['shots'][0]['id'], 'visual_elements': []}])
    assert ops.delete_asset(asset['id'])['removed'] == asset['id']
