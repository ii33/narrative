"""One bounded real assistant pass from written scene to playable sketch keyframe."""

from pathlib import Path

import accept_screenplay as qa
from PIL import Image
from PySide6.QtGui import QImageReader

BASE_SEED = qa.seed
BODY = "周川在旧车站门口向林岚伸出手，把铜钥匙交给她。"
TASK = "为场次 {unit_id} 做一个单镜头的导演台草案。先读取该场正文和分镜，创建并保存一个两秒镜头，引用本场交接钥匙的正文块，再调用 generate_storyboard_sketch 按正文摆放两人生成本镜零时刻的素描帧，决定景别和光向。暂保留为草案。成功后简述。"


def seed(ops):
    f = BASE_SEED(ops)
    ctx = ops.read_scene_table_context(f["segment_id"])
    table = ops.submit_scene_table(
        f["segment_id"],
        [{"plan": {"title": "站前交接", "location_text": "旧车站门口", "summary": BODY}}],
        ctx["expected_outline_revision"],
        ctx["span_fingerprint"],
        ctx["context_fingerprint"],
        reason="单镜导演台隔离场表",
    )
    unit = table["units"][0]["id"]
    ctx = ops.read_script_unit_context(unit)
    result = ops.submit_script_unit(
        unit,
        [{"type": "action", "payload": {"text": BODY}}],
        ctx["expected_plan_revision"],
        ctx["expected_body_revision"],
        ctx["span_fingerprint"],
        ctx["context_fingerprint"],
        ctx["plan_fingerprint"],
        reason="单镜验收正文",
    )
    return f | {"unit_id": unit, "block": result["blocks"][0]["id"]}


def validate(ops, fixture, screenplay, calls, report):
    board = ops.read_storyboard(fixture["unit_id"])
    report["storyboard"] = board
    shot = next(iter(board["shots"]), {})
    frame = next(iter(shot.get("keyframes", [])), {})
    image = Path(frame.get("sketch_path", ""))
    picture_ok = False
    if image.is_file():
        with Image.open(image) as picture:
            picture_ok = (
                picture.size == (960, 540) and picture.getextrema()[0] < picture.getextrema()[1]
            )
    return {
        "one_shot": len(board["shots"]) == 1,
        "source_and_two_seconds": shot.get("source_block_ids") == [fixture["block"]]
        and shot.get("duration_ms") == 2000,
        "qt_decodes_start_frame": frame.get("offset_ms") == 0
        and not QImageReader(str(image)).read().isNull(),
        "has_visible_sketch": picture_ok,
        "draft_preserved": not board["approved"],
    }


if __name__ == "__main__":
    qa.seed = seed
    qa.PROMPT_VERSION = "storyboard-sketch/1"
    qa.FIXTURE_ORIGIN = (
        "New isolated original station-key handoff, with one prewritten screenplay block."
    )
    qa.TASK = TASK
    raise SystemExit(
        qa.main(
            validate=validate,
            use_writing_target=False,
            required_tools=[
                "read_script_unit_context",
                "read_storyboard",
                "apply_storyboard_changes",
                "generate_storyboard_sketch",
            ],
            prompt_factory=lambda f: TASK.format(**f),
        )
    )
