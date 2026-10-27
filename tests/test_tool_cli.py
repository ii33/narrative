"""The CLI door: named commands and the generic tool pair, both on tool_api."""

from __future__ import annotations

import json
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


def test_every_alias_points_at_a_real_tool() -> None:
    """别名写错工具名，只有真去敲的时候才会发现——这里先发现。"""
    from narrative import tool_api

    unknown = {
        name: tool_name
        for name, (tool_name, _spec) in tool_cli.ALIASES.items()
        if tool_name not in tool_api.TOOLS
    }
    assert unknown == {}


def test_the_material_aliases_reach_the_same_answers(capsys, tmp_path: Path) -> None:
    project = _project(tmp_path / "cli-material.sqlite3")
    assert tool_cli.main([
        "add-material", "1", "青衫少年", "--detail", "洗得发白的青衫",
        "--project", str(project), "--json",
    ]) == 0
    created = json.loads(capsys.readouterr().out)
    card_id = created["card"]["id"]

    assert tool_cli.main(["material", "1", "--project", str(project), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [card["title"] for card in listed["cards"]] == ["青衫少年"]

    assert tool_cli.main([
        "delete-material", "1", card_id, "--project", str(project), "--json",
    ]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["removed"] == card_id


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
