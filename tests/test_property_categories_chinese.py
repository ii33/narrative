"""Exact Chinese property labels share the English category hints."""
from copy import deepcopy

from agent_fixtures import draft_result
from agent_fixtures import project as project

from narrative import property_categories


def test_chinese_display_labels_reverse_to_unique_existing_categories():
    pairs = [
        ("age", "年龄", "基本信息"),
        ("height", "身高", "外貌"),
        ("identity", "身份", "身份与经历"),
        ("cultivation_stage", "修为境界", "能力与身体状态"),
        ("plan", "打算", "心理与意图"),
        ("plan", "计划", "心理与意图"),
        ("friend", "朋友", "关系"),
    ]
    for english, chinese, expected in pairs:
        assert property_categories.suggest(english, "person")[0] == expected
        assert property_categories.suggest(chinese, "person") == (expected, "name")


def test_missing_vocabulary_category_and_unknown_names_still_fall_back():
    custom = {"person": ["基本信息"]}
    assert property_categories.suggest("年龄", "person", config=custom) == ("基本信息", "name")
    assert property_categories.suggest("身高", "person", config=custom) == ("其他", "fallback")
    assert property_categories.suggest("未登记的词", "person") == ("其他", "fallback")
    assert property_categories.resolve(
        {"property_id": "年龄", "category": "人工分类"}, "person", custom
    ) == ("人工分类", "extraction")
    assert property_categories.resolve(
        {"id": "PV1", "property_id": "年龄"}, "person", overrides={"PV1": "手动分类"}
    ) == ("手动分类", "manual")


def test_ambiguous_reverse_label_falls_back(monkeypatch):
    monkeypatch.setitem(property_categories._LABEL_HINTS, "person", {"歧义标签": {"基本信息", "关系"}})
    assert property_categories.suggest("歧义标签", "person") == ("其他", "fallback")


def test_event_and_action_location_names_use_basic_information():
    for node_type in ("event", "action"):
        assert property_categories.suggest("地点", node_type) == ("基本信息", "name")
        assert property_categories.suggest("location", node_type) == ("基本信息", "name")


def test_operations_summary_classifies_chinese_ids_without_rewriting_records(project):
    text = "甲约好接乙。乙收起便笺。甲接到乙后离开。"
    source_id = project.import_source("分类回归合成原文", text)
    data = draft_result()
    data["expirations"] = []
    properties = data["properties"]
    for record in properties:
        record.pop("category", None)
    properties[0]["property"] = "计划"
    properties.extend([
        {"node": "甲", "property": name, "value": value, "value_type": "string",
         "quotes": ["甲约好接乙。"], "at_event": "甲约好接乙"}
        for name, value in [
            ("年龄", "二十岁"), ("身高", "一米七"), ("身份", "门主"),
            ("修为境界", "灵寂期"), ("秘书", "乙"), ("手下", "乙"),
            ("外号", "铁掌"), ("女友", "乙"), ("领路人", "乙"),
        ]
    ])
    result = project.submit_extraction(source_id, data)
    assert result["status"] == "accepted"

    person_id = next(node["id"] for node in project.graph_snapshot()["nodes"]
                     if node["name"] == "甲")
    before = deepcopy(project.property_records())
    summary = project.property_node_summary(person_id)
    categories = {row["property_id"]: row["category"] for row in summary["properties"]}
    assert categories["年龄"] == "基本信息"
    assert categories["身高"] == "外貌"
    assert categories["身份"] == "身份与经历"
    assert categories["修为境界"] == "能力与身体状态"
    assert categories["计划"] == "心理与意图"
    assert categories["外号"] == "基本信息"
    assert all(categories[name] == "关系" for name in ("朋友", "秘书", "手下", "女友", "领路人"))
    assert project.property_records() == before
