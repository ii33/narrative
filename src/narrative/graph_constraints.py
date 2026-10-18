"""Boundary constraints: weak interval order plus reviewed strict assertions."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime


def temporal_conflicts(groups: dict, object_revisions: dict | None = None) -> list[dict]:
    strict: dict[tuple, list[tuple[tuple, tuple]]] = defaultdict(list)
    equal: dict[tuple, list[tuple[tuple, tuple]]] = defaultdict(list)

    def ref_key(ref: dict) -> tuple:
        return (ref["timeline_id"], ref["form"], ref.get("number"), ref.get("datetime"),
                ref.get("event_id"), ref.get("boundary"))

    revisions = object_revisions or {}
    validity: dict[str, bool] = {}

    def valid_dependency(item: dict, visiting: set[str] | None = None) -> bool:
        if item["id"] in validity:
            return validity[item["id"]]
        visiting = set() if visiting is None else visiting
        if item["id"] in visiting:
            return False
        visiting.add(item["id"])
        result = item.get("assessment") != "retracted"
        for parent_id in item.get("derived_from", []):
            parent = groups["assertion"].get(parent_id)
            if (parent is None or not valid_dependency(parent, visiting)
                    or revisions.get(parent_id, 0) > revisions.get(item["id"], 0)):
                result = False
        visiting.remove(item["id"])
        validity[item["id"]] = result
        return result

    for item in groups["assertion"].values():
        if (not valid_dependency(item) or item.get("polarity") != "positive"
                or item.get("modality") != "actual"):
            continue
        predicate = groups["predicate"].get(item["predicate_id"], {})
        relation = predicate.get("temporal_relation")
        left, right = item.get("temporal_left"), item.get("temporal_right")
        if relation not in {"before", "after", "same"} or not left or not right:
            continue
        if left["timeline_id"] != right["timeline_id"]:
            continue
        a, b = ref_key(left), ref_key(right)
        if relation == "after":
            a, b = b, a
        scope = (item["context_id"], item["scope"], item.get("holder_id"))
        (equal if relation == "same" else strict)[scope].append((a, b))

    issues = []
    for scope in set(strict) | set(equal):
        pairs = list(equal[scope])
        ordered = list(strict[scope])
        refs = {ref for pair in pairs + ordered for ref in pair}
        # An event's own start <= end is weak, even when its duration is unknown.
        # Never collapse all boundaries of an event to an event-level vertex.
        weak = []
        for ref in list(refs):
            if ref[1] == "event_boundary":
                start, end = (*ref[:5], "start"), (*ref[:5], "end")
                weak.append((start, end))
                refs.update((start, end))
        # Only event occurrence supplies endpoint coordinates; containment and
        # assertion validity do not transfer times to parent or child events.
        for node in groups["node"].values():
            extent = node.get("occurrence")
            if scope[1] != "world" or node.get("context_id") != scope[0] or not extent:
                continue
            for boundary in ("start", "end"):
                coordinate = (extent["point"] if extent["kind"] == "point" else
                              extent[boundary].get("ref") if extent[boundary]["state"] == "known" else None)
                if coordinate is None:
                    continue
                event_ref = ref_key({"timeline_id": coordinate["timeline_id"],
                    "form": "event_boundary", "event_id": node["id"], "boundary": boundary})
                pair = (event_ref, ref_key(coordinate))
                pairs.append(pair)
                refs.update(pair)
        representatives: dict[tuple, tuple] = {}

        def root(node: tuple, representatives: dict[tuple, tuple] = representatives) -> tuple:
            representatives.setdefault(node, node)
            path = []
            while representatives[node] != node:
                path.append(node)
                node = representatives[node]
            for member in path:
                representatives[member] = node
            return node

        for a, b in pairs:
            representatives[root(a)] = root(b)
        coordinates: dict[tuple, list[tuple[float, tuple]]] = defaultdict(list)
        for ref in refs:
            value = None
            if ref[1] == "number":
                value = float(ref[2])
            elif ref[1] == "datetime":
                try:
                    stamp = datetime.fromisoformat(ref[3])
                    # Keep naive and timezone-aware axes distinct.
                    value = stamp.timestamp() if stamp.tzinfo else (stamp - datetime(1970, 1, 1)).total_seconds()
                    coordinates[(ref[0], ref[1], stamp.tzinfo is not None)].append((value, ref))
                except (TypeError, ValueError, OverflowError):
                    pass
                continue
            if value is not None:
                coordinates[(ref[0], ref[1])].append((value, ref))
        for coordinate_axis in coordinates.values():
            coordinate_axis.sort(key=lambda pair: pair[0])
            for (a_value, a), (b_value, b) in zip(coordinate_axis, coordinate_axis[1:], strict=False):
                if a_value == b_value:
                    representatives[root(a)] = root(b)
                else:
                    ordered.append((a, b))
        adjacency: dict[tuple, set[tuple]] = defaultdict(set)
        for a, b in ordered + weak:
            adjacency[root(a)].add(root(b))

        def reaches(start: tuple, end: tuple, adjacency: dict[tuple, set[tuple]] = adjacency) -> bool:
            queue, seen = [start], set()
            while queue:
                node = queue.pop()
                if node == end:
                    return True
                if node not in seen:
                    seen.add(node)
                    queue.extend(adjacency.get(node, ()))
            return False

        if any(reaches(root(b), root(a)) for a, b in ordered):
            issues.append({"code": "TIME_CYCLE", "severity": "error", "object_id": None,
                           "message": "严格边界先后与同时、事件起止或已知坐标矛盾"})
    return issues
