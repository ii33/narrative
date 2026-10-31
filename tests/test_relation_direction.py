"""关系名站哪一边：A —儿子→ B 是「B 是 A 的儿子」，两侧同一个词等于没写方向。

模型很容易把「父子」「上下级」这种**成对**的叫法当成关系名，两侧各写一遍——图上
就出现同一个词两次，方向成了摆设。这里钉住审核能把它挑出来，而真正写对的一对
（父亲 / 儿子）不报。
"""

from __future__ import annotations

from narrative.extraction_review import RELATION_PROMPT
from narrative.property_audit import audit_properties


def _people(*names: str) -> list[dict]:
    return [
        {"id": f"P{index}", "node_type": "person", "name": name}
        for index, name in enumerate(names, start=1)
    ]


def _value(left: str, right: str, kind: str) -> dict:
    return {
        "kind": "property_value",
        "id": f"val_{left}_{right}_{kind}",
        "node_id": left,
        "property_id": kind,
        "value": right,
    }


def _codes(report: dict) -> list[str]:
    return [issue["code"] for issue in report["issues"]]


def test_a_pair_name_is_reported() -> None:
    """「父子」是一个对子，不是某一方的身份：写上去读不出谁是谁的父亲。"""
    report = audit_properties(
        [_value("P1", "P2", "父子")], _people("林舟", "老周")
    )
    assert "relation_pair_name" in _codes(report)


def test_the_same_role_on_both_sides_is_reported() -> None:
    report = audit_properties(
        [_value("P1", "P2", "父亲"), _value("P2", "P1", "父亲")],
        _people("林舟", "老周"),
    )
    assert "relation_same_both_ways" in _codes(report)
    issue = next(i for i in report["issues"] if i["code"] == "relation_same_both_ways")
    assert "儿子" in issue["suggestion"]


def test_a_correct_pair_is_not_reported() -> None:
    """A —儿子→ B、B —父亲→ A：各站一边，没有可挑的。"""
    report = audit_properties(
        [_value("P1", "P2", "儿子"), _value("P2", "P1", "父亲")],
        _people("林舟", "老周"),
    )
    assert not [code for code in _codes(report) if code.startswith("relation_")]


def test_a_symmetric_relation_written_twice_is_left_alone() -> None:
    """仇敌两侧同一个词在语义上没错，图上会合成一条；审核不为此报噪声。"""
    report = audit_properties(
        [_value("P1", "P2", "仇敌"), _value("P2", "P1", "仇敌")],
        _people("林舟", "苏娘"),
    )
    assert not [code for code in _codes(report) if code.startswith("relation_")]


def test_the_relation_prompt_says_which_side_and_shows_it() -> None:
    text = RELATION_PROMPT.format(people="- 林舟", events="- 甲", text="原文")
    # 约定本身要写出来：对方是这一方的什么人。
    assert "对方是 `from` 的什么人" in text
    # 例子必须成对出现，而且两个词相反——规则只写在正文里，模型不会照做。
    assert '"kind": "儿子"' in text
    assert '"kind": "父亲"' in text
    # 成对的叫法要明确排除，否则模型仍然会写「父子」。
    assert "父子" in text and "对子" in text
