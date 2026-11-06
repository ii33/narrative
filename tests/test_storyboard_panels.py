from pathlib import Path

import pytest
from PIL import Image, ImageChops
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board


def test_panel_images_candidates_marks_and_atomic_validation(project, tmp_path):
    ops, unit, _ = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "独立镜头"}])
    shot = board["shots"][0]["id"]
    board = ops.apply_storyboard_changes(
        unit,
        1,
        [
            {"op": "create_panel", "shot_id": shot},
            {"op": "create_panel", "shot_id": shot, "offset_ms": 100},
        ],
    )
    one, two = board["shots"][0]["panels"]
    image = tmp_path / "source.png"
    Image.new("RGB", (960, 540), "blue").save(image)
    board = ops.apply_storyboard_changes(
        unit,
        2,
        [
            {
                "op": "add_panel_candidate",
                "shot_id": shot,
                "panel_id": one["id"],
                "image_path": str(image),
            }
        ],
    )
    candidate = board["shots"][0]["panels"][0]["candidates"][0]
    board = ops.apply_storyboard_changes(
        unit,
        3,
        [
            {
                "op": "adopt_panel_candidate",
                "shot_id": shot,
                "panel_id": one["id"],
                "candidate_id": candidate["id"],
            },
            {
                "op": "update_panel",
                "shot_id": shot,
                "panel_id": one["id"],
                "marks": [{"id": "m1", "kind": "arrow", "points": [[0.1, 0.1], [0.8, 0.8]]}],
            },
        ],
    )
    assert board["shots"][0]["panels"][1] == two
    clean = tmp_path / "clean.png"
    marked = tmp_path / "marked.png"
    ops.export_storyboard_panel(unit, shot, one["id"], str(clean))
    ops.export_storyboard_panel(unit, shot, one["id"], str(marked), True)
    assert Image.open(clean).getpixel((96, 54)) == (0, 0, 255)
    assert ImageChops.difference(Image.open(clean), Image.open(marked)).getbbox()
    with pytest.raises(ValueError, match="归一化"):
        ops.apply_storyboard_changes(
            unit,
            4,
            [
                {
                    "op": "update_panel",
                    "shot_id": shot,
                    "panel_id": one["id"],
                    "marks": [{"kind": "stroke", "points": [[1.1, 0]]}],
                }
            ],
        )
    assert ops.read_storyboard(unit) == board
    board = ops.apply_storyboard_changes(
        unit,
        4,
        [
            {
                "op": "update_panel",
                "shot_id": shot,
                "panel_id": two["id"],
                "base": {"kind": "panel", "panel_id": one["id"]},
            }
        ],
    )
    assert board["shots"][0]["panels"][1]["base"]["kind"] == "snapshot"
    with pytest.raises(FileExistsError):
        ops.export_storyboard_panel(unit, shot, one["id"], str(clean))
    with pytest.raises(ValueError, match="位置"):
        ops.apply_storyboard_changes(
            unit, 5, [{"op": "update_shot", "shot_id": shot, "duration_ms": 50}]
        )


def test_panel_generation_and_use_are_independent_and_keep_provenance(
    project, tmp_path, monkeypatch
):
    ops, unit, _ = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "交接"}])
    shot = board["shots"][0]["id"]
    board = ops.apply_storyboard_changes(
        unit,
        1,
        [
            {"op": "create_panel", "shot_id": shot, "intent": "握住钥匙"},
            {"op": "create_panel", "shot_id": shot, "offset_ms": 100},
        ],
    )
    first, second = board["shots"][0]["panels"]
    calls = []

    def generate(guide, prompt, output, reference_images=()):
        calls.append(prompt)
        Image.new("RGB", (960, 540), "green").save(output)
        return {"provider": "test", "job_id": "fixture", "usage": None}

    monkeypatch.setattr("narrative.storyboard_style_provider.render_sketch_image", generate)
    board = ops.generate_storyboard_panel(unit, shot, first["id"], 2)
    assert board["shots"][0]["panels"][1] == second
    assert board["shots"][0]["keyframes"] == []
    candidate = board["shots"][0]["panels"][0]["candidates"][0]
    board = ops.apply_storyboard_changes(
        unit,
        3,
        [
            {
                "op": "adopt_panel_candidate",
                "shot_id": shot,
                "panel_id": first["id"],
                "candidate_id": candidate["id"],
            },
            {"op": "use_panel_frame", "shot_id": shot, "panel_id": first["id"]},
        ],
    )
    panel = board["shots"][0]["panels"][0]
    assert panel["frame_id"] == board["shots"][0]["keyframes"][0]["id"]
    assert (
        Path(board["shots"][0]["keyframes"][0]["sketch_path"]).read_bytes()
        == Path(candidate["path"]).read_bytes()
    )
    board = ops.generate_storyboard_panels(unit, shot, 4)
    assert len(calls) == 2
    assert len(board["shots"][0]["panels"][0]["candidates"]) == 1
    assert len(board["shots"][0]["panels"][1]["candidates"]) == 1

    # A stale response may never overwrite the current plan.
    def stale(guide, prompt, output, reference_images=()):
        Image.new("RGB", (960, 540), "red").save(output)
        ops.apply_storyboard_changes(
            unit,
            5,
            [{"op": "update_panel", "shot_id": shot, "panel_id": second["id"], "intent": "改动"}],
        )
        return {"provider": "test", "job_id": "stale"}

    monkeypatch.setattr("narrative.storyboard_style_provider.render_sketch_image", stale)
    with pytest.raises(ValueError, match="刷新"):
        ops.generate_storyboard_panel(unit, shot, first["id"], 5)
    assert len(ops.read_storyboard(unit)["shots"][0]["panels"][0]["candidates"]) == 1


def test_panel_review_tracks_only_visual_dependencies(project, monkeypatch):
    ops, unit, _ = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "等候"}])
    shot = board["shots"][0]["id"]
    board = ops.apply_storyboard_changes(
        unit,
        1,
        [
            {"op": "create_panel", "shot_id": shot},
            {"op": "create_panel", "shot_id": shot, "offset_ms": 100},
        ],
    )

    def generate(guide, prompt, output, reference_images=()):
        Image.new("RGB", (960, 540), "green").save(output)
        return {"provider": "test", "job_id": "fixture"}

    monkeypatch.setattr("narrative.storyboard_style_provider.render_sketch_image", generate)
    board = ops.generate_storyboard_panels(unit, shot, 2)
    one, two = board["shots"][0]["panels"]
    assert one["candidates"][0]["generation_basis"]

    def changed():
        return [
            row
            for row in ops.check_storyboard(unit)["diagnostics"]
            if row["code"] == "panel_basis_changed"
        ]

    assert changed() == []
    board = ops.apply_storyboard_changes(
        unit,
        4,
        [
            {
                "op": "update_panel",
                "shot_id": shot,
                "panel_id": one["id"],
                "offset_ms": 300,
                "marks": [{"kind": "text", "text": "旁注", "points": [[0.5, 0.5]]}],
            }
        ],
    )
    assert changed() == []
    board = ops.apply_storyboard_changes(
        unit,
        5,
        [{"op": "update_panel", "shot_id": shot, "panel_id": one["id"], "intent": "新的画面意图"}],
    )
    assert [row["panel_id"] for row in changed()] == [one["id"]]
    board = ops.apply_storyboard_changes(
        unit,
        6,
        [
            {"op": "update_panel", "shot_id": shot, "panel_id": one["id"], "intent": ""},
            {
                "op": "update_panel",
                "shot_id": shot,
                "panel_id": two["id"],
                "base": {"kind": "camera"},
            },
        ],
    )
    assert [row["panel_id"] for row in changed()] == [two["id"]]
    ops.apply_storyboard_changes(
        unit, 7, [{"op": "update_shot", "shot_id": shot, "description": "主体转身"}]
    )
    assert {row["panel_id"] for row in changed()} == {one["id"], two["id"]}
