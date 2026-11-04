"""Script import follows authored adaptation order, independent of source hierarchy."""
from agent_fixtures import project as project
from test_adaptation_hierarchy_contract import seed
from test_screenplay_timeline import create, import_points


def moment_ids(state):
    return [row["id"] for row in state["nodes"] if row["node_type"] in {"storyline", "big_event", "scene", "event", "action"}]


def test_candidates_and_initial_import_keep_cross_branch_adaptation_order(project):
    ids = seed(project)
    project.sync_adaptation()
    # Move E and its A away from their structural branch, without changing ownership.
    project.move_adaptation_node(ids["开门"], after_id=ids["过桥"])
    project.move_adaptation_node(ids["开门动作"], after_id=ids["开门"])
    source = project.adaptation_snapshot()
    script = create(project)
    candidates = project.import_screenplay_timeline_candidates(script)
    expected = moment_ids(source)
    assert [row["id"] for row in candidates] == expected
    assert expected.index(ids["过桥"]) < expected.index(ids["开门"]) < expected.index(ids["开门动作"])
    rows = {row["id"]: row for row in candidates}
    assert rows[ids["开门"]]["parent_id"] == ids["屋内场"]
    assert rows[ids["开门动作"]]["parent_id"] == ids["开门"]
    imported = import_points(project, script, [ids["屋内"], ids["桥上"]])
    assert moment_ids(imported) == expected
    assert project.adaptation_snapshot() == source


def test_append_keeps_local_prefix_then_new_selection_in_adaptation_order(project):
    ids = seed(project)
    project.sync_adaptation()
    project.move_adaptation_node(ids["开门"], after_id=ids["过桥"])
    project.move_adaptation_node(ids["开门动作"], after_id=ids["开门"])
    script = create(project)
    first = import_points(project, script, [ids["停步"]])
    project.edit_screenplay_timeline_node(script, ids["停步"], {"name": "本地结尾"}, expected_timeline_revision=first["revision"], reason="保留本地编辑")
    before = project.read_screenplay_timeline(script)
    source = project.adaptation_snapshot()
    expected_new = [identifier for identifier in moment_ids(source) if identifier not in moment_ids(first)]
    appended = project.append_screenplay_timeline(script, [ids["屋内"], ids["桥上"]], before["revision"], source["revision"], "追加调序后的来源")
    assert moment_ids(appended) == moment_ids(before) + expected_new
    for old, current in zip(before["nodes"], appended["nodes"], strict=False):
        assert {k: v for k, v in old.items() if k != "events"} == {k: v for k, v in current.items() if k != "events"}
    assert next(row for row in appended["nodes"] if row["id"] == ids["停步"])["name"] == "本地结尾"
    repeated = project.append_screenplay_timeline(script, [ids["屋内"], ids["桥上"]], appended["revision"], source["revision"], "重复追加")
    assert repeated["nodes"] == appended["nodes"]
