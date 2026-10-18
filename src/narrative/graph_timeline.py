"""Evidence-preserving timeline projection; organization never implies chronology."""
from __future__ import annotations

from collections import defaultdict

from .graph_hierarchy import hierarchy
from .graph_query import query_graph


def timeline(snapshot: dict, *, candidate_id: str | None = None, context_id: str = "canon",
             story_cut: dict | None = None, disclosure_cut: dict | None = None,
             limit: int = 2000, _event_ids: set[str] | None = None,
             _tree: dict | None = None) -> dict:
    if not 1 <= limit <= 2000:
        raise ValueError("时间轴节点数量须在 1–2000 之间")
    view = query_graph(snapshot, context_id=context_id, story_cut=story_cut,
                       disclosure_cut=disclosure_cut, limit=None)
    tree = _tree if _tree is not None else hierarchy(snapshot, candidate_id=candidate_id, context_id=context_id,
                     story_cut=story_cut, disclosure_cut=disclosure_cut, limit=2000)
    structure = {n["id"]: n for n in tree["nodes"]}
    all_events = [n for n in view["nodes"] if n["node_type"] == "event"]
    if _event_ids is not None:
        all_events = [n for n in all_events if n["id"] in _event_ids]
    all_events.sort(key=lambda n: n["id"])
    events = []
    for node in all_events[:limit]:
        extent = node.get("occurrence")
        if not extent:
            status = "unknown"
        elif extent["kind"] == "point":
            status = "known"
        else:
            states = [extent[k]["state"] for k in ("start", "end")]
            status = "known" if all(s == "known" for s in states) else ("partial" if "known" in states else "unknown")
        refs = [] if not extent else ([extent["point"]] if extent["kind"] == "point" else
                    [extent[k]["ref"] for k in ("start", "end") if extent[k]["state"] == "known"])
        coordinate_refs = [ref for ref in refs if ref["form"] in {"number", "datetime"}]
        if not coordinate_refs:
            status = "unknown"
        elif len(coordinate_refs) < (1 if extent["kind"] == "point" else 2) or len({(r["timeline_id"], r["form"]) for r in coordinate_refs}) > 1:
            status = "partial"
        events.append(node | {"coordinate_refs": coordinate_refs,"title": node["name"], "time_status": status,
                              "parent_id": structure.get(node["id"], {}).get("parent_id"),
                              "depth": structure.get(node["id"], {}).get("depth", 0)})
    ids = {n["id"] for n in events}
    relations = []
    uncertainties = list(view["uncertainties"])
    for a in view["assertions"]:
        if (a["semantic"] != "temporal"
                or a["subject_id"] not in ids or a.get("object_id") not in ids):
            continue
        if (a["scope"] != "world" or a["polarity"] != "positive" or a["modality"] != "actual"
                or a["assessment"] == "retracted" or a["derived_status"] != "valid"):
            uncertainties.append({"assertion_id": a["id"], "reason": "认知、否定或假设时序不作为确定时间关系", "assertion": a})
            continue
        if any(a.get(key, {}).get("form") == "event_boundary" and a[key]["event_id"] != a[node_key]
               for key, node_key in (("temporal_left", "subject_id"), ("temporal_right", "object_id"))):
            uncertainties.append({"assertion_id": a["id"], "reason": "时序边界引用与主宾事件不一致，未连为事件时序", "assertion": a})
            continue
        relation = a["temporal_relation"]
        reverse = relation == "after"
        relations.append(a | {"assertion_id": a["id"], "label": a["predicate_label"], "direct": True,
                              "display_relation": "before" if reverse else relation,
                              "display_left": a.get("temporal_right" if reverse else "temporal_left"),
                              "display_right": a.get("temporal_left" if reverse else "temporal_right"),
                              "display_subject_id": a["object_id"] if reverse else a["subject_id"],
                              "display_object_id": a["subject_id"] if reverse else a["object_id"]})
    timeline_labels = {t["id"]: t["label"] for t in snapshot.get("timelines", [])
                       if t.get("context_id", "canon") in view["context_lineage"]}
    nested_axes = _nested_axes(events, relations, structure, timeline_labels,
                               hierarchy_truncated=tree["truncated"])
    projection = _axis_projection(events, relations, timeline_labels)
    uncertainties.extend(projection["uncertainties"])
    return {**projection, "nested_axes": nested_axes, "revision": view["revision"], "candidate_id": candidate_id,
            "context_id": context_id, "story_cut": view["story_cut"], "disclosure_cut": disclosure_cut,
            "pending_excluded": candidate_id is None, "events": events, "nodes": events,
            "relations": relations, "temporal_edges": relations,
            "organization_edges": tree["edges"], "uncertainties": uncertainties,
            "truncated": len(all_events) > limit,
            "message": "时间轴保留显式时点、区间和偏序；组织顺序不代表发生先后，未知边界不推定为死亡或结束。"}


def _axis_projection(events: list[dict], relations: list[dict], timeline_labels: dict) -> dict:
    """Project only this layer's direct temporal evidence, without ancestor rollups."""
    relative_ids = {a[k] for a in relations for k in ("subject_id", "object_id")}
    known = [e for e in events if e["time_status"] != "unknown"]
    relative = [e for e in events if e["time_status"] == "unknown" and e["id"] in relative_ids]
    unknown = [e for e in events if e["time_status"] == "unknown" and e["id"] not in relative_ids]
    axes = sorted({(r["timeline_id"], r["form"]) for e in known for r in e["coordinate_refs"]})
    lanes = [{"timeline_id": tid, "label": timeline_labels.get(tid, "故事时间"), "form": form,
              "events": [e for e in known if any(r["timeline_id"] == tid and r["form"] == form
                                                for r in e["coordinate_refs"])]}
             for tid, form in axes]
    relative_axes, uncertainties = _relative_axes(relations, {e["id"]: e for e in events})
    return {"events": events, "event_ids": [e["id"] for e in events], "lanes": lanes,
            "known_time": known, "relative": relative, "unknown": unknown,
            "relative_axes": relative_axes, "relations": relations, "uncertainties": uncertainties}


def _nested_axes(events: list[dict], relations: list[dict], structure: dict[str, dict],
                 timeline_labels: dict, *, hierarchy_truncated: bool) -> dict:
    """Nearest event ancestors define expansion; containers contribute no time facts."""
    by_id = {event["id"]: event for event in events}
    parent_events: dict[str, str | None] = {}
    unplaced: set[str] = set()
    for event in events:
        current = structure.get(event["id"])
        seen = {event["id"]}
        nearest = None
        if current is None:
            unplaced.add(event["id"])
        while current and current.get("parent_id"):
            parent_id = current["parent_id"]
            if parent_id in seen or parent_id not in structure:
                unplaced.add(event["id"])
                break
            seen.add(parent_id)
            current = structure[parent_id]
            if current["node_type"] == "event":
                if nearest is None:
                    nearest = parent_id
                if parent_id not in by_id:
                    unplaced.add(event["id"])
            elif current["node_type"] not in {"scene", "storyline"}:
                break
        parent_events[event["id"]] = nearest

    children: dict[str | None, list[dict]] = defaultdict(list)
    for event in events:
        parent_id = parent_events[event["id"]]
        event.update({"parent_event_id": parent_id, "axis_id": None, "child_axis_id": None,
                      "child_event_ids": []})
        if event["id"] not in unplaced:
            children[parent_id].append(event)
    for event in events:
        if event["id"] in unplaced:
            continue
        parent_id = parent_events[event["id"]]
        event["axis_id"] = "root" if parent_id is None else f"event:{parent_id}"
        child_ids = [child["id"] for child in children[event["id"]]]
        event["child_event_ids"] = child_ids
        event["child_axis_id"] = f"event:{event['id']}" if child_ids else None

    layers = []
    for owner_id in [None, *(event["id"] for event in events if children[event["id"]])]:
        layer_events = children[owner_id]
        ids = {event["id"] for event in layer_events}
        local_relations = [relation for relation in relations
                           if relation["subject_id"] in ids and relation.get("object_id") in ids]
        cross_relations = [relation for relation in relations
                           if (relation["subject_id"] in ids) != (relation.get("object_id") in ids)]
        layers.append({"id": "root" if owner_id is None else f"event:{owner_id}",
                       "owner_event_id": owner_id,
                       "parent_axis_id": None if owner_id is None else by_id[owner_id]["axis_id"],
                       "cross_level_relations": cross_relations,
                       "cross_level_relation_count": len(cross_relations),
                       **_axis_projection(layer_events, local_relations, timeline_labels)})
    return {"root_axis_id": "root", "layers": layers, "unplaced_event_ids": sorted(unplaced),
            "truncated": hierarchy_truncated or bool(unplaced),
            "note": "层级仅决定展开；容器不成为时间节点，跨层时序不合成为父事件关系。"}


def _relative_axes(relations: list[dict], events: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Layout accepted boundary constraints, never inventing event facts/durations."""
    refs: dict[tuple, dict] = {}
    links: list[tuple[tuple, tuple, str, str | None]] = []

    def key(ref: dict) -> tuple:
        return (ref["timeline_id"], ref["form"], ref.get("event_id"), ref.get("boundary"),
                ref.get("number"), ref.get("datetime"))

    for relation in relations:
        kind = relation["temporal_relation"]
        left, right = relation.get("temporal_left"), relation.get("temporal_right")
        if kind not in {"before", "after", "same"} or not left or not right:
            continue
        if (left["timeline_id"] != right["timeline_id"]
                or left["form"] != "event_boundary" or right["form"] != "event_boundary"):
            continue
        a, b = key(left), key(right)
        refs[a], refs[b] = left, right
        if kind == "after":
            a, b = b, a
        links.append((a, b, "same" if kind == "same" else "before", relation["id"]))
    # A start cannot be later than its own end. This is a weak layout constraint:
    # no edge/fact is added to the accepted graph and no positive duration is assumed.
    for ref_key, ref in list(refs.items()):
        if ref.get("form") != "event_boundary" or ref.get("boundary") != "start":
            continue
        end = ref | {"boundary": "end"}
        end_key = key(end)
        if end_key in refs:
            links.append((ref_key, end_key, "layout", None))
    adjacency: dict[tuple, set[tuple]] = defaultdict(set)
    for a, b, _, _ in links:
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen: set[tuple] = set()
    axes, uncertainties = [], []
    for seed in sorted(refs, key=repr):
        if seed in seen:
            continue
        component: set[tuple] = set()
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            if current in component:
                continue
            component.add(current)
            frontier.extend(adjacency[current] - component)
        seen.update(component)
        group_links = [link for link in links if link[0] in component]
        parents = {k: k for k in component}

        def find(k: tuple, parent_map: dict = parents) -> tuple:
            while parent_map[k] != k:
                k = parent_map[k]
            return k

        for a, b, kind, _ in group_links:
            if kind == "same":
                parents[find(b)] = find(a)
        outgoing: dict[tuple, dict[tuple, int]] = defaultdict(dict)
        indegree = dict.fromkeys((find(k) for k in component), 0)
        conflict = False
        for a, b, kind, _ in group_links:
            if kind == "same":
                continue
            a, b = find(a), find(b)
            if a == b:
                conflict |= kind == "before"
                continue
            weight = 0 if kind == "layout" else 1
            if b not in outgoing[a]:
                indegree[b] += 1
            outgoing[a][b] = max(weight, outgoing[a].get(b, 0))
        ready = sorted((k for k, degree in indegree.items() if degree == 0), key=repr)
        ranks = dict.fromkeys(indegree, 0)
        visited = 0
        while ready:
            current = ready.pop(0)
            visited += 1
            for target, weight in outgoing[current].items():
                ranks[target] = max(ranks[target], ranks[current] + weight)
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        ids = sorted({aid for _, _, _, aid in group_links if aid})
        if conflict or visited != len(indegree):
            uncertainties.append({"assertion_ids": ids, "reason": "相对边界约束存在环或冲突，未强排相对数轴"})
            continue
        points = []
        for ref_key in component:
            ref = refs[ref_key]
            event_id = ref.get("event_id")
            points.append({"ref": ref, "event_id": event_id,
                           "event_name": events.get(event_id or "", {}).get("name", "时间引用"),
                           "boundary": ref.get("boundary"), "ordinal": ranks[find(ref_key)],
                           "same_group": repr(find(ref_key))})
        points.sort(key=lambda point: (point["ordinal"], repr(point["ref"])))
        axes.append({"id": f"relative-{len(axes) + 1}", "title": f"相对顺序 {len(axes) + 1}",
                     "timeline_id": seed[0], "points": points, "explicit_relation_ids": ids,
                     "connections": [{"from_ref": refs[a], "to_ref": refs[b], "relation": kind,
                                      "relation_id": aid, "direct": True}
                                     for a, b, kind, aid in group_links if kind != "layout"],
                     "layout_connections": [{"from_ref": refs[a], "to_ref": refs[b], "layout_only": True,
                                               "meaning": "同事件开始不晚于结束；不代表已审关系或正时长"}
                                              for a, b, kind, _ in group_links if kind == "layout"],
                     "ordinal_is_duration": False,
                     "note": "数字用于排列；先后关系以连线为准，不代表时长，同位不表示同时。虚线仅为事件边界布局，不新增事实。"})
    axes.sort(key=lambda axis: (-len(axis["points"]), -len(axis["explicit_relation_ids"]),
                                axis["timeline_id"], axis["id"]))
    for index, axis in enumerate(axes, 1):
        axis["id"] = f"relative-{index}"
        axis["title"] = f"相对顺序组 {index}（组号不表示先后）"
    return axes, uncertainties
