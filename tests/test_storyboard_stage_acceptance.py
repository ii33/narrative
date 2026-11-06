"""Independent phase-one director-time contract checks through Operations."""
from copy import deepcopy

from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board

from narrative.operations import Operations
from narrative.project_package import layout


def test_shared_action_time_static_frame_cut_reorder_and_reopen(project, tmp_path):
    from PIL import Image
    ops, unit, block = setup_board(project)
    image = tmp_path / "hold.png"
    Image.new("RGB", (16, 9), "gray").save(image)
    board = ops.apply_storyboard_changes(unit, 0, [
        {"op": "set_fps", "fps": 25},
        {"op": "create_object", "name": "人物", "kind": "person", "x": 0.2, "y": 0.5},
        {"op": "create_shot", "title": "正面", "duration_ms": 1000, "action_start_ms": 0, "source_block_ids": [block]},
        {"op": "create_shot", "title": "侧面重看", "duration_ms": 1000, "action_start_ms": 500, "source_block_ids": [block]},
    ])
    actor = board["objects"][0]["id"]
    changes = [{"op": "set_keyframe", "shot_id": shot["id"], "offset_ms": 0, "sketch_path": str(image)} for shot in board["shots"]]
    changes += [{"op": "set_action_keyframe", "object_id": actor, "time_ms": 0, "x": 0.2, "interpolation": "linear"},
                {"op": "set_action_keyframe", "object_id": actor, "time_ms": 1000, "x": 0.8, "interpolation": "hold"},
                {"op": "set_action_keyframe", "object_id": actor, "time_ms": 1500, "x": 0.5}]
    board = ops.apply_storyboard_changes(unit, board["revision"], changes)
    early, later = [ops.preview_storyboard(unit, t) for t in (0, 500)]
    assert abs(later["objects"][0]["x"] - 0.5) < 1e-9
    assert early["frame_id"] == later["frame_id"] and early["objects"][0]["x"] != later["objects"][0]["x"]
    cut = ops.preview_storyboard(unit, 1000)
    assert cut["shot_id"] == board["shots"][1]["id"] and cut["local_ms"] == 0 and cut["action_ms"] == 500
    assert cut["objects"] == later["objects"]
    held = ops.preview_storyboard(unit, 1750)
    assert held["objects"][0]["x"] == 0.8
    end = ops.preview_storyboard(unit, 2000)
    assert end["at_end"] and end["local_ms"] == 1000
    original_track = deepcopy(board["objects"])
    reordered = ops.apply_storyboard_changes(unit, board["revision"], [{"op": "move_shot", "shot_id": board["shots"][1]["id"], "index": 0}])
    assert reordered["objects"] == original_track
    reopened = Operations.open_graph_project(layout(ops.project_info().path).entry)
    assert reopened.read_storyboard(unit) == reordered
    assert reopened.preview_storyboard(unit, 0)["objects"] == cut["objects"]


def test_director_preview_shared_tool_registry_cli_and_mcp(project, capsys):
    import json

    from narrative import mcp_server, tool_api, tool_cli
    ops, unit, _ = setup_board(project)
    params = {"unit_id": unit, "time_ms": 0}
    expected = ops.preview_storyboard(**params)
    assert tool_api.call("preview_storyboard", params, project=layout(ops.project_info().path).entry) == expected
    assert tool_cli.main(["tool", "call", "preview_storyboard", json.dumps(params), "--project", str(layout(ops.project_info().path).entry)]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    response = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "preview_storyboard", "arguments": params}}, default_project=str(layout(ops.project_info().path).entry))
    assert not response["result"].get("isError")
    assert json.loads(response["result"]["content"][0]["text"]) == expected


def test_static_frame_drag_uses_identity_and_conflicts_are_atomic(project, tmp_path):
    import pytest
    from PIL import Image
    ops, unit, block = setup_board(project)
    image = tmp_path / "frame.png"
    Image.new("RGB", (16, 9), "gray").save(image)
    board = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "帧拖动", "duration_ms": 1000, "source_block_ids": [block]}])
    shot = board["shots"][0]["id"]
    board = ops.apply_storyboard_changes(unit, board["revision"], [{"op": "set_keyframe", "shot_id": shot, "offset_ms": t, "sketch_path": str(image)} for t in (0, 500)])
    frame = board["shots"][0]["keyframes"][1]["id"]
    moved = ops.apply_storyboard_changes(unit, board["revision"], [{"op": "move_keyframe", "shot_id": shot, "frame_id": frame, "offset_ms": 750}])
    assert ops.preview_storyboard(unit, 749)["frame_id"] != frame
    assert ops.preview_storyboard(unit, 750)["frame_id"] == frame
    assert moved["shots"][0]["keyframes"][1]["id"] == frame
    with pytest.raises(ValueError):
        ops.apply_storyboard_changes(unit, moved["revision"], [{"op": "move_keyframe", "shot_id": shot, "frame_id": frame, "offset_ms": 1000}])
    assert ops.read_storyboard(unit) == moved
