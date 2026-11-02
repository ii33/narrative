"""Distinct pair roles control spacing; duplicates and text length do not."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import pytest
from test_chat_scroll import transcript_shell


def layout(ids, pairs, *, extra=None, radii=None):
    from PySide6.QtQml import QJSEngine
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    engine = QJSEngine()
    source = (
        Path("src/narrative/qml/RelationLayout.js")
        .read_text(encoding="utf-8")
        .replace(".pragma library", "")
    )
    assert not engine.evaluate(source).isError()
    persons = [{"id": i, "name": i} for i in ids]
    links = [{"from": a, "to": b, "kind": "关系标签"} for a, b in pairs]
    links.extend(extra or [])
    value = engine.evaluate(
        "JSON.stringify(layout("
        + ",".join(json.dumps(x) for x in (persons, links, radii or {i: 20 for i in ids}, 900, 650))
        + "))"
    )
    assert not value.isError()
    assert app is not None
    return json.loads(value.toString())


def distance(data, a, b):
    x, y = data["positions"][a], data["positions"][b]
    return math.hypot(x["x"] - y["x"], x["y"] - y["y"])


def no_overlap(data):
    for a, b in itertools.combinations(data["positions"], 2):
        assert distance(data, a, b) >= data["positions"][a]["r"] + data["positions"][b]["r"] - 0.01


@pytest.mark.parametrize("variation", ["reversed", "multi_label", "mixed"])
def test_direction_mirrors_preserve_positions_but_distinct_roles_shorten_pair_distance(variation):
    ids = list("abcdef")
    pairs = [("a", "b"), ("b", "c"), ("c", "d"), ("b", "e"), ("e", "f")]
    baseline = layout(ids, pairs)
    if variation == "reversed":
        changed = layout(ids, [(b, a) for a, b in reversed(pairs)])
    else:
        extra = [
            {
                "from": "c" if i % 2 else "b",
                "to": "b" if i % 2 else "c",
                "kind": ("长标签" * 30) + str(i),
                "state": "状态" * 20,
            }
            for i in range(25)
        ]
        changed = layout(ids, list(reversed(pairs)) if variation == "mixed" else pairs, extra=extra)
    if variation == "reversed":
        assert changed == baseline
    else:
        assert distance(changed, "b", "c") < distance(baseline, "b", "c")
        no_overlap(changed)


def test_chain_direct_neighbors_are_closer_on_average_than_far_hops():
    ids = list("abcdef")
    pairs = list(zip(ids, ids[1:], strict=False))
    data = layout(ids, pairs)
    no_overlap(data)
    direct = sum(distance(data, a, b) for a, b in pairs) / len(pairs)
    far = [distance(data, a, b) for i, a in enumerate(ids) for j, b in enumerate(ids) if j - i >= 3]
    assert direct < sum(far) / len(far)


def test_two_connected_clusters_preserve_local_affinity_and_no_collision():
    pairs = [("a", "b"), ("a", "c"), ("b", "c"), ("d", "e"), ("d", "f"), ("e", "f"), ("c", "d")]
    data = layout(list("abcdef"), pairs)
    no_overlap(data)
    local = [distance(data, a, b) for a, b in pairs if (a, b) != ("c", "d")]
    cross = [distance(data, a, b) for a in "abc" for b in "def" if (a, b) != ("c", "d")]
    assert sum(local) / len(local) < sum(cross) / len(cross)


def test_nonadjacent_hubs_keep_their_own_neighbor_groups_without_mutual_attraction():
    pairs = [("h1", x) for x in "abc"] + [("h2", x) for x in "xyz"] + [("c", "z")]
    data = layout(["h1", "h2", *"abcxyz"], pairs)
    no_overlap(data)
    local = [distance(data, "h1", x) for x in "abc"] + [distance(data, "h2", x) for x in "xyz"]
    assert distance(data, "h1", "h2") > sum(local) / len(local)
    assert sum(distance(data, "h1", x) for x in "abc") < sum(distance(data, "h1", x) for x in "xyz")
    assert sum(distance(data, "h2", x) for x in "xyz") < sum(distance(data, "h2", x) for x in "abc")


def test_qml_radii_and_positions_use_unique_neighbors_not_displayed_relations(
    tmp_path, monkeypatch
):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setProperty("currentPage", "relations")
        ui.window.setProperty("assistantOpen", False)
        ui.settle()
        page = ui.window.findChild(object, "relationPage")
        persons = [{"id": i, "name": i} for i in "abcdef"]
        edges = [{"from": "a", "to": x, "kind": "直接关系", "state": ""} for x in "bcdef"]
        page.apply({"persons": persons, "edges": edges})
        ui.wait(lambda: page.property("labelGeometryReady"))

        def plain(v):
            return v.toVariant() if hasattr(v, "toVariant") else v

        before = plain(page.property("graphLayout"))
        before_radii = {p["id"]: page.radiusOf(p) for p in persons}
        assert {frozenset((e["from"], e["to"])) for e in plain(page.links())} == {
            frozenset(("a", x)) for x in "bcdef"
        }
        # Same pair represented in opposite directions and by several roles.
        extra = [
            {
                "from": "b" if i % 2 else "a",
                "to": "a" if i % 2 else "b",
                "kind": "额外长关系标签" + str(i),
                "state": "",
            }
            for i in range(6)
        ]
        page.apply({"persons": persons, "edges": list(reversed(edges)) + extra})
        ui.wait(lambda: page.property("labelGeometryReady"))
        ui.settle()
        assert distance(plain(page.property("graphLayout")), "a", "b") < distance(before, "a", "b")
        assert {p["id"]: page.radiusOf(p) for p in persons} == before_radii
        assert page.degreeOf("a") == 5 and page.degreeOf("b") == 1
        actual = plain(page.property("graphLabelLayout"))["edges"]
        assert all(e["edge"]["from"] == "a" or e["edge"]["to"] == "a" for e in actual)
        assert len(actual) == len(plain(page.links()))


def test_repeated_disclosures_and_reverse_mirrors_do_not_add_weight():
    pairs = [("a", "b"), ("b", "c")]
    baseline = layout(list("abc"), pairs)
    duplicate = [{"from": "b", "to": "a", "kind": "关系标签", "record_id": str(i)} for i in range(20)]
    assert layout(list("abc"), pairs, extra=duplicate) == baseline


def test_role_count_not_label_length_controls_distance_with_collision_floor():
    ids = list("ab")
    baseline = layout(ids, [("a", "b")])
    short = [{"from": "a", "to": "b", "kind": f"角色{i}"} for i in range(4)]
    long = [dict(row, kind=row["kind"] + "很长" * 100) for row in short]
    more = layout(ids, [("a", "b")], extra=short)
    assert more == layout(ids, [("a", "b")], extra=long)
    assert distance(more, "a", "b") < distance(baseline, "a", "b")
    no_overlap(more)


@pytest.mark.parametrize("pairs", [[("a", "b"), ("a", "c"), ("b", "c")],
                                  [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]])
def test_competing_neighbor_springs_prioritize_more_roles_on_the_same_pair(pairs):
    ids = sorted({x for pair in pairs for x in pair})
    single = layout(ids, pairs)
    multiple = layout(ids, pairs, extra=[{"from": "a", "to": "b", "kind": "另一角色"}])
    no_overlap(multiple)
    assert distance(multiple, "a", "b") < distance(single, "a", "b")
    assert distance(multiple, "a", "b") < distance(multiple, "a", "c")


@pytest.mark.parametrize("seed_order", [0, 1, 2])
def test_eight_node_competing_topology_places_two_role_pairs_nearer_than_single_roles(seed_order):
    # Anonymous structural fixture reproduces a hub with two extra-role pairs,
    # a triangle, and a neighbor's own leaf; no private text or user IDs.
    ids = ["hub", "peer", "partner", "guide", "leaf", "x", "y", "z"]
    pairs = [("hub", x) for x in ["peer", "partner", "guide", "x", "y", "z"]]
    pairs += [("peer", "partner"), ("guide", "leaf")]
    radii = {i: 18.2 for i in ids}
    radii.update(hub=25.74, peer=20.35, partner=20.35, guide=20.35)
    extra = [{"from": "hub", "to": x, "kind": "附加角色"} for x in ["partner", "guide"]]
    if seed_order:
        import random
        shuffled = ids[:]
        random.Random(seed_order).shuffle(shuffled)
        mapping = dict(zip(ids, shuffled, strict=True))
        pairs = [(mapping[a], mapping[b]) for a, b in pairs]
        extra = [dict(row, **{"from": mapping[row["from"]], "to": mapping[row["to"]]}) for row in extra]
        radii = {mapping[key]: value for key, value in radii.items()}
    else:
        mapping = {key: key for key in ids}
    weighted = layout(ids, pairs, extra=extra, radii=radii)
    single = layout(ids, pairs, radii=radii)
    no_overlap(weighted)
    for x in ["partner", "guide"]:
        assert distance(weighted, mapping["hub"], mapping[x]) < distance(single, mapping["hub"], mapping[x])
        assert distance(weighted, mapping["hub"], mapping[x]) < min(distance(weighted, mapping["hub"], mapping[y]) for y in ["peer", "x", "y", "z"])
