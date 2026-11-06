"""General project writes keep static contracts, revision guards and accepted audits."""

from __future__ import annotations

from copy import deepcopy

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project

from narrative import assistant, tool_api
from narrative.project_package import layout


def seed(ops):
    sid = ops.import_source("合成原文", TEXT)
    assert ops.submit_extraction(sid, draft_result())["status"] == "accepted"
    return sid


def batch(ops, sid):
    person = next(row for row in ops.graph_snapshot()["nodes"] if row["name"] == "乙")
    quote = "乙收起便笺。"
    evidence = {"source_id": sid, "quote": quote}
    disclosure = {"source_id": sid, "start": TEXT.index(quote)}
    objects = [
        {
            "kind": "node",
            "id": "generic-scene",
            "node_type": "scene",
            "name": "收便笺",
            "events": ["generic-event"],
            "evidence": evidence,
            "disclosure": disclosure,
        },
        {
            "kind": "node",
            "id": "generic-event",
            "node_type": "event",
            "name": "乙收起便笺",
            "participants": [person["id"]],
            "evidence": evidence,
            "disclosure": disclosure,
        },
        {"kind": "context", "id": "generic-context", "label": "备选", "context_type": "branch"},
        {
            "kind": "timeline",
            "id": "generic-timeline",
            "label": "备选时间轴",
            "context_id": "generic-context",
        },
    ]
    records = [
        {
            "kind": "property_definition",
            "id": "generic-property",
            "value_type": "string",
            "cardinality": "one",
        },
        {
            "kind": "property_change", "op": "set",
            "id": "generic-value",
            "node_id": person["id"],
            "property_id": "generic-property",
            "value": "便笺",
            "event_id": "generic-event",
            "evidence": evidence,
            "disclosure": disclosure,
        },
    ]
    return objects, records


def test_catalog_contains_all_project_writes_but_no_external_tools():
    project_tools = {name for name, tool in tool_api.TOOLS.items() if tool.scope == "project"}
    assert set(assistant.ASSISTANT_TOOLS) == project_tools
    assert {name for name in project_tools if tool_api.TOOLS[name].writes} <= set(
        assistant.ASSISTANT_TOOLS
    )
    assert not {"list_projects", "shell", "raw_sql"} & set(assistant.ASSISTANT_TOOLS)
    assert {
        "preview_changes",
        "apply_changes",
        "get_schema",
        "read_project_state",
        "revise_node",
        "record_property_change",
        "attach_children",
        "import_source",
    } <= project_tools


def test_generic_atomic_write_and_revision_guard(project):
    sid = seed(project)
    objects, records = batch(project, sid)
    revision = project.graph_project_info()["revision"]
    assert project.preview_changes(objects, records, revision)["valid"]
    assert project.graph_project_info()["revision"] == revision
    result = tool_api.call(
        "apply_changes",
        {
            "graph_objects": objects,
            "property_records": records,
            "expected_revision": revision,
            "reason": "按原文补录",
        },
        project=layout(project.project_info().path).entry,
    )
    assert result["status"] == "accepted" and result["revision"] == revision + 1
    assert {row["id"] for row in project.read_project_state()["property_records"]} >= {
        "generic-property",
        "generic-value",
    }
    assert "graph_json_schema" in project.get_schema()
    stale = project.apply_changes(objects, [], revision, "拒绝过期覆盖")
    assert (
        stale["status"] == "rejected" and project.graph_project_info()["revision"] == revision + 1
    )
    scene = next(row for row in project.graph_snapshot()["nodes"] if row["id"] == "generic-scene")
    updated = deepcopy(scene) | {"identity_note": "核对后的说明"}
    assert (
        project.apply_changes([updated], [], revision + 1, "修订已有对象")["status"] == "accepted"
    )


@pytest.mark.parametrize("bad", ["quote", "reference", "scene"])
def test_generic_invalid_batch_never_writes_or_stages(project, bad):
    sid = seed(project)
    objects, records = batch(project, sid)
    before = deepcopy(project.graph_snapshot())
    if bad == "quote":
        objects[0]["evidence"] = {"source_id": sid, "quote": "原文里没有"}
    elif bad == "reference":
        records[-1]["node_id"] = "missing-person"
    else:
        objects = [row for row in objects if row["id"] != "generic-scene"]
    if bad == "reference":
        from narrative.content_errors import ContentGuardError
        with pytest.raises(ContentGuardError):
            project.apply_changes(objects, records, before["revision"], "静态失败")
    else:
        result = project.apply_changes(objects, records, before["revision"], "静态失败")
        assert result["status"] == "staged" and not result["valid"]
    assert project.graph_snapshot() == before


def test_commit_failure_preserves_database(project, monkeypatch):
    sid = seed(project)
    objects, records = batch(project, sid)
    before = deepcopy(project.graph_snapshot())

    def fail(*args, **kwargs):
        raise ValueError("before commit")

    monkeypatch.setattr(project, "commit_changes", fail)
    with pytest.raises(ValueError, match="before commit"):
        project.apply_changes(objects, records, before["revision"], "失败不写")
    assert project.graph_snapshot() == before


def test_project_runtime_registers_and_dispatches_general_writes_only_in_active_task(project):
    import json

    from test_task_tool_host import post

    from narrative.conversation_runtime import close_project_runtime, get_runtime
    from narrative.conversation_session import use_chat
    from narrative.harness import HarnessSettings

    class Runner:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    sid = seed(project)
    objects, records = batch(project, sid)
    revision = project.graph_project_info()["revision"]
    runtime = get_runtime(project, HarnessSettings(), "offline-placeholder", runner_factory=Runner)
    entry = layout(project.project_info().path).entry
    try:
        assert {"apply_changes", "revise_node", "record_property_change"} <= {
            row["name"] for row in runtime.host.config["tools"]
        }
        with (
            use_chat(project) as chat,
            runtime.bind(
                chat["id"],
                assistant.tool_catalog(),
                lambda name, args: tool_api.call(name, args, project=entry),
            ),
        ):
            status, raw = post(
                runtime.host,
                "apply_changes",
                token=runtime.host.token,
                session_id=chat["id"],
                args={
                    "graph_objects": objects,
                    "property_records": records,
                    "expected_revision": revision,
                    "reason": "native通用写回归",
                },
            )
            assert status == 200 and json.loads(raw)["result"]["status"] == "accepted"
            status, _ = post(runtime.host, "list_projects", token=runtime.host.token, session_id=chat["id"])
            assert status == 400
        status, _ = post(runtime.host, "read_project_state", token=runtime.host.token, session_id=chat["id"])
        assert status == 400
        assert project.graph_project_info()["revision"] == revision + 1
    finally:
        close_project_runtime(project)


def test_feedback_failure_reports_accepted_database_result(project, monkeypatch):
    from narrative import conversation_session

    sid = seed(project)
    objects, records = batch(project, sid)
    before = project.graph_project_info()["revision"]

    def fail(*args):
        raise OSError("feedback file unavailable")

    monkeypatch.setattr(conversation_session, "record_business_action", fail)
    result = project.apply_changes(objects, records, before, "日志不可写仍报告实际结果")
    assert (
        result["status"] == "accepted" and result["feedback_error"] == "feedback file unavailable"
    )
    assert project.graph_project_info()["revision"] == before + 1


def test_identical_current_state_does_not_create_a_receipt(project):
    sid = seed(project)
    objects, records = batch(project, sid)
    first = project.apply_changes(
        objects, records, project.graph_project_info()["revision"], "补录"
    )
    before = deepcopy(project.property_records())
    repeated = project.apply_changes(objects, records, first["revision"], "重复相同当前状态")
    assert repeated["status"] == "unchanged"
    assert repeated["revision"] == first["revision"]
    assert "change_id" not in repeated
    assert project.property_records() == before
