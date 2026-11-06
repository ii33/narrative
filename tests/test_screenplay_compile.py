"""Compilation keeps authored blocks while separating diagnostics from output."""
from agent_fixtures import project as project
from test_screenplay_core import delete, seed, segment, span


def test_compile_formats_escape_numbering_and_retained_body(project, tmp_path):
    ids = seed(project)
    bound = span(project, ids)['span']['id']
    script_id = ids['_script']
    seg = segment(project, script_id, bound)
    c = project.read_scene_table_context(seg['id'])
    units = project.submit_scene_table(seg['id'], [
        {'scene_number': '99', 'plan': {'title': '<门>', 'summary': '甲敲门开门。', 'source_event_ids': [ids['甲敲门'], ids['甲开门']], 'location_text': '<屋>', 'interior_exterior': 'INT'}},
        {'plan': {'title': '尾声', 'summary': '甲进屋。', 'source_event_ids': [ids['甲进屋']]}}], c['expected_outline_revision'], c['span_fingerprint'], c['context_fingerprint'], reason='编译测试场表')['units']
    c = project.read_script_unit_context(units[0]['id'])
    project.submit_script_unit(units[0]['id'], [
        {'type': 'action', 'payload': {'text': '<script>危险&安全</script>'}},
        {'type': 'dialogue', 'payload': {'speaker_id': ids['甲'], 'parenthetical': '轻声', 'text': '你好。'}},
        {'type': 'transition', 'payload': {'kind': 'cut', 'text': '切黑'}}],
        c['expected_plan_revision'], c['expected_body_revision'], c['span_fingerprint'], c['context_fingerprint'], c['plan_fingerprint'], reason='编译测试正文')
    delete(project, ids, ids['甲敲门'])
    result = project.compile_screenplay(script_id)
    assert [e['number'] for e in result['elements'] if e['kind'] == 'scene'] == [1, 2]
    assert '<script>' not in result['preview_html']
    assert '&lt;script&gt;' in result['preview_html']
    assert '轻声' in result['qt_preview_html'] and 'margin-left:' in result['qt_preview_html']
    assert result['diagnostics'] and '边界已删除' not in result['text']
    assert '你好。' in result['text']
    for fmt in ['text', 'fountain', 'json', 'html']:
        path = tmp_path / ('compiled.' + fmt)
        out = project.compile_screenplay(script_id, format=fmt, path=path)
        assert path.read_text(encoding='utf-8') == out['text']
    assert '@甲' in project.compile_screenplay(script_id, format='fountain')['text']
    assert project.compile_screenplay(script_id, format='pdf')['requires_path']
    assert project.compile_screenplay(script_id, format='docx')['requires_path']


def test_simultaneous_dialogue_and_title_layout():
    from narrative.screenplay_compile import render_html, render_qt_html, render_text
    elements = [{"kind": "title", "text": "双声"},
                {"kind": "dialogue", "text": "一起说。", "speaker": "甲", "simultaneous_group_id": "private-group-hash"}]
    for rendered in [render_html(elements, "双声"), render_qt_html(elements), render_text(elements), render_text(elements, True)]:
        assert "甲（同时）" in rendered
        assert "private-group-hash" not in rendered
    assert '<h1 align="center">' in render_qt_html(elements)
    assert "break-after:page" not in render_html(elements, "双声")
