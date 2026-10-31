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

GRANULARITIES = ("scene", "event")
LABELS = {"storyline": "大事件", "scene": "场景", "event": "事件"}

# 阅读位置的层级，从粗到细：大事件 → 场景 → 事件。再往下（一个事件里的动作）
# 只是往这张表里插一行——父子关系不是写死的，是看谁把谁列在 `events` 里。
MOMENT_LEVELS = (
    {"key": "storyline", "label": "大事件"},
    {"key": "scene", "label": "场景"},
    {"key": "event", "label": "事件"},
)

# 场景必须挂在大事件下面、事件必须挂在场景下面。数据里偶尔缺这一环（老工程、
# 模型漏填），投影不硬凑一个节点，而是给一个**看得见的桶**：它只用来展开，
# 不能被选成阅读位置，所以不会冒充一条真数据。
ORPHAN_STORYLINE = "~orphan"
ORPHAN_SCENE = "~orphan-scene"
ORPHAN_LABELS = {ORPHAN_STORYLINE: "未归入大事件", ORPHAN_SCENE: "未归入场景"}



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
    """The event whose reading position counts as "read this far".

    An event is its own anchor. A scene is a container, so reading it means
    reading its last event; without children it falls back to itself.
    """
    nodes = _by_id(snapshot)
    if node.get("node_type") == "storyline":
        # 大事件是一段：读到它的位置，就是读到它最后一场的最后一步。
        scenes = [
            nodes[child] for child in node.get("events") or []
            if child in nodes and nodes[child].get("node_type") == "scene"
        ]
        anchors = [anchor_event(snapshot, scene) for scene in scenes]
        anchors = [item for item in anchors if item.get("node_type") == "event"]
        return max(anchors, key=_key) if anchors else node
    if node.get("node_type") != "scene":
        return node
    children = [
        nodes[child] for child in node.get("events") or []
        if child in nodes and nodes[child].get("node_type") == "event"
    ]
    return max(children, key=_key) if children else node


def cut_for(snapshot: dict, step_id: str) -> dict | None:
    """Resolve a step to the position both projections cut at."""
    node = _by_id(snapshot).get(step_id)
    if node is None:
        return None
    anchor = anchor_event(snapshot, node)
    disclosure = anchor.get("disclosure") or {}
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
    picked.sort(key=_key)
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


def _progress_by_scene(snapshot: dict, records: list[dict] | None) -> dict[str, dict]:
    """每个场景对「大事件走到哪了」的那一句话，取它自己最后一条。

    记录挂在场景上（`storyline_progress`），不是挂在大事件上：大事件是容器，
    它的样子由已经读过的场景攒出来，而不是自己写一句结论。
    """
    nodes = _by_id(snapshot)
    best: dict[str, tuple[tuple, dict]] = {}
    for record in records or []:
        if not isinstance(record, dict) or record.get("kind") != "property_value":
            continue
        if str(record.get("property_id") or "").strip() != STORYLINE_PROGRESS:
            continue
        scene = nodes.get(str(record.get("node_id") or ""))
        if scene is None or scene.get("node_type") != "scene":
            continue
        event = nodes.get(str(record.get("event_id") or ""))
        if event is None:
            continue
        key = _key(event)
        if scene["id"] not in best or key > best[scene["id"]][0]:
            best[scene["id"]] = (key, record)
    return {scene_id: record for scene_id, (_key_, record) in best.items()}


def _storyline_name(storyline: dict, nodes: dict[str, dict], cut_key: tuple | None) -> dict:
    """读者此刻看到的大事件叫什么。

    大事件常常到收尾才有正式名，过程里只有一个暂称。读到它最后一步之前，
    显示暂称——否则读者在开头就看到了结局的名字。
    """
    final = storyline.get("name") or storyline["id"]
    provisional = str(storyline.get("provisional_name") or "").strip()
    if not provisional or cut_key is None:
        return {"name": final, "final_name": final, "provisional": False}
    end = _key(anchor_event({"nodes": list(nodes.values())}, storyline))
    if cut_key >= end:
        return {"name": final, "final_name": final, "provisional": False}
    return {"name": provisional, "final_name": final, "provisional": True}


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

    `cut_event_id` 是当前阅读位置：大事件在读完最后一步之前显示暂称，`summary`
    也只取到这一步为止。`records` 是属性取值，场景对「大事件走到哪了」的那句话
    就在里面（`STORYLINE_PROGRESS`）。
    """
    nodes = {
        node["id"]: node
        for node in snapshot.get("nodes", [])
        if isinstance(node.get("id"), str) and not node.get("retracted")
    }
    present = {str(node.get("node_type")) for node in nodes.values()}
    wanted = {level["key"] for level in MOMENT_LEVELS}
    if not (present & wanted):
        return {"levels": [], "nodes": []}
    # 三级是固定的，不因为「这个工程暂时没有大事件」就塌成两级——塌了以后场景
    # 又跑到第一列去，正是这次要修的那个错。
    levels = [dict(level) for level in MOMENT_LEVELS]
    rank = {level["key"]: index for index, level in enumerate(levels)}

    # 谁把谁列在自己的 `events` 里，谁就是上一层。
    parent_of: dict[str, str] = {}
    for node in nodes.values():
        for child in node.get("events") or []:
            if child in nodes:
                parent_of.setdefault(child, node["id"])

    progress = _progress_by_scene(snapshot, records)
    cut_key = _key(nodes[cut_event_id]) if cut_event_id in nodes else None

    def seen(scene_id: str) -> bool:
        if cut_key is None:
            return True
        return _key(anchor_event(snapshot, nodes[scene_id])) <= cut_key

    orders = _orders(snapshot)
    rows: list[tuple[tuple, dict]] = []
    orphans: dict[str, list[dict]] = {ORPHAN_STORYLINE: [], ORPHAN_SCENE: []}
    for node in nodes.values():
        kind = str(node.get("node_type"))
        if kind not in wanted:
            continue
        disclosure = node.get("disclosure")
        disclosure = disclosure if isinstance(disclosure, dict) else {}
        source = disclosure.get("source_id")
        start = disclosure.get("start")
        order = (
            int(orders.get(source, 0) or 0) if isinstance(source, str) else 0,
            start if isinstance(start, int) else -1,
            _key(node),
        )
        parent = parent_of.get(node["id"], "")
        if kind == "scene":
            if parent not in nodes or nodes[parent].get("node_type") != "storyline":
                parent = ORPHAN_STORYLINE
        elif kind == "event":
            if parent not in nodes or nodes[parent].get("node_type") != "scene":
                parent = ORPHAN_SCENE
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
        if kind == "storyline":
            row.update(_storyline_name(node, nodes, cut_key))
            # 大事件此刻的样子：它下面已经读过的场景里，最后那一句话。
            done = [
                child for child in node.get("events") or []
                if child in nodes and nodes[child].get("node_type") == "scene"
                and seen(child)
            ]
            done.sort(key=lambda scene_id: _key(anchor_event(snapshot, nodes[scene_id])))
            for scene_id in reversed(done):
                if progress.get(scene_id):
                    row["summary"] = str(progress[scene_id].get("value") or "")
                    break
        elif kind == "scene":
            row["summary"] = str((progress.get(node["id"]) or {}).get("value") or "")
            if parent == ORPHAN_STORYLINE:
                orphans[ORPHAN_STORYLINE].append(row)
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
    if orphans[ORPHAN_STORYLINE]:
        out.insert(
            len(out),
            {
                "id": ORPHAN_STORYLINE,
                "label_id": "",
                "name": ORPHAN_LABELS[ORPHAN_STORYLINE],
                "level": "storyline",
                "kind_label": LABELS["storyline"],
                "parent_id": "",
                "rank": rank.get("storyline", 0),
                "synthetic": True,
                "summary": "",
            },
        )
    return {"levels": levels, "nodes": out}


def _scene_of(snapshot: dict, event_id: str) -> dict | None:
    """The scene that lists this event as its own child."""
    scenes = [
        node for node in snapshot.get("nodes", [])
        if node.get("node_type") == "scene"
        and not node.get("retracted")
        and event_id in (node.get("events") or [])
    ]
    return min(scenes, key=_key) if scenes else None


def step_for_granularity(snapshot: dict, step_id: str, granularity: str) -> str:
    """The step in another granularity that sits at the same reading position.

    The two views are not two facts: a scene is read when its last event is read,
    so "this scene" and "that scene's last event" are the same position. Going
    back, an event belongs to the scene that lists it; an event in no scene falls
    back to the last scene the reader has already finished, because switching
    granularity must not throw the position away and drop the reader at the end
    of the book.
    """
    if not step_id:
        return ""
    kind = granularity if granularity in GRANULARITIES else "scene"
    nodes = _by_id(snapshot)
    node = nodes.get(step_id)
    if node is None:
        return ""
    if kind == "event":
        if node.get("node_type") == "event":
            return step_id
        anchor = anchor_event(snapshot, node)
        return anchor["id"] if anchor.get("node_type") == "event" else ""
    if node.get("node_type") == "scene":
        return step_id
    scene = _scene_of(snapshot, step_id)
    if scene is not None:
        return scene["id"]
    position = _key(node)
    finished = [
        candidate for candidate in snapshot.get("nodes", [])
        if candidate.get("node_type") == "scene"
        and not candidate.get("retracted")
        and _key(anchor_event(snapshot, candidate)) <= position
    ]
    if not finished:
        return ""
    return max(finished, key=lambda item: _key(anchor_event(snapshot, item)))["id"]


def _members(nodes: dict[str, dict], node: dict) -> list[dict]:
    """What a scene is made of, at any depth."""
    out: list[dict] = []
    for child_id in node.get("events") or []:
        child = nodes.get(child_id)
        if child is None:
            continue
        out.append(child)
        if child.get("node_type") == "scene":
            out.extend(_members(nodes, child))
    return out


def related_nodes(
    snapshot: dict, step_id: str, records: list[dict] | None = None
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
    found = {step_id}
    kind = node.get("node_type")
    if kind == "scene":
        members = _members(nodes, node)
    elif kind == "storyline":
        # 大事件 → 它下面每一场 → 那些场里的事件
        scenes = [
            child for child in node.get("events") or []
            if (nodes.get(child) or {}).get("node_type") == "scene"
        ]
        members = [nodes[child] for child in scenes]
        for scene in list(members):
            members.extend(_members(nodes, scene))
    else:
        members = []
    found.update(member["id"] for member in members)
    # The container is related too: an event belongs to its scene.
    if kind == "event":
        found.update(
            scene["id"] for scene in snapshot.get("nodes", [])
            if scene.get("node_type") == "scene"
            and step_id in (scene.get("events") or [])
        )
    for item in members or [node]:
        found.update(
            participant for participant in item.get("participants") or []
            if participant in nodes
        )
    # 属性指到的人 / 物：谁拿着这把钥匙、这一场里谁和谁是仇敌。
    if records:
        for record in records:
            if record.get("kind") != "property_value":
                continue
            if str(record.get("node_id") or "") not in found:
                continue
            for target in [record.get("value"), *(record.get("related_nodes") or [])]:
                if isinstance(target, str) and target in nodes and target != step_id:
                    found.add(target)
    return sorted(found, key=lambda node_id: _key(nodes[node_id]))


def visible_nodes(snapshot: dict, cut: dict | None) -> list[dict]:
    """Every node the reader has met at this position."""
    nodes = [node for node in snapshot.get("nodes", []) if not node.get("retracted")]
    if cut is None:
        return nodes
    orders = _orders(snapshot)
    return [node for node in nodes if revealed(node, cut, orders)]


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
    order = _reading_key(snapshot)
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

    def emit_scene(scene: dict, depth: int) -> None:
        """场景连同它下面的事件一起排——事件永远跟着自己的场景走。"""
        emit(scene, depth)
        for event_id in scene.get("events") or []:
            event = nodes.get(event_id)
            if event and event.get("node_type") == "event":
                emit(event, depth + 1)

    storylines = sorted(
        (n for n in nodes.values() if n.get("node_type") == "storyline"), key=order
    )
    for story in storylines:
        emit(story, 0)
        for scene_id in story.get("events") or []:
            scene = nodes.get(scene_id)
            if scene and scene.get("node_type") == "scene":
                emit_scene(scene, 1)

    for scene in sorted(
        (n for n in nodes.values()
         if n.get("node_type") == "scene" and n["id"] not in placed),
        key=order,
    ):
        emit_scene(scene, 0)

    for event in sorted(
        (n for n in nodes.values()
         if n.get("node_type") == "event" and n["id"] not in placed),
        key=order,
    ):
        emit(event, 0)
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
        set(related_nodes(snapshot, cut_id, records))
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
        # 阅读位置的三列菜单与资料卡的时间点选择器用**同一份**树：两套结构早晚
        # 会说出两种层级，而层级只有一个。
        "moments": moment_tree(snapshot),
        # Pickers that need an event (补录) get the full list whatever the
        # granularity is — the reading position is not the only use of events.
        "events": steps(snapshot, "event"),
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
