"""Actual Qt JavaScript geometry and QML cache behavior, no model requests."""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import pytest
from test_chat_scroll import transcript_shell

CASES = {
    'chain': [('a', 'b'), ('b', 'c'), ('c', 'd'), ('d', 'e')],
    'clusters': [('a', 'b'), ('b', 'c'), ('a', 'c'), ('d', 'e'), ('e', 'f'), ('d', 'f')],
    'star': [('a', other) for other in 'bcdef'],
    'isolates': [],
}


def graph(case):
    return ([{'id': x, 'name': x} for x in 'abcdef'],
            [{'from': a, 'to': b} for a, b in CASES[case]])


@pytest.mark.parametrize('case', CASES)
def test_relation_geometry_is_stable_nonoverlapping_and_relation_driven(case):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QJSEngine

    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QJSEngine()
    source = Path('src/narrative/qml/RelationLayout.js').read_text(encoding='utf-8').replace('.pragma library', '')
    assert not engine.evaluate(source).isError()
    persons, links = graph(case)
    radii = {n['id']: 24 + i * 2 for i, n in enumerate(persons)}
    def run(nodes, edges):
        result = engine.evaluate('JSON.stringify(layout(' + ','.join(json.dumps(v) for v in (nodes, edges, radii, 600, 420)) + '))')
        assert not result.isError()
        return json.loads(result.toString())
    result = run(persons, links)
    assert result == run(list(reversed(persons)), list(reversed(links)))
    positions, scale = result['positions'], result['fitScale']
    def distance(a, b):
        return math.hypot(positions[a]['x']-positions[b]['x'], positions[a]['y']-positions[b]['y'])
    for a, b in itertools.combinations(positions, 2):
        assert distance(a, b) >= positions[a]['r'] + positions[b]['r'] - .01
    for identifier, point in positions.items():
        assert point['r'] == radii[identifier]
        assert (abs(point['x']) + point['r']) * scale <= 300
        assert (abs(point['y']) + point['r']) * scale <= 210
    if case == 'chain':
        assert sum(distance(a,b) for a,b in CASES[case])/4 < distance('a','e')
    if case == 'clusters':
        within = [distance(a,b) for a,b in CASES[case]]
        cross = [distance(a,b) for a in 'abc' for b in 'def']
        assert sum(within)/len(within) < sum(cross)/len(cross)
    assert app is not None


@pytest.mark.parametrize('width', [1392, 1100])
def test_relation_qml_layout_fits_and_zoom_pan_preserve_cached_positions(tmp_path, monkeypatch, width):
    from PySide6.QtTest import QSignalSpy

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setProperty('currentPage', 'relations')
        ui.window.setWidth(width)
        ui.settle()
        page = ui.window.findChild(object, 'relationPage')
        persons, links = graph('clusters')
        page.apply({'persons': persons, 'edges': links})
        ui.settle()
        def plain(value):
            return value.toVariant() if hasattr(value, 'toVariant') else value
        before = plain(page.property('graphLayout'))
        spy = QSignalSpy(page.graphLayoutChanged)
        page.zoomAt(100, 100, 1.18)
        page.panBy(14, -9)
        ui.settle()
        assert spy.count() == 0
        assert plain(page.property('graphLayout')) == before
        canvas = ui.window.findChild(object, 'relationCanvas')
        for point in before['positions'].values():
            assert (abs(point['x']) + point['r']) * before['fitScale'] <= canvas.width()/2
            assert (abs(point['y']) + point['r']) * before['fitScale'] <= canvas.height()/2


def test_relation_layout_accepts_prototype_and_delimiter_ids_without_key_collisions():
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QJSEngine

    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QJSEngine()
    source = Path('src/narrative/qml/RelationLayout.js').read_text(encoding='utf-8').replace('.pragma library', '')
    assert not engine.evaluate(source).isError()
    ids = ['constructor', '__proto__', 'a|b', 'a', 'b|c', 'c']
    persons = [{'id': identifier} for identifier in ids]
    links = [{'from': 'a|b', 'to': 'c'}, {'from': 'a', 'to': 'b|c'},
             {'from': 'constructor', 'to': '__proto__'}]
    args = (persons, links, {identifier: 24 for identifier in ids}, 600, 420)
    result = engine.evaluate('JSON.stringify(layout(' + ','.join(json.dumps(v) for v in args) + '))')
    assert not result.isError()
    data = json.loads(result.toString())
    assert set(data['positions']) == set(ids)
    assert sorted(sorted(c) for c in data['components']) == sorted([['__proto__', 'constructor'], ['a|b', 'c'], ['a', 'b|c']])
    assert app is not None


def test_relation_spacing_and_compact_labels_remain_readable_with_real_pan_zoom(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from test_static_check_ui import plain

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'relations')
        ui.settle()
        page=ui.window.findChild(object,'relationPage')
        data={'persons':[{'id':'a','name':'甲'},{'id':'b','name':'乙'}], 'edges':[{'from':'a','to':'b','kind':'故交'}]}
        page.apply(data)
        ui.wait(lambda:bool(plain(page.property('displayLabelLayout'))['edges']))
        positions=plain(page.property('graphLayout'))['positions']
        a,b=positions['a'],positions['b']
        camera=plain(page.property('graphLabelLayout'))['fitScale']
        gap=math.hypot(a['x']-b['x'],a['y']-b['y'])-a['r']-b['r']
        assert gap*camera>=85
        assert gap/min(a['r'],b['r'])>4.5
        box=plain(page.property('displayLabelLayout'))['edges'][0]['label']
        assert box['h']*camera<22
        assert page.property('labelScreenFontSize')==11
        assert page.property('zoom')==1
        canvas=ui.window.findChild(object,'relationCanvas')
        point=canvas.mapToScene(QPointF(30,30)).toPoint()
        QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        QTest.mouseMove(ui.window,point+QPoint(35,20),30)
        QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point+QPoint(35,20))
        ui.settle()
        assert page.property('panX')==pytest.approx(35)
        assert page.property('panY')==pytest.approx(20)
        wheel_point=canvas.mapToScene(QPointF(canvas.width()/2,canvas.height()/2))
        ui.app.sendEvent(ui.window,QWheelEvent(wheel_point,ui.window.mapToGlobal(wheel_point.toPoint()),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False))
        ui.settle()
        assert page.property('zoom')>1
        assert plain(page.property('graphLayout'))['positions']==positions
        assert plain(page.property('edges'))==data['edges']
