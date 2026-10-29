"""界面上不该出现英文属性名。

属性名在数据里是英文标识符（`identity`、`holder`），显示时靠 `property_labels`
翻成中文。漏一条，读者就会在「身份」旁边看到 `office` —— 中英混着，一眼就露馅。
"""

from __future__ import annotations

import pytest

from narrative import property_labels
from narrative.property_audit import audit_properties


def test_every_label_is_chinese() -> None:
    wrong = {
        key: value
        for key, value in property_labels.LABELS.items()
        if not value.strip() or value.isascii()
    }
    assert wrong == {}


def test_the_labels_are_lowercase_keys() -> None:
    """查表时统一 lower()，表里写大写就永远命中不到。"""
    assert [key for key in property_labels.LABELS if key != key.lower()] == []


def test_a_known_name_gets_a_chinese_label() -> None:
    assert property_labels.label_for("identity") == "身份"
    assert property_labels.label_for("Identity") == "身份"
    assert property_labels.label_for(" troop_strength ") == "兵力"


def test_an_unknown_name_stays_what_it_is() -> None:
    """不猜：没对照的名字原样显示，好过编一个像样的中文。"""
    assert property_labels.label_for("something_new") == "something_new"


@pytest.mark.parametrize(
    "property_id",
    [
        # 三国试跑与工作工程里真实出现过的、曾经没有对照的那批
        "environment", "follower_count", "lineage", "native_place",
        "quantity", "title", "troop_strength",
        # 常见的其它方面
        "office", "troops", "residence", "sibling_order", "intent", "slogan",
    ],
)
def test_observed_aspects_all_read_as_chinese(property_id: str) -> None:
    assert not property_labels.label_for(property_id).isascii()


def test_the_audit_points_out_an_aspect_without_a_label() -> None:
    records = [
        {"kind": "property_definition", "id": "brand_new_aspect", "value_type": "string"},
        {
            "kind": "property_value", "id": "v1", "node_id": "1",
            "property_id": "brand_new_aspect", "value": "x", "event_id": "e1",
        },
    ]
    nodes = [{"id": "1", "name": "甲", "node_type": "person"}]

    report = audit_properties(records, nodes)

    codes = [issue["code"] for issue in report["issues"]]
    assert "label_missing" in codes
    issue = next(item for item in report["issues"] if item["code"] == "label_missing")
    assert issue["property_id"] == "brand_new_aspect"
    assert issue["severity"] == "info"
    assert "改名" in issue["suggestion"]


def test_a_chinese_aspect_is_not_reported() -> None:
    """新提取按提示词就该给中文名，中文名当然不用再翻。"""
    records = [
        {"kind": "property_definition", "id": "身份", "value_type": "string"},
        {
            "kind": "property_value", "id": "v1", "node_id": "1",
            "property_id": "身份", "value": "秀才", "event_id": "e1",
        },
    ]
    nodes = [{"id": "1", "name": "甲", "node_type": "person"}]

    report = audit_properties(records, nodes)
    assert [issue for issue in report["issues"] if issue["code"] == "label_missing"] == []
