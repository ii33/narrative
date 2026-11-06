"""Independent Operations review of generated sketch binding and failure boundaries."""
from pathlib import Path

import pytest
from PIL import Image
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board


def test_generated_picture_binds_provider_output_not_private_guide(project, monkeypatch):
    from narrative import storyboard_style_provider
    ops, unit, block = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "指尖接触钥匙", "source_block_ids": [block]}])
    seen = {}
    def fake(guide, prompt, output, *, reference_images):
        seen.update(guide=Path(guide).read_bytes(), prompt=prompt, refs=reference_images)
        Image.new("RGB", (960, 540), "red").save(output)
        return {"provider": "isolated-offline", "job_id": "offline-image-job"}
    monkeypatch.setattr(storyboard_style_provider, "render_sketch_image", fake)
    result = ops.generate_storyboard_sketch(unit, board["shots"][0]["id"], 0, {}, board["revision"], prompt="明确手与道具接触", simple_color=True)
    frame = result["shots"][0]["keyframes"][0]
    actual = Path(frame["sketch_path"])
    assert actual.read_bytes() != seen["guide"]
    with Image.open(actual) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    assert "甲等待。" in seen["prompt"] and "明确手与道具接触" in seen["prompt"]
    assert "简洁色彩" in seen["prompt"] and not result["approved"]


def test_failed_service_cannot_bind_geometric_guide(project, monkeypatch):
    from narrative import storyboard_style_provider
    ops, unit, block = setup_board(project)
    before = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "等待", "source_block_ids": [block]}])
    def fail(*args, **kwargs):
        raise ValueError("isolated image service failure")
    monkeypatch.setattr(storyboard_style_provider, "render_sketch_image", fail)
    with pytest.raises(ValueError, match="isolated image service failure"):
        ops.generate_storyboard_sketch(unit, before["shots"][0]["id"], 0, {}, before["revision"])
    assert ops.read_storyboard(unit) == before
