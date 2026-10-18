from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.operations import OperationError, Operations


def test_packaged_texts_import_exactly_without_models_or_duplicate_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "narrative.jobs.start", lambda *a, **kw: pytest.fail("载入内置原文不应调用模型")
    )
    app = Operations(tmp_path / "book.sqlite3")
    catalog = app.builtin_text_catalog()
    assert len(catalog) == 4
    assert {entry["extent"] for entry in catalog} == {"全文", "节选"}
    for entry in catalog:
        article = app.builtin_text(entry["id"])
        source_id = app.import_builtin_text(entry["id"])
        source = app.source(source_id)
        assert source["text"] == article["text"]
        assert source["hash"] == entry["sha256"]
        assert len(source["text"]) == entry["characters"]
        assert app.source_attribution(source_id) == entry
        revision = app.project_info().revision
        assert app.import_builtin_text(entry["id"]) == source_id
        assert app.project_info().revision == revision
        quote = source["text"].splitlines()[0][:15]
        evidence = app.locate(source_id, quote)[0]
        assert app.evidence_excerpt(evidence).quote == quote
    assert len(app.sources()) == 4
    assert not app.candidates() and not app.runs()
    assert not app.entities() and not app.assertions() and not app.events() and not app.records()


def test_builtin_source_attribution_survives_backup_restore_and_reopen(tmp_path):
    app = Operations(tmp_path / "book.sqlite3")
    source_id = app.import_builtin_text("kongyiji")
    attribution = app.source_attribution(source_id)
    backup = app.backup(tmp_path / "backup.sqlite3")
    Operations.restore_file(backup, tmp_path / "restored.sqlite3")
    restored = Operations(tmp_path / "restored.sqlite3")
    assert restored.source(source_id) == app.source(source_id)
    assert restored.source_attribution(source_id) == attribution
    assert "2605389" in attribution["url"]


def test_unknown_builtin_id_and_plain_import_do_not_invent_attribution(tmp_path):
    app = Operations(tmp_path / "book.sqlite3")
    snapshot = app.project_snapshot()
    with pytest.raises(OperationError, match="内置文本不存在"):
        app.import_builtin_text("../other-file")
    assert app.project_snapshot() == snapshot
    source = app.import_text("手工输入", "自己的原文")
    assert app.source_attribution(source) == {}


def test_real_text_picker_loads_selected_source_without_model_call(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "builtin")
    monkeypatch.setattr(
        "narrative.operations.credential_status", lambda: {"available": False, "source": "none"}
    )
    monkeypatch.setattr(
        "narrative.jobs.start", lambda *a, **kw: pytest.fail("载入内置原文不应调用模型")
    )
    ui_path = Path(__file__).resolve().parents[1] / "src/narrative/ui.py"
    ui = AppTest.from_file(str(ui_path), default_timeout=20).run()
    assert not ui.exception
    next(box for box in ui.selectbox if box.label == "选择内置文章").set_value("kongyiji").run()
    next(button for button in ui.button if button.label == "载入所选原文").click().run()
    assert not ui.exception
    app = Operations(tmp_path / "builtin.sqlite3")
    assert len(app.sources()) == 1 and "孔乙己" in app.sources()[0]["title"]
    selected_source = next(box for box in ui.selectbox if box.label == "章节").value
    assert selected_source == app.sources()[0]["id"]
    assert any("鲁迅" in str(element.value) for element in ui.caption)
    assert any("原文出处" in str(element.value) for element in ui.markdown)
    next(button for button in ui.button if button.label == "载入所选原文").click().run()
    assert not ui.exception
    assert len(app.sources()) == 1 and not app.candidates() and not app.runs()
