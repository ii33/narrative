"""One real phase-one shared-stage task, bounded externally and never retried."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_storyboard import seed as base_seed
from PIL import Image

TASK = """为本场创建最小二维导演台草案，不改正文或原文。读取本场分镜及正文。创建人物对象一位（kind person，起点x=0.2,y=0.5），设置动作时间0ms处x=0.2且interpolation=linear，1000ms处x=0.8且interpolation=hold，1500ms处x=0.5。创建两个镜头各1000ms，均引用本场正文：正面action_start_ms=0，侧面重看action_start_ms=500。fps=25。两个镜头均用已提供图片作为offset_ms=0的单张静帧。保存后调用preview_storyboard，至少核对剪辑500ms与1000ms（第二机位重看同一动作500ms）；不生成图片、不另建资产、不改其他数据。完成自然结束，程序显示账目。"""


def seed(ops):
    fixture = base_seed(ops)
    ops.save_harness_settings({"provider": "ark", "model": "ark-code-latest", "reasoning_effort": "low",
        "context_window": 1_000_000, "output_reserve": 131_072, "compaction_reserve": 65_536,
        "compaction_max_tokens": 32_768, "max_tokens": 131_072})
    image = Path(ops.project_info().path).parent / "controlled-stage-frame.png"
    Image.new("RGB", (96, 54), "lightgray").save(image)
    fixture["image_path"] = str(image.resolve())
    fixture["before_screenplay"] = ops.read_screenplay(fixture["screenplay_id"])
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    from copy import deepcopy

    from narrative.operations import Operations
    from narrative.project_package import layout
    unit = fixture["unit_id"]
    board = ops.read_storyboard(unit)
    samples = {str(t): ops.preview_storyboard(unit, t) for t in (0, 500, 999, 1000, 1750, 2000)}
    shots = board["shots"]
    report["stage_evidence"] = {"board": board, "samples": samples}
    checks = {
        "two_shared_action_views": len(shots) == 2 and [s["action_start_ms"] for s in shots] == [0, 500],
        "fps_and_durations": board["fps"] == 25 and all(s["duration_ms"] == 1000 for s in shots),
        "single_static_image_each": len(shots) == 2 and all(len(s["keyframes"]) == 1 and s["keyframes"][0]["offset_ms"] == 0 for s in shots),
        "linear_midpoint": bool(samples["500"]["objects"]) and abs(samples["500"]["objects"][0]["x"] - 0.5) < 1e-9,
        "motion_while_image_held": bool(samples["0"]["frame_id"]) and samples["0"]["frame_id"] == samples["500"]["frame_id"] and samples["0"]["objects"] != samples["500"]["objects"],
        "cut_and_repeated_action": len(shots) == 2 and samples["1000"]["shot_id"] == shots[1]["id"] and samples["1000"]["action_ms"] == 500 and samples["1000"]["objects"] == samples["500"]["objects"],
        "hold_interpolation": bool(samples["1750"]["objects"]) and samples["1750"]["objects"][0]["x"] == 0.8,
        "total_end": len(shots) == 2 and samples["2000"]["at_end"] and samples["2000"]["local_ms"] == 1000 and bool(shots[-1]["keyframes"]) and samples["2000"]["frame_id"] == shots[-1]["keyframes"][-1]["id"],
        "body_unchanged": screenplay == fixture["before_screenplay"],
    }
    if report.get("audit_directory"):
        audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
        preview_times = {step.get("args", {}).get("time_ms") for step in audit["steps"] if step.get("ok") and step.get("tool") == "preview_storyboard"}
        checks["model_checked_both_views"] = {500, 1000} <= preview_times
        checks["actual_source_refs"] = all(shot["source_block_ids"] == [fixture["block"]] for shot in shots)
        report["stage_evidence"]["model_preview_times_ms"] = sorted(preview_times)
    # Deterministic post-model verification is separate from the raw model task.
    original_objects = deepcopy(board["objects"])
    if len(shots) == 2:
        copy_path = layout(ops.project_info().path).entry.parent / "deterministic-postcheck.nproj"
        postcheck = Operations.duplicate_graph_project(layout(ops.project_info().path).entry, copy_path)
        reordered = postcheck.apply_storyboard_changes(unit, board["revision"], [{"op": "move_shot", "shot_id": shots[1]["id"], "index": 0}])
        reopened = Operations.open_graph_project(copy_path)
        checks["reorder_preserves_shared_action"] = reordered["objects"] == original_objects
        checks["reopen_preserves_preview"] = reopened.read_storyboard(unit) == reordered and reopened.preview_storyboard(unit, 0)["objects"] == samples["1000"]["objects"]
        report["stage_evidence"]["deterministic_postcheck"] = {"reordered_board": reordered, "note": "Post-model Operations check; not credited as model-created data."}
    return checks


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "storyboard-shared-stage/1"
    qa.FIXTURE_ORIGIN = "New isolated station handoff body and controlled static image. Model creates two cameras sharing one action track; postcheck reorders/reopens via Operations."
    return qa.main(validate=validate, use_writing_target=False,
        required_tools=["read_storyboard", "apply_storyboard_changes", "preview_storyboard"],
        prompt_factory=lambda f: TASK + "\nunit_id=" + f["unit_id"] + "\nsketch_path=" + f["image_path"])


if __name__ == "__main__":
    raise SystemExit(main())
