"""What the browsing page shows, decided outside the GUI.

The page asks one question — "which nodes are visible at this reading position,
in this granularity, filtered this way" — and renders the answer. Step lists,
the related-node rule, counting and filtering all live here, so the QML holds no
opinion about the data model and cannot drift from it.

Two things are deliberately separate:

* the **step** the reader picked (a scene or an event) — that is what the list
  offers and what 相关节点 is computed from;
* the **anchor event** the step resolves to — that is the reading position both
  the node list and the property pane cut at. A scene is read when its last
  event is read, so a scene step anchors on its last child event.
"""

from __future__ import annotations

from collections import Counter

from .graph_query import revealed
from .order_ids import label_id, order_key
from .property_labels import STORYLINE_PROGRESS

GRANULARITIES = ("scene", "event", "action")
LABELS = {"storyline": "主要故事", "big_event": "大事件", "scene": "场景", "event": "事件", "action": "动作"}

# 阅读位置的层级，从粗到细：大事件 → 场景 → 事件。再往下（一个事件里的动作）
# 只是往这张表里插一行——父子关系不是写死的，是看谁把谁列在 `events` 里。
MOMENT_LEVELS = (
    {"key": "storyline", "label": "主要故事"},
    {"key": "big_event", "label": "大事件"},
    {"key": "scene", "label": "场景"},
    {"key": "event", "label": "事件"},
    {"key": "action", "label": "动作"},
)

# 场景必须挂在大事件下面、事件必须挂在场景下面。数据里偶尔缺这一环（老工程、
# 模型漏填），投影不硬凑一个节点，而是给一个**看得见的桶**：它只用来展开，
# 不能被选成阅读位置，所以不会冒充一条真数据。
ORPHAN_STORYLINE = "~orphan"
ORPHAN_BIG_EVENT = "~orphan-big-event"
ORPHAN_SCENE = "~orphan-scene"
ORPHAN_LABELS = {ORPHAN_STORYLINE: "未归入主要故事", ORPHAN_BIG_EVENT: "未归入大事件", ORPHAN_SCENE: "未归入场景"}



def _key(node: dict) -> tuple:
    """同一类型内的先后：编号就是发号顺序，所以按编号比。

    跨类型不能用它——`P3` 和 `I3` 之间没有先后，两套编号互不相关。
    """
    return order_key(node["id"]) or (999,)


def _reading_key(snapshot: dict):
    """跨类型的先后只能看披露位置：第几章、这一章第几个字。"""
    orders = _orders(snapshot)

    def key(node: dict) -> tuple:
        disclosure = node.get("disclosure")
        disclosure = disclosure if isinstance(disclosure, dict) else {}
        source = disclosure.get("source_id")
        start = disclosure.get("start")
        # 同一个字读到的几个节点没有先后可言，用编号给个稳定顺序就够——
        # 这一层不承担「谁先谁后」的意思。
        return (
            orders.get(source, 0) if isinstance(source, str) else 0,
            start if isinstance(start, int) else -1,
            order_key(node.get("id") or "") or (9999,),
            node.get("id") or "",
        )

    return key


def _by_id(snapshot: dict) -> dict[str, dict]:
    return {node["id"]: node for node in snapshot.get("nodes", []) if isinstance(node.get("id"), str)}


def _orders(snapshot: dict) -> dict[str, int]:
    return {
        row["source_id"]: row["ordinal"]
        for row in snapshot.get("source_orders", []) or []
        if isinstance(row, dict) and "source_id" in row and "ordinal" in row
    }


def anchor_event(snapshot: dict, node: dict) -> dict:
    """Resolve a container to its last active leaf by source position."""
    nodes = _by_id(snapshot)
    descendants = _members(nodes, node)
    leaves = [child for child in descendants if child.get("node_type") in {"event", "action"}
              and not any((nodes.get(identifier) or {}).get("node_type") in {"event", "action"}
                          for identifier in child.get("events") or [])]
    return max(leaves, key=_reading_key(snapshot)) if leaves else node


def cut_for(snapshot: dict, step_id: str) -> dict | None:
    """Resolve a step to the position both projections cut at."""
    node = _by_id(snapshot).get(step_id)
    if node is None:
        return None
    anchor = anchor_event(snapshot, node)
    from .temporal_state import boundary_at
    if node.get("node_type") == "storyline" and node.get("completion"):
        completion = node["completion"]
        disclosure = {"source_id": completion["source_id"], "start": completion["start"] - 1}
    elif anchor["id"] == node["id"] and node.get("node_type") in {"storyline", "big_event", "scene"}:
        disclosure = node.get("disclosure") or {}
    else:
        disclosure = boundary_at(snapshot, anchor["id"]) or {}
    start = disclosure.get("start")
    source_id = disclosure.get("source_id")
    if not isinstance(start, int) or not isinstance(source_id, str):
        return None
    return {
        "step_id": step_id,
        "step_name": node.get("name") or step_id,
        "step_kind": node.get("node_type"),
        "event_id": anchor["id"],
        "event_name": anchor.get("name") or anchor["id"],
        "source_id": source_id,
        "start": start,
    }


def steps(snapshot: dict, granularity: str = "scene") -> list[dict]:
    """Reading positions offered for one granularity, in reading order."""
    kind = granularity if granularity in GRANULARITIES else "scene"
    picked = [
        node for node in snapshot.get("nodes", [])
        if node.get("node_type") == kind and not node.get("retracted")
    ]
    picked.sort(key=_reading_key(snapshot))
    return [
        {
            "id": node["id"],
            "label_id": label_id(node["id"], kind),
            "name": node.get("name") or node["id"],
            "kind": kind,
            "label": LABELS[kind],
            "start": (node.get("disclosure") or {}).get("start"),
        }
        for node in picked
    ]


def _progress_by_member(snapshot: dict, records: list[dict] | None) -> dict[str, dict]:
    """主要故事进展记在大事件上，大事件进展记在场景上。"""
    nodes = _by_id(snapshot)
    best: dict[str, tuple[tuple, dict]] = {}
    from .temporal_state import projected_changes
    for record in projected_changes(snapshot, records or []):
        if record.get("state_kind") != "known":
            continue
        scene = nodes.get(str(record.get("node_id") or ""))
        expected = {"big_event": STORYLINE_PROGRESS, "scene": "big_event_progress"}.get(str((scene or {}).get("node_type") or ""))
        if scene is None or not expected or record.get("property_id") != expected:
            continue
        event = nodes.get(str(record.get("event_id") or ""))
        if event is None:
            continue
        key = _reading_key(snapshot)(event)
        if scene["id"] not in best or key > best[scene["id"]][0]:
            best[scene["id"]] = (key, record)
    return {scene_id: record for scene_id, (_key_, record) in best.items()}


def moment_tree(
    snapshot: dict,
    cut_event_id: str = "",
    records: list[dict] | None = None,
) -> dict:
    """按粒度分级的阅读位置：一列一层，粗的在前。

    返回 `{"levels": [{key, label}], "nodes": [{id, label_id, name, level,
    parent_id, rank, kind_label, summary, synthetic}]}`。

    层级是**硬性**的：第一列只有大事件，场景一律在大事件下面，事件一律在场景
    下面。没有归属的场景进「未归入大事件」这个桶，而不是混进第一列冒充大事件
    ——混在一起时，读者没法判断自己在选哪一级。

    `cut_event_id` 仅过滤可见成员；大事件名称保持原名，start/end 始终依据完整快照计算。
    `summary` 只取到当前阅读位置。`records` 是属性取值，场景对「大事件走到哪了」的那句话
    就在里面（`STORYLINE_PROGRESS`）。
    """
    from .storyline_ranges import analyze, reading_starts
    starts = reading_starts(snapshot)
    ranges = {row['id']: row for row in analyze(snapshot)[0]}
    nodes = {
        node["id"]: node
        for node in snapshot.get("nodes", [])
        if isinstance(node.get("id"), str) and not node.get("retracted")
    }
    if cut_event_id and cut_event_id in nodes:
        cut = cut_for(snapshot, cut_event_id)
        if cut:
            cut_orders = _orders(snapshot)
            visible = {key for key, node in nodes.items() if revealed(node, cut, cut_orders)}
            for identifier, start in starts.items():
                if nodes[identifier].get('node_type') == 'storyline':
                    start = nodes[identifier].get('disclosure')
                    if not start and nodes[identifier].get('completion'):
                        completion = nodes[identifier]['completion']
                        start = {'source_id': completion['source_id'], 'start': completion['start'] - 1}
                    if not start:
                        visible.discard(identifier)
                        continue
                else:
                    start = ranges.get(identifier, {}).get('start', start)
                left = cut_orders.get(start['source_id'])
                right = cut_orders.get(cut['source_id'])
                if left is not None and right is not None and (left, start['start']) > (right, cut['start']):
                    visible.discard(identifier)
                elif left is not None and right is not None:
                    visible.add(identifier)
            nodes = {key: node for key, node in nodes.items() if key in visible}
    present = {str(node.get("node_type")) for node in nodes.values()}
    wanted = {level["key"] for level in MOMENT_LEVELS}
    if not (present & wanted):
        return {"levels": [], "nodes": []}
    # 层级由MOMENT_LEVELS统一定义，不因某层暂时没有节点而变换含义。
    levels = [dict(level) for level in MOMENT_LEVELS]
    rank = {level["key"]: index for index, level in enumerate(levels)}

    # 谁把谁列在自己的 `events` 里，谁就是上一层。
    parent_of: dict[str, str] = {}
    for node in nodes.values():
        for child in node.get("events") or []:
            if child in nodes:
                parent_of.setdefault(child, node["id"])

    progress = _progress_by_member(snapshot, records)
    cut_key = _reading_key(snapshot)(nodes[cut_event_id]) if cut_event_id in nodes else None

    def seen(scene_id: str) -> bool:
        if cut_key is None:
            return True
        return _reading_key(snapshot)(anchor_event(snapshot, nodes[scene_id])) <= cut_key

    orders = _orders(snapshot)
    rows: list[tuple[tuple, dict]] = []
    orphans: dict[str, list[dict]] = {ORPHAN_STORYLINE: [], ORPHAN_BIG_EVENT: [], ORPHAN_SCENE: []}
    for node in nodes.values():
        kind = str(node.get("node_type"))
        if kind not in wanted:
            continue
        disclosure = ranges.get(node["id"], {}).get("start") or starts.get(node["id"]) or node.get("disclosure")
        disclosure = disclosure if isinstance(disclosure, dict) else {}
        source = disclosure.get("source_id")
        start = disclosure.get("start")
        order = (
            int(orders.get(source, 0) or 0) if isinstance(source, str) else 0,
            start if isinstance(start, int) else -1,
            _key(node),
        )
        parent = parent_of.get(node["id"], "")
        expected_parent = levels[rank[kind] - 1]["key"] if rank[kind] else ""
        if expected_parent:
            ancestor = next((row for row in _ancestors(snapshot, node["id"])
                             if row.get("node_type") == expected_parent and row["id"] in nodes), None)
            parent = ancestor["id"] if ancestor else {"storyline": ORPHAN_STORYLINE, "big_event": ORPHAN_BIG_EVENT,
                                                    "scene": ORPHAN_SCENE}.get(expected_parent, "")
        else:
            parent = ""
        row = {
            "id": node["id"],
            "label_id": label_id(node["id"], kind),
            "name": node.get("name") or node["id"],
            "level": kind,
            "kind_label": LABELS.get(kind, ""),
            "parent_id": parent,
            "rank": rank.get(kind, 0),
            "synthetic": False,
            "summary": "",
        }
        if kind in {"storyline", "big_event"}:
            if kind == "big_event" and parent == ORPHAN_STORYLINE:
                orphans[ORPHAN_STORYLINE].append(row)
            row.update(start=ranges.get(node["id"], {}).get("start"),
                       end=ranges.get(node["id"], {}).get("end"))
            # 大事件此刻的样子：它下面已经读过的场景里，最后那一句话。
            done = [
                child for child in node.get("events") or []
                if child in nodes and nodes[child].get("node_type") == ("big_event" if kind == "storyline" else "scene")
                and seen(child)
            ]
            done.sort(key=lambda scene_id: _reading_key(snapshot)(anchor_event(snapshot, nodes[scene_id])))
            for scene_id in reversed(done):
                if progress.get(scene_id):
                    row["summary"] = str(progress[scene_id].get("value") or "")
                    break
        elif kind == "scene":
            row["summary"] = str((progress.get(node["id"]) or {}).get("value") or "")
            if parent == ORPHAN_BIG_EVENT:
                orphans[ORPHAN_BIG_EVENT].append(row)
        elif kind == "event" and parent == ORPHAN_SCENE:
            orphans[ORPHAN_SCENE].append(row)
        rows.append((order, row))

    rows.sort(key=lambda item: item[0])
    out = [row for _order, row in rows]

    # 桶排在它那一层的末尾：它是「剩下的」，不该抢在真数据前面。
    if orphans[ORPHAN_SCENE]:
        out.insert(
            len(out),
            {
                "id": ORPHAN_SCENE,
                "label_id": "",
                "name": ORPHAN_LABELS[ORPHAN_SCENE],
                "level": "scene",
                "kind_label": LABELS["scene"],
                "parent_id": ORPHAN_STORYLINE,
                "rank": rank.get("scene", 1),
                "synthetic": True,
                "summary": "",
            },
        )
    for bucket, level, parent in [(ORPHAN_BIG_EVENT, "big_event", ORPHAN_STORYLINE),
                                   (ORPHAN_STORYLINE, "storyline", "")]:
        if orphans[bucket] or bucket == ORPHAN_STORYLINE and orphans[ORPHAN_BIG_EVENT]:
            out.append({"id": bucket, "label_id": "", "name": ORPHAN_LABELS[bucket],
                        "level": level, "kind_label": LABELS[level], "parent_id": parent,
                        "rank": rank[level], "synthetic": True, "summary": ""})

    return {"levels": levels, "nodes": out}


def _ancestors(snapshot: dict, identifier: str) -> list[dict]:
    nodes = _by_id(snapshot)
    result, seen, pending = [], {identifier}, [identifier]
    while pending:
        child = pending.pop(0)
        for parent in nodes.values():
            if not parent.get("retracted") and parent["id"] not in seen and child in (parent.get("events") or []):
                seen.add(parent["id"])
                result.append(parent)
                pending.append(parent["id"])
    return result


def _scene_of(snapshot: dict, event_id: str) -> dict | None:
    return next((node for node in _ancestors(snapshot, event_id) if node.get("node_type") == "scene"), None)


def step_for_granularity(snapshot: dict, step_id: str, granularity: str) -> str:
    kind = granularity if granularity in GRANULARITIES else "scene"
    nodes = _by_id(snapshot)
    node = nodes.get(step_id)
    if not node:
        return ""
    if node.get("node_type") == kind:
        return step_id
    parent = next((row for row in _ancestors(snapshot, step_id) if row.get("node_type") == kind), None)
    if parent:
        return parent["id"]
    children = [row for row in _members(nodes, node) if row.get("node_type") == kind]
    if children:
        return max(children, key=lambda row: _reading_key(snapshot)(anchor_event(snapshot, row)))["id"]
    position = _reading_key(snapshot)(anchor_event(snapshot, node))
    finished = [row for row in nodes.values() if row.get("node_type") == kind
                and not row.get("retracted") and _reading_key(snapshot)(anchor_event(snapshot, row)) <= position]
    return max(finished, key=lambda row: _reading_key(snapshot)(anchor_event(snapshot, row)))["id"] if finished else ""


def _members(nodes: dict[str, dict], node: dict) -> list[dict]:
    result, seen, pending = [], {node["id"]}, list(node.get("events") or [])
    while pending:
        identifier = pending.pop(0)
        child = nodes.get(identifier)
        if not child or identifier in seen or child.get("retracted"):
            continue
        seen.add(identifier)
        result.append(child)
        pending.extend(child.get("events") or [])
    return result


def related_nodes(
    snapshot: dict, step_id: str, records: list[dict] | None = None, *, cut: dict | None = None
) -> list[str]:
    """Nodes the step is about: itself, what it is made of, and who takes part.

    A scene has no participants of its own, so it takes the union over the events
    it contains — and those events belong to it, so they are related too: reading
    a scene means reading its events. An event is related to the scene that holds
    it, for the same reason, so the two granularities agree about the same step.

    A 大事件 is a span, not a point: it is related to every scene it holds and to
    those scenes' events. Without that, selecting one showed a page with nothing
    in it, which read as "this arc mentions nobody".

    Property records widen it further — the holder of an item, the person a
    relation points at. Those are what the reader means by 相关. One step only:
    a relation's target does not drag in its own relations.
    """
    nodes = _by_id(snapshot)
    node = nodes.get(step_id)
    if node is None:
        return []
    boundary = cut if cut is not None else cut_for(snapshot, step_id)
    def visible(item):
        return (not item.get("retracted") and item.get("context_id", "canon") == node.get("context_id", "canon")
                and (boundary is None or revealed(item, boundary, _orders(snapshot))))
    members = [item for item in _members(nodes, node) if visible(item)]
    own_members = [item for item in [node, *members] if visible(item)]
    found = {item["id"] for item in own_members}
    found.update(parent["id"] for parent in _ancestors(snapshot, step_id) if visible(parent))
    for item in own_members:
        found.update(participant for participant in item.get("participants") or [] if participant in nodes)
    from .node_involvement import involvement
    aggregated = involvement(snapshot, records or [], step_id, boundary)
    found.update(row["id"] for rows in aggregated.values() for row in rows)
    # 属性从哪里往外扩：这一步本身（场景/事件），加上参与其中的**物和地点**——
    # 「这一场里手镯在谁手上」正是相关的事。
    # 但**人物**的档案不扩：「身份」「司机」是他自己的资料，顺着它走会把全书的人都
    # 拉进来，选一个大事件却列出没出场的人，就是这么来的。
    own = {item["id"] for item in own_members}
    structural = {
        identifier for identifier in own | {ref for item in own_members for ref in item.get("participants") or []}
        if (nodes.get(identifier) or {}).get("node_type") != "person"
    }
    from .property_core import canonical_definitions
    from .temporal_state import projected_changes
    definitions = canonical_definitions(records or [])
    if records:
        for record in projected_changes(snapshot, records, boundary):
            if record.get("state_kind") != "known" or record.get("node_id") not in structural:
                continue
            if record.get("scope", "world") != "world":
                continue
            targets = list(record.get("related_nodes") or [])
            if definitions.get(str(record.get("property_id") or ""), {}).get("value_type") in {"node", "either"}:
                value = record.get("value")
                targets.extend(value if isinstance(value, list) else [value])
            for target in targets:
                if isinstance(target, str) and target in nodes and target != step_id:
                    found.add(target)
    found = {identifier for identifier in found if not nodes[identifier].get("retracted")
             and nodes[identifier].get("context_id", "canon") == node.get("context_id", "canon")
             and (boundary is None or revealed(nodes[identifier], boundary, _orders(snapshot)))}
    return sorted(found, key=lambda node_id: _key(nodes[node_id]))


def visible_nodes(snapshot: dict, cut: dict | None) -> list[dict]:
    """Every node the reader has met at this position."""
    nodes = [node for node in snapshot.get("nodes", []) if not node.get("retracted")]
    if cut is None:
        return nodes
    orders = _orders(snapshot)
    return [node for node in nodes if revealed(
        node if node.get("disclosure") or node.get("node_type") != "storyline" or not node.get("completion")
        else node | {"disclosure": {"source_id": node["completion"]["source_id"],
                                   "start": node["completion"]["start"] - 1}}, cut, orders)]


def matches(node: dict, needle: str) -> bool:
    if not needle:
        return True
    text = needle.casefold()
    if text in str(node.get("name") or "").casefold():
        return True
    return any(text in str(alias).casefold() for alias in node.get("aliases") or [])


def timeline_tree(snapshot: dict) -> list[dict]:
    """按层级排开的时间点：大事件 → 场景 → 事件，各自带缩进深度。

    层级从数据里读：场景挂在它所属的大事件下，事件挂在它所属的场景下；没有归属的
    自己占一层（散场在顶层，散事件也在顶层），不硬凑。
    """
    nodes = {
        node["id"]: node
        for node in snapshot.get("nodes", [])
        if not node.get("retracted")
    }
    from .storyline_ranges import analyze, reading_starts
    starts = reading_starts(snapshot)
    starts.update({row["id"]: row["start"] for row in analyze(snapshot)[0]})
    orders = _orders(snapshot)
    def order(node):
        point = starts.get(node["id"]) or {}
        return (orders.get(str(point.get("source_id") or ""), -1), point.get("start", -1), _key(node))
    rows: list[dict] = []
    placed: set[str] = set()

    def emit(node: dict, depth: int) -> None:
        rows.append({
            "id": node["id"],
            "name": node.get("name", node["id"]),
            "label_id": label_id(node["id"], node.get("node_type")),
            "node_type": node.get("node_type"),
            "depth": depth,
        })
        placed.add(node["id"])

    def emit_subtree(node: dict, depth: int) -> None:
        if node["id"] in placed:
            return
        emit(node, depth)
        children = [nodes[identifier] for identifier in node.get("events") or []
                    if identifier in nodes and nodes[identifier].get("node_type") in LABELS]
        for child in sorted(children, key=order):
            emit_subtree(child, depth + 1)

    contained = {identifier for node in nodes.values() for identifier in node.get("events") or []}
    roots = [node for node in nodes.values() if node.get("node_type") in LABELS and node["id"] not in contained]
    for root in sorted(roots, key=order):
        emit_subtree(root, 0)
    for node in sorted(nodes.values(), key=order):
        if node.get("node_type") in LABELS:
            emit_subtree(node, 0)
    return rows


def browse_view(
    snapshot: dict,
    *,
    granularity: str = "scene",
    cut_id: str = "",
    node_type: str = "",
    query: str = "",
    related_only: bool = False,
    time_filter: bool = False,
    records: list[dict] | None = None,
) -> dict:
    """One answer for the browsing page: nodes, counts, steps and the cut.

    ``time_filter`` decides whether the *list* obeys the reading position. The
    cut is resolved and returned either way, because the property pane always
    obeys it.

    Counts describe everything the other conditions leave — the reading position,
    相关节点, the search — but never the type filter itself: the sidebar *is* that
    filter, and a count that only ever shows the selected type would be useless.
    """
    cut = cut_for(snapshot, cut_id) if cut_id else None
    nodes = visible_nodes(snapshot, cut if (cut is not None and time_filter) else None)
    related = (
        set(related_nodes(snapshot, cut_id, records, cut=cut))
        if (related_only and cut_id) else None
    )
    needle = query.strip()
    candidates = [
        node for node in nodes
        if (related is None or node["id"] in related) and matches(node, needle)
    ]
    counts = Counter(node.get("node_type") or "other" for node in candidates)
    shown = [
        node for node in candidates
        if not node_type or (node.get("node_type") or "other") == node_type
    ]
    # 列表里什么类型都有，所以按披露位置排；编号只在自己那类里有意义。
    shown.sort(key=_reading_key(snapshot))
    # 编号带类型前缀（E1.2 / P7），一眼看出同类里的先后；存的主键不变。
    shown = [
        dict(node, label_id=label_id(node.get("id") or "", node.get("node_type")))
        for node in shown
    ]
    return {
        "granularity": granularity if granularity in GRANULARITIES else "scene",
        "revision": snapshot.get("revision"),
        "steps": steps(snapshot, granularity),
        # 时间点的层级：大事件 → 场景 → 事件。菜单按这个缩进展开，点哪一级就停在哪一级。
        "timeline": timeline_tree(snapshot),
        # 阅读位置菜单与资料卡的时间点选择器用**同一份**树：两套结构早晚
        # 会说出两种层级，而层级只有一个。
        "moments": moment_tree(snapshot),
        # Pickers that need an event (补录) get the full list whatever the
        # granularity is — the reading position is not the only use of events.
        "events": steps(snapshot, "event"),
        "anchor_steps": sorted(steps(snapshot, "event") + steps(snapshot, "action"),
                               key=lambda row: _reading_key(snapshot)(_by_id(snapshot)[row["id"]])),
        "cut": cut,
        # The same position in both granularities, so switching the step list
        # keeps the reader where they were instead of jumping to the last page.
        "cut_alt": {
            kind: step_for_granularity(snapshot, cut_id, kind)
            for kind in GRANULARITIES
        },
        "nodes": shown,
        "counts": {key: counts.get(key, 0) for key in sorted(counts)},
        "total": len(shown),
        # 工程里到底有没有节点，用来区分"空工程"和"筛没了"。
        "project_total": len([
            node for node in snapshot.get("nodes", []) if not node.get("retracted")
        ]),
        "related_only": bool(related_only),
        "related": sorted(related) if related is not None else [],
        "time_filter": bool(time_filter),
    }
