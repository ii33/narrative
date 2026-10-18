"""Context-aware, evidence-preserving read projection for accepted v4 records."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime

from .graph_models import TimeRef


def _contexts(objects: dict[str, dict], selected: str) -> list[str]:
    lineage, seen = [], set()
    current = selected
    while current:
        if current in seen or current not in objects or objects[current]["kind"] != "context":
            raise ValueError("查询语境不存在或形成循环")
        seen.add(current)
        lineage.append(current)
        current = objects[current].get("parent_id")
    return lineage


def _time_key(ref: dict) -> tuple:
    return (
        ref["timeline_id"], ref["form"], ref.get("number"),
        ref.get("datetime"), ref.get("event_id"), ref.get("boundary"),
    )


class _TimeOrder:
    def __init__(self, snapshot: dict, contexts: set[str]):
        self.less: dict[tuple, set[tuple]] = defaultdict(set)
        self.same: dict[tuple, set[tuple]] = defaultdict(set)
        predicates = {item["id"]: item for item in snapshot.get("predicates", [])}
        for assertion in snapshot.get("assertions", []):
            if (assertion["context_id"] not in contexts or assertion["scope"] != "world"
                    or assertion["assessment"] == "retracted" or assertion["polarity"] != "positive"
                    or assertion["modality"] != "actual"):
                continue
            relation = predicates.get(assertion["predicate_id"], {}).get("temporal_relation")
            left, right = assertion.get("temporal_left"), assertion.get("temporal_right")
            if not left or not right or left["timeline_id"] != right["timeline_id"]:
                continue
            a, b = _time_key(left), _time_key(right)
            if relation == "before":
                self.less[a].add(b)
            elif relation == "after":
                self.less[b].add(a)
            elif relation == "same":
                self.same[a].add(b)
                self.same[b].add(a)
        for node in snapshot.get("nodes", []):
            if node["node_type"] != "event" or node["context_id"] not in contexts:
                continue
            extent = node.get("occurrence")
            if not extent:
                continue
            for phase in ("start", "end"):
                if extent["kind"] == "point":
                    ref = extent["point"]
                else:
                    bound = extent[phase]
                    if bound["state"] != "known":
                        continue
                    ref = bound["ref"]
                event_ref = {
                    "timeline_id": ref["timeline_id"], "form": "event_boundary",
                    "event_id": node["id"], "boundary": phase,
                }
                self.same[_time_key(event_ref)].add(_time_key(ref))
                self.same[_time_key(ref)].add(_time_key(event_ref))

    @staticmethod
    def _reaches(start: tuple, end: tuple, graph: dict[tuple, set[tuple]]) -> bool:
        frontier, seen = [start], set()
        while frontier:
            current = frontier.pop()
            if current == end:
                return True
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(graph.get(current, ()))
        return False

    def compare(self, left: dict, right: dict) -> int | None:
        if left["timeline_id"] != right["timeline_id"]:
            return None
        if left["form"] == right["form"] == "number":
            return (left["number"] > right["number"]) - (left["number"] < right["number"])
        if left["form"] == right["form"] == "datetime":
            try:
                a, b = datetime.fromisoformat(left["datetime"]), datetime.fromisoformat(right["datetime"])
                return (a > b) - (a < b)
            except (ValueError, TypeError):
                return None
        a, b = _time_key(left), _time_key(right)
        if self._reaches(a, b, self.same):
            return 0
        starts = {node for node in self.same if self._reaches(a, node, self.same)} | {a}
        ends = {node for node in self.same if self._reaches(b, node, self.same)} | {b}
        for start in starts:
            for end in ends:
                if start[1] == end[1] == "number":
                    return (start[2] > end[2]) - (start[2] < end[2])
                if start[1] == end[1] == "datetime":
                    try:
                        x, y = datetime.fromisoformat(start[3]), datetime.fromisoformat(end[3])
                        return (x > y) - (x < y)
                    except (ValueError, TypeError):
                        pass
        if any(self._reaches(start, end, self.less) for start in starts for end in ends):
            return -1
        if any(self._reaches(end, start, self.less) for start in starts for end in ends):
            return 1
        return None


def _story_status(assertion: dict, cut: dict | None, order: _TimeOrder) -> str:
    if cut is None:
        return "not_evaluated"
    extent = assertion["validity"]
    if extent["kind"] == "point":
        relation = order.compare(cut, extent["point"])
        if relation is None:
            return "unknown"
        return {0: "active", -1: "future", 1: "expired"}.get(relation, "unknown")
    start, end = extent["start"], extent["end"]
    if start["state"] == "known":
        relation = order.compare(cut, start["ref"])
        if relation == -1:
            return "future"
        if relation is None:
            return "unknown"
    if end["state"] == "known":
        relation = order.compare(cut, end["ref"])
        if relation in {0, 1}:
            return "expired"
        if relation is None:
            return "unknown"
    return "active" if start["state"] != "unknown" and end["state"] != "unknown" else "unknown"


def _disclosure_status(assertion: dict, cut: dict | None, source_order: dict) -> str:
    if cut is None:
        return "not_evaluated"
    disclosure = assertion.get("disclosure")
    if disclosure is None:
        return "unknown"
    left = source_order.get(disclosure["source_id"])
    right = source_order.get(cut.get("source_id"))
    if left is None or right is None:
        return "unknown"
    if left < right or left == right and disclosure["start"] <= cut.get("start", 0):
        return "revealed"
    return "hidden"


def query_graph(
    snapshot: dict,
    *,
    node_id: str | None = None,
    query: str = "",
    node_ids: list[str] | None = None,
    predicate_ids: list[str] | None = None,
    kinds: list[str] | None = None,
    context_id: str = "canon",
    story_cut: dict | None = None,
    disclosure_cut: dict | None = None,
    limit: int | None = 200,
) -> dict:
    if limit is not None and (limit < 1 or limit > 2000):
        raise ValueError("检索数量须在 1–2000 之间")
    if story_cut is not None:
        story_cut = TimeRef.model_validate(story_cut).model_dump(mode="json", exclude_none=True)
    by_id = {item["id"]: item for item in snapshot["objects"]}
    lineage = _contexts(by_id, context_id)
    visible_contexts = set(lineage)
    nodes = {
        item["id"]: item for item in snapshot.get("nodes", [])
        if item["context_id"] in visible_contexts
    }
    predicates = {item["id"]: item for item in snapshot.get("predicates", [])}
    evidences = {item["id"]: item for item in snapshot.get("evidences", [])}
    links: dict[str, list[dict]] = defaultdict(list)
    for link in snapshot["evidence_links"]:
        if link["evidence_id"] in evidences:
            links[link["assertion_id"]].append(link)
    source_order = {}
    for layer in reversed(lineage):
        for item in snapshot.get("source_orders", []):
            if item["context_id"] == layer:
                source_order[item["source_id"]] = item["ordinal"]
    needle = query.strip().casefold()
    selected_nodes = set(node_ids or [])
    if node_id:
        selected_nodes.add(node_id)
    matching_nodes = {
        item["id"] for item in nodes.values()
        if needle and needle in (item["name"] + " " + " ".join(item.get("aliases", []))).casefold()
    }
    dependency_cache: dict[str, str] = {}

    def dependency_status(assertion: dict, visiting: set[str] | None = None) -> str:
        aid = assertion["id"]
        if aid in dependency_cache:
            return dependency_cache[aid]
        visiting = set() if visiting is None else visiting
        if aid in visiting:
            return "invalid_dependency"
        visiting.add(aid)
        status = "valid"
        for parent_id in assertion.get("derived_from", []):
            parent = by_id.get(parent_id)
            if parent is None or parent.get("kind") != "assertion" or parent.get("assessment") == "retracted":
                status = "invalid_dependency"
                break
            parent_status = dependency_status(parent, visiting)
            if parent_status == "invalid_dependency":
                status = "invalid_dependency"
                break
            if (parent_status == "stale_dependency" or
                    snapshot["object_revisions"].get(parent_id, 0) > snapshot["object_revisions"].get(aid, 0)):
                status = "stale_dependency"
        visiting.remove(aid)
        dependency_cache[aid] = status
        return status

    # A later-disclosed order must not turn an uncertain earlier story slice
    # into known information. Invalid/stale inference is not a time oracle.
    order_snapshot = snapshot | {"assertions": [a for a in snapshot.get("assertions", [])
        if a["context_id"] in visible_contexts and dependency_status(a) == "valid"
        and _disclosure_status(a, disclosure_cut, source_order) in {"revealed", "not_evaluated"}]}
    if disclosure_cut is not None:
        disclosed_nodes = {nid for a in order_snapshot["assertions"]
                           for nid in (a["subject_id"], a.get("object_id"), a.get("holder_id")) if nid}
        order_snapshot["nodes"] = [n for n in snapshot.get("nodes", []) if n["id"] in disclosed_nodes]
    time_order = _TimeOrder(order_snapshot, visible_contexts)
    result_assertions, uncertainties = [], []
    # Resolve organization against the complete visible slice before query,
    # identity filters or limits can remove a later revision.
    organization_replaced = set()
    for item in snapshot.get("assertions", []):
        if (item["context_id"] in visible_contexts
                and predicates.get(item["predicate_id"], {}).get("semantic") == "containment"
                and item.get("supersedes_id")
                and _story_status(item, story_cut, time_order) not in {"future", "expired", "unknown"}
                and _disclosure_status(item, disclosure_cut, source_order) not in {"hidden", "unknown"}):
            organization_replaced.add(item["supersedes_id"])
    for assertion in snapshot.get("assertions", []):
        if assertion["context_id"] not in visible_contexts:
            continue
        if predicate_ids and assertion["predicate_id"] not in predicate_ids:
            continue
        if selected_nodes and not ({assertion["subject_id"], assertion.get("object_id"), assertion.get("holder_id")} & selected_nodes):
            continue
        if kinds and predicates.get(assertion["predicate_id"], {}).get("semantic") not in kinds:
            continue
        if needle and not (
            needle in assertion["predicate_id"].casefold()
            or needle in str(assertion.get("value", "")).casefold()
            or assertion["subject_id"] in matching_nodes
            or assertion.get("object_id") in matching_nodes
        ):
            continue
        story_status = _story_status(assertion, story_cut, time_order)
        disclosure_status = _disclosure_status(assertion, disclosure_cut, source_order)
        if story_status in {"future", "expired"} or disclosure_status == "hidden":
            continue
        if story_cut is not None and story_status == "unknown":
            uncertainties.append({"assertion_id": assertion["id"], "reason": "故事有效期无法定位到查询时点"})
            continue
        if disclosure_cut is not None and disclosure_status == "unknown":
            uncertainties.append({"assertion_id": assertion["id"], "reason": "文本披露顺序未知，未作为已知信息展示"})
            continue
        derived_status = dependency_status(assertion)
        view = assertion | {
            "semantic": predicates.get(assertion["predicate_id"], {}).get("semantic", "unknown"),
            "predicate_label": predicates.get(assertion["predicate_id"], {}).get("label", assertion["predicate_id"]),
            "temporal_relation": predicates.get(assertion["predicate_id"], {}).get("temporal_relation"),
            "story_status": story_status,
            "disclosure_status": disclosure_status,
            "derived_status": derived_status,
            "evidence": [
                evidences[link["evidence_id"]] | {"role": link["role"]}
                for link in links[assertion["id"]]
            ],
            "accepted_revision": snapshot["object_revisions"].get(assertion["id"]),
        }
        result_assertions.append(view)
        if view["semantic"] == "containment":
            view["organization_status"] = (
                "superseded" if view["id"] in organization_replaced else
                "active" if view["assessment"] != "retracted" and derived_status == "valid"
                and view["scope"] == "world" and view["polarity"] == "positive"
                and view["modality"] == "actual" and view["origin"] in {"original", "inference"}
                and any(e["role"] == "supports" for e in view["evidence"]) else "unresolved"
            )
        if limit is not None and len(result_assertions) >= limit:
            break
    # A cut is a knowledge/world slice: node labels without a visible assertion
    # would leak future names even when all their claims were correctly hidden.
    visible_node_ids = (
        set() if story_cut is not None or disclosure_cut is not None
        else set(selected_nodes) | matching_nodes
    )
    for assertion in result_assertions:
        visible_node_ids.add(assertion["subject_id"])
        if assertion.get("object_id"):
            visible_node_ids.add(assertion["object_id"])
        if assertion.get("holder_id"):
            visible_node_ids.add(assertion["holder_id"])
    if story_cut is None and disclosure_cut is None and not selected_nodes and not needle:
        visible_node_ids = set(nodes)
    result_nodes = [nodes[key] for key in visible_node_ids if key in nodes]
    if limit is not None:
        result_nodes = result_nodes[:limit]
    edges = [
        {
            "assertion_id": assertion["id"],
            "subject_id": assertion["subject_id"],
            "object_id": assertion["object_id"],
            "predicate_id": assertion["predicate_id"],
            "label": assertion["predicate_label"],
            "semantic": assertion["semantic"],
            "role": assertion.get("qualifiers", {}).get("role"),
            "scope": assertion["scope"],
            "holder_id": assertion.get("holder_id"),
            "origin": assertion["origin"],
            "derived_status": assertion["derived_status"],
            "direct": True,
            **{key: assertion.get(key) for key in ("temporal_relation", "temporal_left", "temporal_right", "evidence", "validity", "disclosure", "polarity", "modality", "assessment", "order_index")},
        }
        for assertion in result_assertions if assertion.get("object_id")
        and assertion.get("organization_status", "active") == "active"
    ]
    return {
        "revision": snapshot["revision"], "context_id": context_id,
        "context_lineage": lineage, "story_cut": story_cut,
        "disclosure_cut": disclosure_cut, "nodes": result_nodes,
        "assertions": result_assertions, "edges": edges,
        "evidences": list({e["id"]: e for a in result_assertions for e in a["evidence"]}.values()),
        "uncertainties": uncertainties,
        "pending_excluded": True,
        "note": "边仅来自已接受断言；人物主张、推断和依赖失效状态保留标记",
    }


def neighborhood(
    snapshot: dict,
    *,
    center_id: str,
    depth: int = 2,
    node_types: list[str] | None = None,
    context_id: str = "canon",
    story_cut: dict | None = None,
    disclosure_cut: dict | None = None,
    limit: int = 2000,
) -> dict:
    """Shortest 1–3 hop paths over visible direct assertions; no inferred edges."""
    if depth not in {1, 2, 3}:
        raise ValueError("邻域深度须为 1–3")
    allowed_types = {"person", "item", "location", "event", "scene", "storyline", "group", "other"}
    if node_types is not None and not set(node_types) <= allowed_types:
        raise ValueError("节点类型筛选无效")
    visible = query_graph(
        snapshot, context_id=context_id, story_cut=story_cut,
        disclosure_cut=disclosure_cut, limit=limit,
    )
    nodes = {item["id"]: item for item in visible["nodes"]}
    status = "visible" if center_id in nodes else (
        "missing" if not any(item["id"] == center_id for item in snapshot["nodes"])
        else "not_visible_or_truncated"
    )
    base = {
        "revision": visible["revision"], "context_id": context_id,
        "story_cut": visible["story_cut"], "disclosure_cut": visible["disclosure_cut"],
        "center_id": center_id, "center_status": status, "depth": depth,
        "node_types": node_types, "nodes": [], "endpoints": [],
        "edges": [], "paths": [], "uncertainties": [],
        "truncated": len(visible["assertions"]) >= limit or len(visible["nodes"]) >= limit,
        "pending_excluded": True,
        "note": "多跳路径是已接受直接断言的遍历结果，不是新事实或直接关系",
    }
    if status != "visible":
        return base
    adjacency: dict[str, list[tuple[str, dict, str]]] = defaultdict(list)
    for edge in visible["edges"]:
        a, b = edge["subject_id"], edge["object_id"]
        if a not in nodes or b not in nodes:
            continue
        adjacency[a].append((b, edge, "forward"))
        adjacency[b].append((a, edge, "reverse"))
    queue = deque([center_id])
    found: dict[str, list[dict]] = {center_id: []}
    while queue:
        current = queue.popleft()
        route = found[current]
        if len(route) >= depth:
            continue
        for target, edge, traversal in adjacency[current]:
            if target in found:
                continue
            found[target] = [*route, {"from_id": current, "to_id": target, "traversal": traversal, "edge": edge}]
            queue.append(target)
    endpoint_ids = [
        node_id for node_id in found if node_id != center_id
        and (node_types is None or nodes[node_id]["node_type"] in node_types)
    ]
    routes = [found[node_id] for node_id in endpoint_ids]
    kept_ids = {center_id} | {
        step["to_id"] for route in routes for step in route
    }
    used_edge_ids = {
        step["edge"]["assertion_id"] for route in routes for step in route
    }
    source_assertions = {item["id"]: item for item in snapshot["assertions"]}
    return base | {
        "nodes": [nodes[node_id] for node_id in found if node_id in kept_ids],
        "endpoints": [nodes[node_id] for node_id in endpoint_ids],
        "edges": [edge for edge in visible["edges"] if edge["assertion_id"] in used_edge_ids],
        "paths": [
            {
                "to_id": endpoint,
                "via_node_ids": [step["to_id"] for step in route[:-1]],
                "assertion_ids": [step["edge"]["assertion_id"] for step in route],
                "steps": route,
                "length": len(route),
                "direct": len(route) == 1,
                "derived": len(route) > 1,
                "contains_claim_or_inference": any(
                    step["edge"]["scope"] != "world"
                    or step["edge"]["origin"] != "original"
                    or step["edge"]["derived_status"] != "valid"
                    for step in route
                ),
            }
            for endpoint, route in zip(endpoint_ids, routes, strict=True)
        ],
        "uncertainties": [
            item for item in visible["uncertainties"]
            if (assertion := source_assertions.get(item["assertion_id"]))
            and ({assertion["subject_id"], assertion.get("object_id")} & kept_ids)
        ],
    }
