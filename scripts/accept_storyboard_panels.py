"""One real unified task for script, visual usage, sound intention and a panel image."""

from pathlib import Path

import accept_screenplay as qa
from accept_storyboard import seed
from PIL import Image

TASK = """为场次 {unit_id} 完成最小单镜分镜草案。仅一个统一任务、不重试图像服务。先读正文和分镜；创建2秒镜头，引用正文块，description写铜钥匙交接的可通读画面推进，shot_language写中景固定，visual_elements记录周川、林岚、铜钥匙与车站的本镜用法，不虚构资产id。创建一个共享effect声音意图：钥匙轻响，随后将意图id引用到镜头sound_intent_ids。建立两个独立画格，零时刻为伸手、1000毫秒为交接，写intent。仅对零时刻画格调用一次generate_storyboard_panel生成真实黑白素描（提示两人手与铜钥匙接触、车站外景、清楚遮挡），然后采用返回候选。另一格保留空白，不调用其他图像生成工具。调用export_storyboard_panel导出干净PNG到 {output}。最后简述已写入与未出图范围，保留草案。"""


def validate(ops, fixture, screenplay, calls, report):
    board = ops.read_storyboard(fixture["unit_id"])
    report["storyboard"] = board
    shots = board["shots"]
    shot = shots[0] if shots else {}
    panels = shot.get("panels", [])
    first = next((p for p in panels if p["offset_ms"] == 0), {})
    second = next((p for p in panels if p["offset_ms"] == 1000), {})
    candidate = next(
        (c for c in first.get("candidates", []) if c["id"] == first.get("adopted_candidate_id")), {}
    )
    path = Path(candidate.get("path", ""))
    image_ok = False
    if path.is_file():
        with Image.open(path) as image:
            image.load()
            image_ok = image.width > 0 and image.height > 0
    return {
        "one_shot": len(shots) == 1,
        "narrative_description": bool(shot.get("description")),
        "visual_elements": len(shot.get("visual_elements", [])) >= 3,
        "sound_intention": bool(board.get("sound_intents")) and bool(shot.get("sound_intent_ids")),
        "two_independent_panels": len(panels) == 2
        and bool(second)
        and not second.get("candidates"),
        "adopted_real_image": image_ok,
        "one_image_candidate": sum(len(p.get("candidates", [])) for p in panels) == 1,
        "clean_export": Path(report["panel_export"]).is_file(),
        "draft": not board["approved"],
    }


if __name__ == "__main__":
    qa.seed = seed
    qa.PROMPT_VERSION = "storyboard-panel/1"
    qa.FIXTURE_ORIGIN = "New isolated station key handoff, one prewritten block."
    qa.TASK = TASK

    def started(ops, fixture, report, directory, began):
        report["panel_export"] = str(directory / "clean-panel.png")

    raise SystemExit(
        qa.main(
            validate=validate,
            use_writing_target=False,
            required_tools=[
                "read_script_unit_context",
                "read_storyboard",
                "apply_storyboard_changes",
                "generate_storyboard_panel",
                "export_storyboard_panel",
            ],
            prompt_factory=lambda f: TASK.format(
                **f,
                output=str(
                    Path(
                        __import__("sys").argv[__import__("sys").argv.index("--output") + 1]
                    ).resolve()
                    / "clean-panel.png"
                ),
            ),
            on_started=started,
        )
    )
