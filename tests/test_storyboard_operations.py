from pathlib import Path

import pytest
from PIL import Image
from test_screenplay_writing import body, table
from test_screenplay_writing import project as project

from narrative.operations import Operations


def setup_board(project):
    ops, _, segment_id = project
    unit = table(ops, segment_id)["units"][0]["id"]
    block = body(ops, unit)["blocks"][0]["id"]
    return ops, unit, block


def test_board_is_atomic_and_preserves_order_across_open(project):
    ops, unit, block = setup_board(project)
    change = ops.apply_storyboard_changes
    first_result = change(unit, 0, [{"op": "create_shot", "title": "等候", "source_block_ids": [block]},
                              {"op": "create_shot", "title": "回头", "duration_ms": 1250}])
    first = ops.read_storyboard(unit)
    assert first_result['submission_status'] == 'committed' and first_result['shots'] == first['shots']
    one, two = first["shots"]
    with pytest.raises(ValueError, match="正文依据"):
        change(unit, 1, [{"op": "delete_shot", "shot_id": one["id"]},
                         {"op": "update_shot", "shot_id": two["id"], "source_block_ids": ["missing"]}])
    assert ops.read_storyboard(unit) == first
    with pytest.raises(ValueError, match="刷新"):
        change(unit, 0, [{"op": "delete_shot", "shot_id": two["id"]}])
    ordered = change(unit, 1, [{"op": "move_shot", "shot_id": two["id"], "index": 0}])
    assert [s["title"] for s in ordered["shots"]] == ["回头", "等候"]
    assert ops.read_storyboard(unit) == ordered


def test_sketch_then_approved_style_and_portable_copy(project, tmp_path):
    ops, unit, block = setup_board(project)
    change = ops.apply_storyboard_changes
    shot = change(unit, 0, [{"op": "create_shot", "title": "全景", "source_block_ids": [block]}])["shots"][0]["id"]
    image = tmp_path / "草图.png"
    Image.new("RGB", (16, 9), "gray").save(image)
    frame = {"op": "set_keyframe", "shot_id": shot, "offset_ms": 0}
    with pytest.raises(ValueError, match="定案"):
        change(unit, 1, [frame | {"style_path": str(image)}])
    with pytest.raises(ValueError, match="起始草图"):
        change(unit, 1, [{"op": "set_approved", "approved": True}])
    sketch = change(unit, 1, [frame | {"sketch_path": str(image)}, frame | {"offset_ms": 1600, "sketch_path": str(image)}])
    image.unlink()
    saved = Path(sketch["shots"][0]["keyframes"][0]["sketch_path"])
    assert saved.is_file()
    before = ops.read_storyboard(unit)
    invalid = change(unit, 2, [{'op': 'update_shot', 'shot_id': shot, 'duration_ms': 1300}])
    assert invalid['status'] == 'staged' and '时长' in invalid['problems'][0]['message']
    assert ops.read_storyboard(unit) == before
    change(unit, 2, [{"op": "set_approved", "approved": True}])
    with pytest.raises(ValueError, match="解除定案"):
        change(unit, 3, [{"op": "delete_shot", "shot_id": shot}])
    final = change(unit, 3, [frame | {"style_path": str(saved)}])
    assert final["approved"] and final["shots"][0]["keyframes"][0]["style_path"]
    copy = Operations.duplicate_graph_project(tmp_path / "writing.nproj", tmp_path / "副本.nproj")
    copied_path = Path(copy.read_storyboard(unit)["shots"][0]["keyframes"][0]["sketch_path"])
    assert copied_path.is_file() and copied_path != saved
    assert copied_path.read_bytes() == saved.read_bytes()
