"""Resolve declared containment by typed, context-local identity, never proximity."""


def resolve_parent(reference: str, kind: str, context: str, snapshot: dict,
                   declared: list[dict], mention_ids: dict, issues: list[dict], owner: str) -> str | None:
    if not reference:
        return None
    current = {node["id"]: node for node in snapshot["nodes"]}
    if reference in current:
        candidates = [current[reference]]
    else:
        local = [row for row in declared if row['name'] == reference and row['type'] == kind]
        local_ids = {mention_ids.get((row['type'], row['name'], row['quotes'][0])) for row in local}
        local_ids.discard(None)
        candidates = [current.get(identifier) or {'id': identifier, 'node_type': kind, 'context_id': context}
                      for identifier in local_ids]
    valid = [node for node in candidates if node.get("node_type") == kind
             and node.get("context_id", "canon") == context and not node.get("retracted")]
    if len(valid) != 1:
        issues.append({"name": owner, "reference": reference, "parent_type": kind,
                       "candidate_ids": [node["id"] for node in candidates],
                       "reason": "声明的父级引用缺失、歧义、已撤回或类型/语境不匹配；请查证并使用明确父级ID"})
        return None
    return valid[0]["id"]


def event_scene_issues(objects: list[dict]) -> list[dict]:
    """Every active event belongs to an active same-context scene, transitively."""
    nodes = {obj["id"]: obj for obj in objects if obj.get("kind") == "node" and not obj.get("retracted")}
    issues = []
    colors = {}
    for root in nodes:
        stack = [(root, False)]
        while stack:
            key, leaving = stack.pop()
            if leaving:
                colors[key] = 2
                continue
            color = colors.get(key, 0)
            if color == 1:
                issues.append({"severity": "error", "code": "CONTAINMENT_CYCLE", "object_id": key,
                               "message": f"events包含关系形成循环：{key}"})
                stack.clear()
                break
            if color == 2:
                continue
            colors[key] = 1
            stack.append((key, True))
            stack.extend((child, False) for child in nodes.get(key, {}).get("events") or [])
        if issues:
            break
    parents = {}
    for parent in nodes.values():
        for child_id in parent.get("events") or []:
            if child_id in nodes:
                parents.setdefault(child_id, []).append(parent)
    for node in nodes.values():
        if node.get("node_type") != "action":
            continue
        containers = parents.get(node["id"], [])
        if (len(containers) != 1 or containers[0].get("node_type") != "event"
                or containers[0].get("context_id", "canon") != node.get("context_id", "canon")):
            issues.append({"severity": "error", "code": "ACTION_EVENT", "object_id": node["id"],
                           "message": "动作必须直属恰好一个同语境活动事件，不能直接归入场景或大事件"})
        if node.get("events"):
            issues.append({"severity": "error", "code": "ACTION_LEAF", "object_id": node["id"],
                           "message": "动作是最小包含层级，不能包含子项"})
    reached = set()
    for scene in nodes.values():
        if scene.get("node_type") != "scene":
            continue
        seen = {scene["id"]}
        stack = list(scene.get("events") or [])
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            child = nodes.get(key)
            if child is None or child.get("context_id", "canon") != scene.get("context_id", "canon"):
                continue
            if child.get("node_type") not in {"event", "scene"}:
                continue
            if child["node_type"] == "event":
                reached.add(key)
            stack.extend(child.get("events") or [])
    return issues + [{"severity": "error", "code": "EVENT_SCENE", "object_id": node["id"],
             "message": f"事件 {node['id']}（{node['name']}）必须属于同语境的活动场景；可同批创建单事件场景或补挂已有场景"}
            for node in nodes.values() if node.get("node_type") == "event" and node["id"] not in reached]


def storyline_issues(objects: list[dict], snapshot: dict) -> list[dict]:
    from .storyline_ranges import analyze
    graph = snapshot | {'objects': objects,
                        'nodes': [row for row in objects if row.get('kind') == 'node'],
                        'source_orders': [row for row in objects if row.get('kind') == 'source_order']}
    return analyze(graph)[1]
