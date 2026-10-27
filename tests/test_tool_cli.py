"""The CLI door: named commands and the generic tool pair, both on tool_api."""

from __future__ import annotations

from pathlib import Path

from narrative import tool_cli
from narrative.project_catalog import create_graph_project


def _project(path: Path) -> Path:
    ops = create_graph_project(path)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "交代年龄",
                 "participants": ["1"]},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    return path


def test_tool_list_prints_every_tool(capsys) -> None:
    assert tool_cli.main(["tool", "list"]) == 0
    printed = capsys.readouterr().out
    assert "list_nodes" in printed
    assert "add_property" in printed


def test_generic_call_with_json(capsys, tmp_path: Path) -> None:
    project = _project(tmp_path / "cli.sqlite3")
    code = tool_cli.main([
        "tool", "call", "get_node", '{"node_id": "1"}', "--project", str(project),
    ])
    assert code == 0
    assert "林舟" in capsys.readouterr().out


def test_named_alias_reads_a_node(capsys, tmp_path: Path) -> None:
    project = _project(tmp_path / "cli-node.sqlite3")
    assert tool_cli.main(["node", "1", "--project", str(project)]) == 0
    printed = capsys.readouterr().out
    assert "林舟" in printed and "person" in printed


def test_named_alias_writes_a_value(capsys, tmp_path: Path) -> None:
    project = _project(tmp_path / "cli-write.sqlite3")
    assert tool_cli.main([
        "add-property", "1", "age", "二十九岁", "2", "--project", str(project),
    ]) == 0
    assert "accepted" in capsys.readouterr().out

    assert tool_cli.main(["props", "1", "--project", str(project)]) == 0
    assert "二十九岁" in capsys.readouterr().out


def test_a_bad_tool_exits_with_a_message(capsys, tmp_path: Path) -> None:
    project = _project(tmp_path / "cli-bad.sqlite3")
    assert tool_cli.main(["tool", "call", "nope", "{}", "--project", str(project)]) == 2
    assert "nope" in capsys.readouterr().err
