"""One minimal real append operation over a screenplay with existing body content."""
from __future__ import annotations

import json

import accept_screenplay as qa

PROMPT = """本轮仅验收追加导入，请实际完成，不生成或改写正文。固定目标见下方JSON。先read_screenplay_timeline读取当前副本revision、adaptation_snapshot读取当前源revision；然后只调用append_screenplay_timeline把new_moment_id追加到固定screenplay_id（moment_ids仅此ID），传刚读到的两个修订与reason。再read_screenplay_timeline确认新点在末尾、旧点仍存在，简短报告。不得调用旧import重新同步，不编辑旧节点、段落、场表、正文或源改编，不开启其他任务。"""
_original_seed = qa.seed


def seed(ops):
    fixture = _original_seed(ops)
    context = ops.read_scene_table_context(fixture["segment_id"])
    table = ops.submit_scene_table(fixture["segment_id"], [{"plan": {"title": "已有场次", "summary": "林岚到仓门前停步。"}}],
        context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"],
        dependency_refs=context["dependency_refs"], reason="已有场表fixture")
    unit_id = table["units"][0]["id"]
    context = ops.read_script_unit_context(unit_id)
    ops.submit_script_unit(unit_id, [{"type": "action", "payload": {"text": "已有正文必须保留。"}}],
        context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"],
        context["context_fingerprint"], context["plan_fingerprint"], dependency_refs=context["dependency_refs"], reason="已有正文fixture，不计模型输出")
    source = ops.adaptation_snapshot()
    parent = next(n["id"] for n in source["nodes"] if n["node_type"] == "scene")
    added = ops.edit_adaptation_node(changes={"node_type": "event", "name": "追加验收节点"}, parent_id=parent,
        after_id=max(source["nodes"], key=lambda n: n["sort_id"])["id"], reason="新增源改编点fixture")
    new_id = next(n["id"] for n in added["nodes"] if n["name"] == "追加验收节点")
    fixture.update(unit_id=unit_id, new_moment_id=new_id,
                   before_document=ops.read_screenplay(fixture["screenplay_id"]),
                   before_timeline=ops.read_screenplay_timeline(fixture["screenplay_id"]))
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    timeline = ops.read_screenplay_timeline(fixture["screenplay_id"])
    previous = fixture["before_timeline"]
    old_ids = {n["id"] for n in previous["nodes"]}
    new = next((n for n in timeline["nodes"] if n["id"] == fixture["new_moment_id"]), None)
    old_order = [n["id"] for n in sorted(previous["nodes"], key=lambda n: n["sort_id"])]
    order_now = [n["id"] for n in sorted(timeline["nodes"], key=lambda n: n["sort_id"]) if n["id"] in old_ids]
    return {"task_completed": report["task_status"] == "completed", "old_nodes_retained_in_order": old_order == order_now,
            "new_node_appended": new is not None and new["sort_id"] > max(n["sort_id"] for n in previous["nodes"]),
            "existing_document_unchanged": all(screenplay[key] == fixture["before_document"][key] for key in ["screenplay", "episodes", "acts", "structure_entries", "structure_tree", "segment_order", "segments"]),
            "existing_properties_unchanged": timeline["properties"] == previous["properties"],
            "screenplay_static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"]}


if __name__ == "__main__":
    qa.TASK = PROMPT
    qa.PROMPT_VERSION = "screenplay-append-acceptance/1"
    qa.FIXTURE_ORIGIN = "All existing content is deterministic fixture; only the append tool operation is model-driven"
    qa.seed = seed
    raise SystemExit(qa.main(validate=validate, required_tools=["append_screenplay_timeline", "read_screenplay_timeline"],
        use_writing_target=False, prompt_factory=lambda fixture: PROMPT + "\n固定目标：" + json.dumps({key: fixture[key] for key in ["screenplay_id", "new_moment_id"]}, ensure_ascii=False)))
