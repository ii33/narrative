"""大事件 → 场景 → 事件：层级是硬的，大事件的样子跟着阅读位置长。

两件事在这里钉住：

* 第一列只有大事件。没有归属的场景进「未归入大事件」这个桶，而不是混进第一列
  冒充一条大事件——混在一起时读者分不清自己在选哪一级。
* 大事件自己不写属性。每个场景说一句「它走到这里的样子」，记录挂在场景上；
  大事件那一行是借来的，读者停在哪就说到哪，编辑器里看不到它（没有可改的东西）。
"""

from __future__ import annotations

from pathlib import Path

from narrative.browse_view import (
    ORPHAN_SCENE,
    ORPHAN_STORYLINE,
    STORYLINE_PROGRESS,
    moment_tree,
)
from narrative.project_catalog import create_graph_project

NODES = [
    {"kind": "node", "id": "1", "node_type": "storyline", "name": "林舟被带进另一界",
     "provisional_name": "有人找上门", "events": ["2", "3"]},
    {"kind": "node", "id": "2", "node_type": "scene", "name": "雨夜的门口", "events": ["4", "5"]},
    {"kind": "node", "id": "3", "node_type": "scene", "name": "旧车站", "events": ["6", "7"]},
    {"kind": "node", "id": "4", "node_type": "event", "name": "老周敲门"},
    {"kind": "node", "id": "5", "node_type": "event", "name": "林舟接过铜钥匙"},
    {"kind": "node", "id": "6", "node_type": "event", "name": "两人到旧车站"},
    {"kind": "node", "id": "7", "node_type": "event", "name": "老周打开铁门"},
    # 不属于任何大事件的场景，用来验证桶。
    {"kind": "node", "id": "10", "node_type": "scene", "name": "无人认领的一段", "events": ["11"]},
    {"kind": "node", "id": "11", "node_type": "event", "name": "什么都没发生"},
]


def _project(tmp_path: Path):
    ops = create_graph_project(tmp_path / "story.nproj")
    ops.graph_accept(ops.graph_propose(NODES, ops.graph_project_info()["revision"]), 1)
    return ops


def _progress(ops, rows: list[tuple[str, str, str]]) -> None:
    """rows: (记录 id, 场景 id, 那句话)。"""
    records = [
        {"kind": "property_definition", "id": STORYLINE_PROGRESS,
         "value_type": "string", "cardinality": "one"}
    ]
    for record_id, scene_id, value in rows:
        records.append({
            "kind": "property_value",
            "id": record_id,
            "node_id": scene_id,
            "property_id": STORYLINE_PROGRESS,
            "value": value,
            "event_id": "5" if scene_id == "2" else "7",
        })
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(ops.property_propose(records, revision, source="manual"), 1)


def _rows(tree: dict, rank: int) -> list[dict]:
    return [row for row in tree["nodes"] if row["rank"] == rank]


def test_the_first_column_holds_only_storylines(tmp_path: Path) -> None:
    ops = _project(tmp_path)
    tree = moment_tree(ops.graph_snapshot())

    assert [level["label"] for level in tree["levels"]] == ["大事件", "场景", "事件"]
    assert {row["level"] for row in _rows(tree, 0)} == {"storyline"}
    assert [row["name"] for row in _rows(tree, 0)] == ["林舟被带进另一界", "未归入大事件"]


def test_a_scene_hangs_under_its_storyline_and_an_event_under_its_scene(
    tmp_path: Path,
) -> None:
    ops = _project(tmp_path)
    tree = moment_tree(ops.graph_snapshot())

    scenes = {row["id"]: row for row in _rows(tree, 1)}
    assert scenes["2"]["parent_id"] == "1"
    assert scenes["3"]["parent_id"] == "1"
    # 没有大事件的场景进桶，不是进第一列。
    assert scenes["10"]["parent_id"] == ORPHAN_STORYLINE

    events = {row["id"]: row for row in _rows(tree, 2)}
    assert events["4"]["parent_id"] == "2"
    assert events["6"]["parent_id"] == "3"


def test_the_orphan_bucket_is_marked_and_not_a_reading_position(tmp_path: Path) -> None:
    ops = _project(tmp_path)
    tree = moment_tree(ops.graph_snapshot())

    bucket = next(row for row in tree["nodes"] if row["id"] == ORPHAN_STORYLINE)
    assert bucket["synthetic"] is True
    assert bucket["parent_id"] == ""
    assert bucket["label_id"] == ""
    # 桶不是一条数据，所以它不出现在任何一次「选一步」的结果里。
    from narrative.browse_view import cut_for

    assert cut_for(ops.graph_snapshot(), ORPHAN_STORYLINE) is None


def test_a_storyline_is_provisional_until_its_last_event_is_read(tmp_path: Path) -> None:
    ops = _project(tmp_path)
    snapshot = ops.graph_snapshot()

    before = moment_tree(snapshot, cut_event_id="4")
    story = next(row for row in _rows(before, 0) if row["id"] == "1")
    assert story["name"] == "有人找上门"
    assert story["provisional"] is True
    assert story["final_name"] == "林舟被带进另一界"

    after = moment_tree(snapshot, cut_event_id="7")
    story = next(row for row in _rows(after, 0) if row["id"] == "1")
    assert story["name"] == "林舟被带进另一界"
    assert story["provisional"] is False


def test_the_summary_is_the_last_scene_the_reader_finished(tmp_path: Path) -> None:
    ops = _project(tmp_path)
    _progress(
        ops,
        [
            ("v1", "2", "老周找上门，说林舟得跟他走"),
            ("v2", "3", "林舟已经到了旧车站，门还没开"),
        ],
    )
    snapshot = ops.graph_snapshot()
    records = ops.property_records(include_retracted=True)

    def summary_at(cut: str) -> str:
        tree = moment_tree(snapshot, cut_event_id=cut, records=records)
        return next(row for row in _rows(tree, 0) if row["id"] == "1")["summary"]

    # 第一场还没读完，大事件一句话都没有——不编。
    assert summary_at("4") == ""
    assert summary_at("5") == "老周找上门，说林舟得跟他走"
    assert summary_at("7") == "林舟已经到了旧车站，门还没开"


def test_each_scene_carries_its_own_sentence(tmp_path: Path) -> None:
    ops = _project(tmp_path)
    _progress(ops, [("v1", "2", "老周找上门"), ("v2", "3", "到了旧车站")])
    tree = moment_tree(ops.graph_snapshot(), records=ops.property_records())

    scenes = {row["id"]: row for row in _rows(tree, 1)}
    assert scenes["2"]["summary"] == "老周找上门"
    assert scenes["3"]["summary"] == "到了旧车站"


def test_the_storyline_pane_borrows_the_latest_sentence(tmp_path: Path) -> None:
    ops = _project(tmp_path)
    _progress(
        ops,
        [
            ("v1", "2", "老周找上门，说林舟得跟他走"),
            ("v2", "3", "林舟已经到了旧车站，门还没开"),
        ],
    )

    def row_at(cut: str) -> dict:
        summary = ops.property_node_summary("1", cut=cut)
        return next(
            item for item in summary["properties"]
            if item["property_id"] == STORYLINE_PROGRESS
        )

    first = row_at("5")
    assert first["value"] == "老周找上门，说林舟得跟他走"
    assert first["derived"] is True
    assert first["from_scene"]["id"] == "2"

    second = row_at("7")
    assert second["value"] == "林舟已经到了旧车站，门还没开"
    assert second["from_scene"]["id"] == "3"


def test_the_editor_does_not_offer_a_borrowed_row(tmp_path: Path) -> None:
    """派生行没有可改的记录：编辑器里出现它，就等于让人去改一条不存在的记录。"""
    ops = _project(tmp_path)
    _progress(ops, [("v1", "2", "老周找上门")])

    editor = ops.node_editor("1")
    assert [item for item in editor["properties"]
            if item["property_id"] == STORYLINE_PROGRESS] == []


def test_an_event_keeps_no_properties_of_its_own(tmp_path: Path) -> None:
    """事件只讲发生了什么。挂在事件上的属性是这一层不该有的东西。"""
    from narrative.property_audit import audit_properties

    ops = _project(tmp_path)
    revision = ops.graph_project_info()["revision"]
    records = [
        {"kind": "property_definition", "id": "state", "value_type": "string",
         "cardinality": "one"},
        {"kind": "property_value", "id": "v1", "node_id": "6",
         "property_id": "state", "value": "门开了", "event_id": "6"},
    ]
    ops.property_accept(ops.property_propose(records, revision, source="manual"), 1)

    report = audit_properties(ops.property_records(), ops.graph_snapshot()["nodes"])
    codes = {issue["code"] for issue in report["issues"]}
    assert "property_on_event" in codes


def test_a_scene_without_a_storyline_is_reported(tmp_path: Path) -> None:
    from narrative.property_audit import audit_properties

    ops = _project(tmp_path)
    report = audit_properties(ops.property_records(), ops.graph_snapshot()["nodes"])
    messages = [
        issue["message"] for issue in report["issues"]
        if issue["code"] == "scene_without_storyline"
    ]
    assert messages and "无人认领的一段" in messages[0]


def test_orphan_events_get_a_bucket_of_their_own(tmp_path: Path) -> None:
    """事件没有场景时也不许挤进第一列。"""
    ops = create_graph_project(tmp_path / "loose.nproj")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "scene", "name": "有场景的一场",
                 "events": ["2"]},
                {"kind": "node", "id": "2", "node_type": "event", "name": "正常事件"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "没人认领的事件"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    tree = moment_tree(ops.graph_snapshot())

    assert {row["level"] for row in _rows(tree, 0)} == {"storyline"}
    assert ORPHAN_SCENE in {row["id"] for row in _rows(tree, 1)}
    loose = next(row for row in _rows(tree, 2) if row["id"] == "3")
    assert loose["parent_id"] == ORPHAN_SCENE


def test_the_progress_sentence_is_anchored_on_the_scenes_last_event(
    tmp_path: Path,
) -> None:
    """一句话说的是「这一场读完时的样子」，所以锚在最后一步，不是模型引的那一步。

    模型给了 `at_event`（它引文所在的事件），但那一步之后这一场还没走完；照它锚，
    读者读到一半就看到整场的总结。
    """
    from narrative.extraction import build_candidate
    from narrative.project_catalog import create_graph_project

    ops = create_graph_project(tmp_path / "anchor.nproj")
    text = "老周敲门。林舟接过铜钥匙。"
    source_id = ops.import_source("雨夜", text)
    data = {
        "source_id": source_id,
        "nodes": [
            {"type": "storyline", "name": "林舟被带走", "start": 0,
             "quotes": ["老周敲门。"], "scene": "", "storyline": "",
             "provisional_name": "有人找上门"},
            {"type": "scene", "name": "雨夜的门口", "start": 0,
             "quotes": ["老周敲门。"], "scene": "", "storyline": "林舟被带走",
             "provisional_name": ""},
            {"type": "event", "name": "老周敲门", "start": 0,
             "quotes": ["老周敲门。"], "scene": "雨夜的门口", "storyline": "",
             "participants": []},
            {"type": "event", "name": "林舟接过铜钥匙", "start": 5,
             "quotes": ["林舟接过铜钥匙。"], "scene": "雨夜的门口", "storyline": "",
             "participants": []},
        ],
        "properties": [
            {"node": "雨夜的门口", "property": STORYLINE_PROGRESS,
             "value": "老周找上门", "value_type": "string",
             "quotes": ["老周敲门。"], "start": 0, "at_event": "老周敲门",
             "scope": "world", "holder": "", "source": "narration",
             "related_nodes": [], "category": "大事件"},
        ],
        "rejected": [],
    }

    built = build_candidate(ops, data)
    record = next(
        item for item in built["property_records"]
        if item.get("property_id") == STORYLINE_PROGRESS
    )
    assert record["anchor_basis"] == "scene_end"
    # E2 是这一场最后一步；模型说的 E1 不是。
    assert record["event_id"] == "E2"


def test_progress_written_against_a_location_lands_on_its_scene(tmp_path: Path) -> None:
    """模型会把 `node` 写成地点名（「旧站台」既是地点又是场景）。

    这句话只能挂在场景上，所以按它自己的落点事件归位到那一场——用模型给的位置，
    不是猜；归不回去就原样留着，审核会报「场景没写进展」。
    """
    from narrative.extraction import build_candidate
    from narrative.project_catalog import create_graph_project

    ops = create_graph_project(tmp_path / "reroute.nproj")
    text = "两人到了旧站台。老周以白光带走林舟。"
    source_id = ops.import_source("旧站台", text)
    data = {
        "source_id": source_id,
        "nodes": [
            {"type": "storyline", "name": "林舟随老周离开", "start": 0,
             "quotes": ["两人到了旧站台。"], "scene": "", "storyline": "",
             "provisional_name": ""},
            {"type": "scene", "name": "旧站台", "start": 0,
             "quotes": ["两人到了旧站台。"], "scene": "", "storyline": "林舟随老周离开",
             "provisional_name": ""},
            {"type": "location", "name": "旧站台", "start": 0,
             "quotes": ["两人到了旧站台。"], "scene": "", "storyline": "",
             "provisional_name": ""},
            {"type": "event", "name": "老周以白光带走林舟", "start": 7,
             "quotes": ["老周以白光带走林舟。"], "scene": "旧站台", "storyline": "",
             "participants": []},
        ],
        "properties": [
            # node 写的是地点，落点事件在「旧站台」这一场里
            {"node": "旧站台", "property": STORYLINE_PROGRESS,
             "value": "老周改造完林舟的身体，带他离开", "value_type": "string",
             "quotes": ["老周以白光带走林舟。"], "start": 7,
             "at_event": "老周以白光带走林舟",
             "scope": "world", "holder": "", "source": "narration",
             "related_nodes": [], "category": "大事件"},
        ],
        "rejected": [],
    }

    built = build_candidate(ops, data)
    record = next(
        item for item in built["property_records"]
        if item.get("property_id") == STORYLINE_PROGRESS
    )
    scene_id = next(
        obj["id"] for obj in built["objects"]
        if obj.get("kind") == "node" and obj.get("node_type") == "scene"
    )
    assert record["node_id"] == scene_id
