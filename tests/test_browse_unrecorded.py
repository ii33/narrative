"""QML presentation of legacy missing-holder placeholders, without writing graph facts."""
from __future__ import annotations

import pytest


@pytest.fixture
def browse(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QT_QUICK_BACKEND", "software")
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtWidgets import QApplication

    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge

    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)
    page = window.findChild(object, "browsePage")
    try:
        yield page
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("value,explanation", [("未记录", "尚无该属性的记录"),
                                             ("未披露", "截至当前阅读位置尚未披露")])
def test_missing_holder_is_not_a_historical_or_scene_derived_fact(browse, value, explanation):
    placeholder = {"property_id": "holder", "property_ids": ["holder"], "aspect": "holder",
        "label": "持有者", "category": "归属与持有", "value": value, "value_node": None,
        "scope": "world", "scopes": ["world"], "is_current": False, "derived": True,
        "state": "unrecorded", "disclosed_at": "", "records": []}
    result = browse.buildItem(placeholder).toVariant()
    assert result["valueText"] == value
    assert result["derivedText"] == explanation
    assert result["disclosedText"] == ""
    assert result["records"] == [] and result["recordIds"] == []
    assert "历史" not in result["valueText"] and "场景" not in result["derivedText"]


@pytest.mark.parametrize("current", [True, False])
def test_real_scene_derived_progress_retains_source_and_history(browse, current):
    item = {"property_id": "storyline_progress", "value": "正在交谈", "scope": "world",
            "derived": True, "from_scene": {"name": "门前交谈"}, "is_current": current,
            "disclosed_at": "E1", "records": [{"id": "actual-record"}]}
    result = browse.buildItem(item).toVariant()
    assert result["valueText"] == ("" if current else "（历史）") + "正在交谈"
    assert result["derivedText"] == "来自场景 门前交谈，随阅读位置更新"
    assert result["disclosedText"] == "披露于 E1"
    assert result["recordIds"] == ["actual-record"]
