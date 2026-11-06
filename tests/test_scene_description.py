"""Scene description remains separate from action across editing and compilation."""
from test_screenplay_writing import body, table
from test_screenplay_writing import project as project


def test_scene_description_roundtrip_manual_retype_and_independent_style(project):
    ops, script_id, segment_id = project
    unit = table(ops, segment_id)['units'][0]
    written = body(ops, unit['id'], [
        {'type': 'scene_description', 'payload': {'text': '窗外暮色沉沉，室内只有一盏灯。'}},
        {'type': 'action', 'payload': {'text': '甲抬手关窗。'}}])
    assert [b['type'] for b in written['blocks']] == ['scene_description', 'action']
    settings = ops.load_compile_settings()
    settings['settings']['paragraphs']['scene_description']['indent_left_mm'] = 11
    settings['settings']['paragraphs']['action']['indent_left_mm'] = 3
    ops.save_compile_settings(settings['settings'], settings['revision'], '区分段落')
    compiled = ops.compile_screenplay(script_id)
    assert [e['kind'] for e in compiled['elements'] if e.get('block_id')] == ['scene_description', 'action']
    assert '.scene_description{text-align:left;margin-left:11.0mm' in compiled['preview_html']
    assert '.action{text-align:left;margin-left:3.0mm' in compiled['preview_html']
    assert '<p class="scene_description">窗外' in compiled['preview_html']
    old = written['blocks'][1]
    changed = body(ops, unit['id'], [
        {'id': written['blocks'][0]['id'], 'type': 'scene_description', 'payload': written['blocks'][0]['payload']},
        {'id': old['id'], 'type': 'scene_description', 'payload': old['payload']}])
    assert changed['blocks'][1]['id'] == old['id']
    assert changed['blocks'][1]['payload'] == old['payload']
    assert changed['blocks'][1]['type'] == 'scene_description'
    assert '甲抬手关窗。' in ops.compile_screenplay(script_id, 'fountain')['text']
