"""One bounded real task edits and physically deletes a local timeline subtree."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

TEXT = "甲打开门。甲在门外稍候。"
TASK = """这是剧本本地时间点增删改移动复制的最小验收。只修改给定目标剧本时间线，不改源提取/改编或对照剧本。按顺序实际调用：
1. read_screenplay_timeline读取目标当前修订。
2. edit_screenplay_timeline_node新增event“本地新点”，parent_id为给定“开门场”scene，after_id为“开门”event。
3. edit_screenplay_timeline_node将新点改名“本地改名点”。
4. move_screenplay_timeline_node把该新点移到给定“候场”scene，排在“稍候”event之后。
5. copy_screenplay_timeline_node复制整个“候场”scene及其子树，不传after_id，使用默认位置放在原整棵子树之后，保留其事件状态并使用程序分配的新ID。
6. delete_screenplay_timeline_node物理删除原“候场”scene及原子树。不要删除新复制的子树，不用屏蔽代替删除。保留复制子树中的两事件与“待删状态”。
每步用刚返回的revision作为expected_timeline_revision参数，并用真实新ID，不重复大读、不创建其他内容、不改状态值。简述新建/复制/删除IDs。仅以上五次写操作，不另开任务或重试服务。"""


def seed(ops):
    source_id = ops.import_source("本地时间点操作样本", TEXT)
    result = ops.submit_extraction(source_id, {"nodes": [
        {"type": "person", "name": "甲", "quotes": ["甲"]},
        {"type": "scene", "name": "开门场", "quotes": ["甲打开门。"]},
        {"type": "scene", "name": "候场", "quotes": ["甲在门外稍候。"]},
        {"type": "event", "name": "开门", "scene": "开门场", "participants": ["甲"], "quotes": ["甲打开门。"]},
        {"type": "event", "name": "稍候", "scene": "候场", "participants": ["甲"], "quotes": ["甲在门外稍候。"]}],
        "properties": [{"node": "稍候", "property": "情势", "value": "待删状态", "value_type": "string", "at_event": "稍候", "quotes": ["甲在门外稍候。"]}]})
    assert result["status"] == "accepted", result
    ids = {row["name"]: row["id"] for row in ops.graph_snapshot()["nodes"]}
    adaptation = ops.import_adaptation([ids["开门场"], ids["候场"]], reason="隔离验收源改编")
    documents = []
    for name in ("目标剧本", "对照剧本"):
        document = ops.create_screenplay(name, reason="隔离验收独立剧本")["screenplay"]
        ops.import_screenplay_timeline(document["id"], [ids["开门场"], ids["候场"]], 0, adaptation["revision"], "隔离验收导入")
        documents.append(document["id"])
    return {"source_id": source_id, "screenplay_id": documents[0], "other_screenplay_id": documents[1], "ids": ids,
        "before_target": ops.read_screenplay_timeline(documents[0]), "before_other": ops.read_screenplay_timeline(documents[1]),
        "properties_before": ops.property_records()}


def validate(ops, fixture, screenplay, calls, report):
    from narrative.tool_api import TOOLS
    snapshot = ops.read_screenplay_timeline(fixture["screenplay_id"])
    before = fixture["before_target"]
    ids = fixture["ids"]
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    attempts = [step for step in audit["steps"] if step["tool"] in TOOLS and TOOLS[step["tool"]].writes]
    writes = [step for step in attempts if step.get("ok")]
    new = [node for node in snapshot["nodes"] if node["id"] not in {row["id"] for row in before["nodes"]}]
    copied_scene = next((node for node in new if node["node_type"] == "scene" and node["name"] == "候场"), {})
    copied_wait = next((node for node in new if node["name"] == "稍候"), {})
    renamed = next((node for node in new if node["name"] == "本地改名点"), {})
    creation = next((step for step in writes if step["tool"] == "edit_screenplay_timeline_node" and not step["args"].get("node_id")), {})
    created_id = creation.get("result", {}).get("created_node_id") or next((node["id"] for node in creation.get("result", {}).get("nodes", []) if node["name"] == "本地新点"), None)
    deleted_ids = {ids["候场"], ids["稍候"], created_id}
    old_state_ids = {row["id"] for row in before["properties"] if row.get("node_id") == ids["稍候"]}
    values = [row for row in snapshot["properties"] if row.get("kind") == "property_value"]
    report["timeline_crud_evidence"] = {"target_after": snapshot, "other_after": ops.read_screenplay_timeline(fixture["other_screenplay_id"]),
        "attempted_writes": attempts, "successful_writes": writes, "deleted_ids": sorted(identifier for identifier in deleted_ids if identifier), "original_state_ids": sorted(old_state_ids)}
    move = next((step for step in writes if step["tool"] == "move_screenplay_timeline_node"), {})
    copying = next((step for step in writes if step["tool"] == "copy_screenplay_timeline_node"), {})
    copied_state = {node["id"]: node for node in copying.get("result", {}).get("nodes", [])}
    deletion = next((step.get("result", {}) for step in writes if step["tool"] == "delete_screenplay_timeline_node"), {})
    moved_state = move.get("result", {})
    moved_nodes = {node["id"]: node for node in moved_state.get("nodes", [])}
    return {"exact_five_writes": [step["tool"] for step in writes] == ["edit_screenplay_timeline_node", "edit_screenplay_timeline_node", "move_screenplay_timeline_node", "copy_screenplay_timeline_node", "delete_screenplay_timeline_node"],
        "all_writes_target_local_timeline": all(step["args"].get("screenplay_id") == fixture["screenplay_id"] for step in writes),
        "created_point_moved_to_requested_parent": bool(created_id) and created_id in moved_nodes.get(ids["候场"], {}).get("events", []) and created_id not in moved_nodes.get(ids["开门场"], {}).get("events", []) and moved_nodes[created_id]["sort_id"] > moved_nodes[ids["稍候"]]["sort_id"],
        "new_copy_retains_two_children": bool(copied_scene) and set(copied_scene.get("events", [])) == {copied_wait.get("id"), renamed.get("id")} and bool(copied_wait) and bool(renamed),
        "default_copy_follows_whole_original_subtree": bool(copied_scene) and not copying.get("args", {}).get("after_id") and all(copied_state[key]["sort_id"] < copied_state[copied_scene["id"]]["sort_id"] for key in deleted_ids if key in copied_state),
        "physical_subtree_deletion": bool(created_id) and not (deleted_ids & {node["id"] for node in snapshot["nodes"]}),
        "deletion_report_matches_removed_subtree": set(deletion.get("deleted_node_ids", [])) == deleted_ids and old_state_ids <= set(deletion.get("deleted_property_ids", [])),
        "original_state_physically_removed": not (old_state_ids & {row["id"] for row in snapshot["properties"]}),
        "copied_state_remapped": len(values) == 1 and values[0]["node_id"] == copied_wait.get("id") and values[0].get("event_id") == copied_wait.get("id") and values[0]["value"] == "待删状态" and values[0]["id"] not in old_state_ids,
        "no_deleted_containment_refs": all(not (deleted_ids & set(node.get("events", []))) for node in snapshot["nodes"]),
        "other_screenplay_unchanged": ops.read_screenplay_timeline(fixture["other_screenplay_id"]) == fixture["before_other"],
        "source_properties_unchanged": ops.property_records() == fixture["properties_before"],
        "both_screenplays_static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"] and ops.check_screenplay(fixture["other_screenplay_id"])["ok"]}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = "screenplay-local-timeline-crud/1"
    qa.FIXTURE_ORIGIN = "Operations source/adaptation imported to two independent scripts; real model creates, renames, moves, copies subtree and physically deletes original locally"
    return qa.main(validate=validate, required_tools=["read_screenplay_timeline", "edit_screenplay_timeline_node", "move_screenplay_timeline_node", "copy_screenplay_timeline_node", "delete_screenplay_timeline_node"],
        prompt_factory=lambda fixture: TASK + "\n目标screenplay_id=" + fixture["screenplay_id"] + "；真实已有ID=" + json.dumps(fixture["ids"], ensure_ascii=False), use_writing_target=False)


if __name__ == "__main__":
    raise SystemExit(main())
