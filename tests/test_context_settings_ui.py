"""Both web settings surfaces round-trip isolated provider context policies."""

from streamlit.testing.v1 import AppTest

from narrative.operations import Operations


def _number(app, label):
    return next(widget for widget in app.number_input if widget.label == label)


def _button(app, label):
    return next(widget for widget in app.button if widget.label == label)


def _exercise_context_policy(app, ops, provider_label, model_label, save_label):
    app.run()
    assert not app.exception
    assert _number(app, "上下文窗口（token）").value == 1_000_000
    assert _number(app, "输出预留（token）").value == 131_072
    assert _number(app, "压缩预留（token）").value == 65_536
    assert _number(app, "压缩最大输出（token）").value == 32_768
    _number(app, "上下文窗口（token）").set_value(900_000)
    _number(app, "输出预留（token）").set_value(0)
    _number(app, "压缩预留（token）").set_value(50_000)
    _number(app, "压缩最大输出（token）").set_value(90_000)
    output = next(widget for widget in app.number_input if widget.label in {
        "每块最大输出 Token", "单次响应输出上限",
    })
    assert output.value == 131_072
    output.set_value(200_000).run()
    assert any("650,000" in item.value for item in app.caption)
    _button(app, save_label).click().run()
    assert not app.exception
    saved = ops.harness_settings()
    assert {key: saved[key] for key in (
        "context_window", "output_reserve", "compaction_reserve", "compaction_max_tokens", "max_tokens",
    )} == {
        "context_window": 900_000, "output_reserve": 0, "compaction_reserve": 50_000,
        "compaction_max_tokens": 90_000, "max_tokens": 200_000,
    }

    next(widget for widget in app.selectbox if widget.label == provider_label).set_value("qwen").run()
    assert _number(app, "上下文窗口（token）").value == 1_000_000
    assert _number(app, "输出预留（token）").value == 131_072
    assert ops.harness_settings()["provider"] == "ark"  # Reading a profile does not activate it.
    next(widget for widget in app.text_input if widget.label == model_label).set_value("qwen-context-test")
    _number(app, "上下文窗口（token）").set_value(800_000)
    _button(app, save_label).click().run()
    assert not app.exception
    assert ops.harness_settings("qwen")["context_window"] == 800_000
    next(widget for widget in app.selectbox if widget.label == provider_label).set_value("ark").run()
    assert _number(app, "上下文窗口（token）").value == 900_000
    assert _number(app, "输出预留（token）").value == 0
    assert _number(app, "压缩最大输出（token）").value == 90_000

    _number(app, "上下文窗口（token）").set_value(200_000).run()
    assert _button(app, save_label).disabled
    assert any("有效输出预留" in item.value for item in app.error)
    assert ops.harness_settings("ark")["context_window"] == 900_000


def test_legacy_settings_edit_save_provider_switch_and_validation(tmp_path):
    ops = Operations.open_project(tmp_path, "context-ui")
    script = tmp_path / "legacy_settings_app.py"
    script.write_text(
        "from narrative.operations import Operations\n"
        "from narrative.ui import harness_settings_panel\n"
        f"harness_settings_panel(Operations.open_project({str(tmp_path)!r}, 'context-ui'))\n",
        encoding="utf-8",
    )
    app = AppTest.from_file(str(script), default_timeout=20)
    _exercise_context_policy(app, ops, "模型供应商", "精确模型 ID", "保存服务设置")


def test_graph_settings_edit_save_provider_switch_and_validation(tmp_path):
    path = tmp_path / "graph-context-ui.sqlite3"
    ops = Operations.create_graph_project(path)
    script = tmp_path / "graph_settings_app.py"
    script.write_text(
        "from narrative.operations import Operations\n"
        "from narrative.graph_ui import _analysis_settings\n"
        f"_analysis_settings(Operations.open_graph_project({str(path)!r}))\n",
        encoding="utf-8",
    )
    app = AppTest.from_file(str(script), default_timeout=20)
    _exercise_context_policy(app, ops, "服务", "模型 ID", "保存模型配置")
