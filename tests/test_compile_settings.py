"""Project typography is validated, persistent and used by compilation."""
import copy
import json

import pytest
from agent_fixtures import project as project
from test_screenplay_core import seed, span


def test_compile_settings_persistence_validation_and_formats(project):
    initial = project.load_compile_settings()
    settings = copy.deepcopy(initial['settings'])
    settings['font_family'] = '宋体'
    settings['font_size_pt'] = 14
    settings['line_height'] = 1.8
    settings['page_margins_mm']['top'] = 30
    settings['paragraphs']['scene']['alignment'] = 'center'
    settings['paragraphs']['action']['indent_left_mm'] = 10
    saved = project.save_compile_settings(settings, initial['revision'], '排版测试')
    assert saved['status'] == 'accepted' and saved['revision'] == 1
    persisted = project.load_compile_settings()
    assert {key: saved[key] for key in persisted} == persisted
    saved = persisted
    with pytest.raises(ValueError, match='已变化'):
        project.save_compile_settings(settings, 0, '过期')
    for bad in [settings | {'font_size_pt': -1}, settings | {'font_family': '</style>'}, settings | {'unknown': 1}]:
        assert project.save_compile_settings(bad, 1, '非法')['status'] == 'staged'
    with pytest.raises(ValueError):
        project.save_compile_settings(settings | {'line_height': float('nan')}, 1, '非法JSON数值')
    assert project.load_compile_settings() == saved
    ids = seed(project)
    span(project, ids)
    result = project.compile_screenplay(ids['_script'])
    assert result['compile_settings'] == saved
    assert 'font-size:14.0pt' in result['preview_html']
    assert 'margin:30.0mm' in result['preview_html']
    assert '.scene{text-align:center' in result['preview_html']
    assert 'font-size:14.0pt' in result['qt_preview_html']
    structured = json.loads(project.compile_screenplay(ids['_script'], 'json')['text'])
    assert structured['compile_settings'] == saved
    for fmt in ['text', 'fountain']:
        output = project.compile_screenplay(ids['_script'], fmt)
        assert any(d['code'] == 'COMPILE_FORMAT_STYLE_UNSUPPORTED' for d in output['diagnostics'])
    with project._store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM actions WHERE action='save_compile_settings'").fetchone()[0] == 1

def test_qt_document_uses_actual_paragraph_margins_and_independent_speaker():
    from PySide6.QtGui import QGuiApplication, QTextDocument

    from narrative.compile_settings import default_settings
    from narrative.screenplay_compile import render_qt_html
    app = QGuiApplication.instance() or QGuiApplication([])
    assert app
    settings = default_settings()
    settings['font_size_pt'] = 17
    settings['line_height'] = 1.8
    settings['paragraphs']['action'].update(indent_left_mm=7, space_before_pt=6)
    settings['paragraphs']['speaker'].update(indent_left_mm=3)
    settings['paragraphs']['dialogue'].update(indent_left_mm=20)
    elements = [{'kind': 'scene', 'text': '门口', 'number': 1}, {'kind': 'action', 'text': '行动'}, {'kind': 'dialogue', 'text': '对白', 'speaker': '甲', 'parenthetical': '低声'}]
    document = QTextDocument()
    document.setHtml(render_qt_html(elements, settings))
    blocks = {}
    block = document.begin()
    while block.isValid():
        blocks[block.text()] = block
        block = block.next()
    assert blocks['行动'].blockFormat().leftMargin() == pytest.approx(7 * 96 / 25.4, abs=1)
    assert blocks['行动'].blockFormat().topMargin() == pytest.approx(8)
    assert blocks['行动'].blockFormat().lineHeight() == pytest.approx(180)
    assert blocks['行动'].begin().fragment().charFormat().fontPointSize() == pytest.approx(17)
    assert blocks['甲'].blockFormat().leftMargin() == pytest.approx(3 * 96 / 25.4, abs=1)
    assert blocks['对白'].blockFormat().leftMargin() == pytest.approx(20 * 96 / 25.4, abs=1)
    assert blocks['(低声)'].blockFormat().leftMargin() == pytest.approx(20 * 96 / 25.4, abs=1)

    assert blocks['1. 门口'].begin().fragment().charFormat().fontPointSize() == pytest.approx(17)
    assert blocks['(低声)'].blockFormat().topMargin() == 0
    assert blocks['(低声)'].blockFormat().bottomMargin() == 0
