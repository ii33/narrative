import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from test_screenplay_writing import body, table
from test_screenplay_writing import project as project

from narrative.storyboard_art import render_sketch


def test_light_changes_image_without_moving_blocking():
    spec = {"composition": "empty", "setting": "interior"}
    left = render_sketch(spec | {"light": "left"})
    right = render_sketch(spec | {"light": "right"})
    assert left.getpixel((32, 36)) > right.getpixel((32, 36))
    assert left.getpixel((928, 36)) < right.getpixel((928, 36))
    actor = {"kind": "person", "x": 0.3, "y": 0.8, "pose": "reach"}
    placed = render_sketch(spec | {"subjects": [actor]})
    box = ImageChops.difference(left, placed).getbbox()
    assert box and box[2] < 480


def test_style_package_keeps_original_sketch_and_effective_style(project, tmp_path, monkeypatch):
    def generate(guide, prompt, copies_to, reference_images):
        Image.new('RGB', (960, 540), 'gray').save(copies_to)
        return {'provider': 'offline-test', 'job_id': 'test-job', 'usage': None}
    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', generate, raising=False)
    ops, _, segment = project
    unit = table(ops, segment)["units"][0]["id"]
    block = body(ops, unit)["blocks"][0]
    ops.save_project_style("低饱和木刻版画", 0)
    added = ops.apply_storyboard_changes(
        unit, 0, [{"op": "create_shot", "title": "窗边等待", "source_block_ids": [block["id"]]}]
    )
    shot = added["shots"][0]["id"]
    board = ops.generate_storyboard_sketch(
        unit,
        shot,
        0,
        {"setting": "interior", "framing": "close", "light": "back"},
        added["revision"],
    )
    path = Path(board["shots"][0]["keyframes"][0]["sketch_path"])
    with Image.open(path) as sketch:
        assert sketch.mode == "RGB" and sketch.size == (960, 540)
    output = tmp_path / "外部出图.zip"
    with pytest.raises(ValueError, match="定案"):
        ops.export_storyboard_style_package(unit, shot, str(output))
    ops.apply_storyboard_changes(
        unit, board["revision"], [{"op": "set_approved", "approved": True}]
    )
    ops.export_storyboard_style_package(unit, shot, str(output))
    with zipfile.ZipFile(output) as package:
        manifest = json.loads(package.read("manifest.json"))
        prompt = package.read("prompt.txt").decode()
        assert "低饱和木刻版画" in prompt and "甲等待。" in prompt and "窗边等待" in prompt
        assert package.read(manifest["keyframes"][0]["sketch"]) == path.read_bytes()
    with pytest.raises(FileExistsError):
        ops.export_storyboard_style_package(unit, shot, str(output))
