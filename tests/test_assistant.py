"""常驻协作者的循环：模型提议调工具，程序执行，结果喂回去。"""

from __future__ import annotations

import json
from pathlib import Path

from narrative import assistant, tool_api
from narrative.project_catalog import create_graph_project


def _project(path: Path) -> Path:
    ops = create_graph_project(path)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "林舟出场",
                 "participants": ["1"]},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    return path


def _script(*replies: dict):
    """按顺序回放模型的话；回放完就重复最后一条。"""
    queue = [json.dumps(reply, ensure_ascii=False) for reply in replies]

    def ask(_instruction: str) -> str:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return ask


def test_the_catalog_is_read_only_and_comes_from_the_tool_table() -> None:
    names = [tool["name"] for tool in assistant.tool_catalog()]
    assert "list_nodes" in names
    # 写操作一个都不在里面：协作者不能改图。
    assert all(not tool_api.TOOLS[name].writes for name in names)


def test_a_turn_can_call_a_tool_and_then_answer(tmp_path: Path) -> None:
    project = _project(tmp_path / "chat.sqlite3")
    result = assistant.run_turn(
        _script(
            {"tool": "list_nodes", "args": {}},
            {"answer": "图里只有林舟和一次出场。"},
        ),
        "图里有什么？",
        project=project,
    )
    assert result["answer"] == "图里只有林舟和一次出场。"
    assert [step["tool"] for step in result["steps"]] == ["list_nodes"]
    assert result["steps"][0]["ok"] is True
    # 界面只要一行摘要，完整结果留给模型。
    assert "节点" in result["steps"][0]["summary"]
    assert "result" not in result["steps"][0]


def test_a_write_tool_is_refused_not_executed(tmp_path: Path) -> None:
    project = _project(tmp_path / "write.sqlite3")
    before = tool_api.call("statistics", {}, project=project)["properties"]["value_records"]
    result = assistant.run_turn(
        _script(
            {"tool": "add_property", "args": {"node_id": "1", "property_id": "age",
                                              "value": "30", "event_id": "2"}},
            {"answer": "我改不了。"},
        ),
        "帮我把年龄改成三十",
        project=project,
    )
    assert result["steps"][0]["ok"] is False
    assert "只能读" in result["steps"][0]["summary"]
    # 图没动。
    after = tool_api.call("statistics", {}, project=project)["properties"]["value_records"]
    assert after == before


def test_a_broken_tool_call_comes_back_as_an_error(tmp_path: Path) -> None:
    project = _project(tmp_path / "broken.sqlite3")
    result = assistant.run_turn(
        _script(
            {"tool": "get_node", "args": {"node_id": "P99"}},
            {"answer": "没有这个节点。"},
        ),
        "P99 是谁",
        project=project,
    )
    assert result["steps"][0]["ok"] is False
    assert "节点不存在" in result["steps"][0]["summary"]


def test_a_plain_reply_is_taken_as_the_answer(tmp_path: Path) -> None:
    """模型不按 JSON 回（或者直接说话）时，不能把它当成工具调用。"""
    project = _project(tmp_path / "plain.sqlite3")
    result = assistant.run_turn(
        lambda _instruction: "我不知道，图里还没有这条。",
        "有这条吗",
        project=project,
    )
    assert result["answer"] == "我不知道，图里还没有这条。"
    assert result["steps"] == []


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f00050001ff9d5f6d0d0000000049454e44ae426082"
)


def test_an_attached_image_goes_in_as_a_content_block(tmp_path: Path) -> None:
    """有图就发内容块：一段文字 + 一张图，而不是把路径写进正文。"""
    project = _project(tmp_path / "image.sqlite3")
    image = tmp_path / "ref.png"
    image.write_bytes(PNG)
    seen: dict = {}

    def ask(instruction):
        seen["instruction"] = instruction
        return json.dumps({"answer": "看到了"})

    assistant.run_turn(ask, "这张图里是什么", project=project, image=str(image))
    blocks = seen["instruction"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "text"
    assert "ref.png" in blocks[0]["text"]
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_an_unreadable_attachment_falls_back_to_text(tmp_path: Path) -> None:
    project = _project(tmp_path / "noimage.sqlite3")
    note = tmp_path / "note.txt"
    note.write_text("不是图片", encoding="utf-8")
    seen: dict = {}

    def ask(instruction):
        seen["instruction"] = instruction
        return json.dumps({"answer": "没看到图"})

    assistant.run_turn(ask, "看看这个", project=project, image=str(note))
    assert isinstance(seen["instruction"], str)


def test_the_loop_stops_at_the_step_limit(tmp_path: Path) -> None:
    project = _project(tmp_path / "loop.sqlite3")
    result = assistant.run_turn(
        lambda _instruction: json.dumps({"tool": "list_nodes", "args": {}}),
        "一直查",
        project=project,
        max_steps=3,
    )
    assert len(result["steps"]) == 3
    assert "停在这里" in result["answer"]
