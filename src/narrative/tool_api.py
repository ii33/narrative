"""One surface for programs and models: read the graph, and edit it.

Every capability here is a plain function over a project file — no Qt, no window,
no GUI state. The CLI and the MCP server are two doors onto this same list, so an
LLM calling a tool and a person typing a command cannot disagree about what
"the state of this scene" means.

Reading is cheap and side-effect free. Writing follows the same rules as the
interface: records are never rewritten, an edit is a new record, and a value that
is replaced stops counting from the disclosure that replaced it.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .project_catalog import list_projects, open_graph_project


class ToolError(ValueError):
    """A call the tool cannot honour; the message is meant for the caller."""


@dataclass(frozen=True)
class Tool:
    name: str
    summary: str
    params: dict[str, dict]
    handler: Callable[[Any, dict], dict]
    writes: bool = False
    required: tuple[str, ...] = field(default=())

    def schema(self) -> dict:
        properties = {
            key: {k: v for k, v in spec.items() if k != "required"}
            for key, spec in self.params.items()
        }
        required = [key for key, spec in self.params.items() if spec.get("required")]
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }


def _labelled(node: dict) -> dict:
    """节点 + 它的显示编号（`P3`/`E1.2`）。界面显示什么，工具就返回什么。"""
    from .order_ids import label_id

    if not isinstance(node, dict) or not node.get("id"):
        return node
    return node | {"label_id": label_id(str(node["id"]), node.get("node_type"))}


def _need(params: dict, *keys: str) -> None:
    missing = [key for key in keys if params.get(key) in (None, "")]
    if missing:
        raise ToolError("缺少参数：" + "、".join(missing))


def _cut(params: dict) -> str | None:
    cut = params.get("cut")
    return str(cut) if cut not in (None, "") else None


def _node_types(snapshot: dict) -> Counter:
    return Counter(
        node.get("node_type") or "other" for node in snapshot.get("nodes", [])
    )


def _properties(ops, node_id: str, cut: str | None) -> list[dict]:
    """The node's properties as the reader knows them at this position."""
    summary = ops.property_node_summary(node_id, cut=cut)
    return [
        {
            "property_id": item["property_id"],
            "label": item["label"],
            "category": item["category"],
            "value": item["value"],
            "value_node": item.get("value_node"),
            "scope": item["scope"],
            "holder": item.get("holder_name"),
            "disclosed_at": item.get("disclosed_at"),
            "records": [
                {
                    "id": row["id"],
                    "value": row["value"],
                    "event_id": row["event_id"],
                    "event_name": row.get("event_name"),
                }
                for row in item["records"]
            ],
        }
        for item in summary["properties"]
    ]


# -- reading ---------------------------------------------------------------


def _list_projects(_ops, params: dict) -> dict:
    directory = params.get("dir") or "data/graph-projects"
    return {"directory": str(Path(directory).resolve()), "projects": list_projects(directory)}


def _project_info(ops, _params: dict) -> dict:
    info = ops.graph_project_info()
    snapshot = ops.graph_snapshot()
    return {
        "project": info,
        "nodes": dict(_node_types(snapshot)),
        "sources": len(ops.sources()),
        "pending_candidates": len(ops.change_candidates("pending")),
    }


def _list_nodes(ops, params: dict) -> dict:
    view = ops.browse_view(
        granularity=params.get("granularity") or "scene",
        cut_id=str(params.get("cut") or ""),
        node_type=params.get("node_type") or "",
        query=params.get("query") or "",
        related_only=bool(params.get("related_only")),
        time_filter=True,
    )
    limit = int(params.get("limit") or 200)
    return {
        "cut": view.get("cut"),
        "counts": view.get("counts"),
        "total": view.get("total"),
        "project_total": view.get("project_total"),
        "nodes": view.get("nodes", [])[:limit],
    }


def _get_node(ops, params: dict) -> dict:
    _need(params, "node_id")
    node_id = str(params["node_id"])
    cut = _cut(params)
    snapshot = ops.graph_snapshot()
    node = next((item for item in snapshot["nodes"] if item["id"] == node_id), None)
    if node is None:
        raise ToolError(f"节点不存在：{node_id}")
    return {
        "node": _labelled(node),
        "cut": cut,
        "properties": _properties(ops, node_id, cut),
    }


def _node_properties(ops, params: dict) -> dict:
    _need(params, "node_id")
    cut = _cut(params)
    return {
        "node_id": str(params["node_id"]),
        "cut": cut,
        "properties": _properties(ops, str(params["node_id"]), cut),
    }


def _related_ids(ops, step_id: str) -> list[str]:
    from .browse_view import related_nodes

    return sorted(related_nodes(ops.graph_snapshot(), step_id))


def _scene_events(snapshot: dict, scene: dict, seen: set[str] | None = None) -> list[str]:
    """A scene's events, deepest last — the reading order inside one scene."""
    from .order_ids import order_key

    nodes = {node["id"]: node for node in snapshot["nodes"]}
    seen = seen or set()
    out: list[str] = []
    for child_id in scene.get("events") or []:
        if child_id in seen:
            continue
        seen.add(child_id)
        child = nodes.get(child_id)
        if child is None:
            continue
        if child.get("node_type") == "event":
            out.append(child_id)
        out.extend(_scene_events(snapshot, child, seen))
    return sorted(dict.fromkeys(out), key=lambda item: order_key(item) or ())


def _state_of(ops, ids: list[str], cut: str, limit: int = 20) -> list[dict]:
    snapshot = ops.graph_snapshot()
    by_id = {node["id"]: node for node in snapshot["nodes"]}
    out: list[dict] = []
    for node_id in ids:
        node = by_id.get(node_id)
        if node is None or node.get("node_type") in {"event", "scene", "storyline"}:
            continue
        properties = _properties(ops, node_id, cut)
        if not properties:
            continue
        out.append({
            "id": node_id,
            "label_id": _labelled(node).get("label_id"),
            "name": node.get("name"),
            "node_type": node.get("node_type"),
            "properties": properties,
        })
        if len(out) >= limit:
            break
    return out


def _list_moments(ops, _params: dict) -> dict:
    """阅读位置的层级：一列一层（场景 → 事件），给 cut 挑合法值。"""
    from .browse_view import moment_tree

    return moment_tree(ops.graph_snapshot())


def _material(ops):
    from .node_material import MaterialStore

    return MaterialStore(ops.path)


def _material_view(ops, params: dict) -> dict:
    _need(params, "node_id")
    return _material(ops).view(str(params["node_id"]), cut=_cut(params) or "")


def _add_material_card(ops, params: dict) -> dict:
    _need(params, "node_id", "title")
    store = _material(ops)
    card = store.add_card(
        str(params["node_id"]),
        str(params["title"]),
        str(params.get("detail") or ""),
        str(params.get("valid_from") or ""),
        str(params.get("valid_to") or ""),
    )
    return {"status": "accepted", "card": card} | _material_view(ops, params)


def _update_material_card(ops, params: dict) -> dict:
    _need(params, "node_id", "card_id")
    store = _material(ops)
    card = store.update_card(
        str(params["node_id"]),
        str(params["card_id"]),
        title=None if params.get("title") is None else str(params["title"]),
        detail=None if params.get("detail") is None else str(params["detail"]),
        valid_from=None if params.get("valid_from") is None else str(params["valid_from"]),
        valid_to=None if params.get("valid_to") is None else str(params["valid_to"]),
    )
    return {"status": "accepted", "card": card} | _material_view(ops, params)


def _delete_material_card(ops, params: dict) -> dict:
    _need(params, "node_id", "card_id")
    store = _material(ops)
    result = store.remove_card(str(params["node_id"]), str(params["card_id"]))
    return {"status": "accepted", **result} | _material_view(ops, params)


def _add_material_image(ops, params: dict) -> dict:
    _need(params, "node_id", "card_id", "path")
    store = _material(ops)
    record = store.add_image(
        str(params["node_id"]),
        str(params["card_id"]),
        Path(str(params["path"])),
        str(params.get("caption") or ""),
    )
    return {"status": "accepted", "image": record} | _material_view(ops, params)


def _delete_material_image(ops, params: dict) -> dict:
    _need(params, "node_id", "card_id", "image_id")
    store = _material(ops)
    result = store.remove_image(
        str(params["node_id"]), str(params["card_id"]), str(params["image_id"])
    )
    return {"status": "accepted", **result} | _material_view(ops, params)


def _scene_state(ops, params: dict) -> dict:
    _need(params, "scene_id")
    scene_id = str(params["scene_id"])
    snapshot = ops.graph_snapshot()
    scene = next((item for item in snapshot["nodes"] if item["id"] == scene_id), None)
    if scene is None:
        raise ToolError(f"场景不存在：{scene_id}")
    view = ops.browse_view(granularity="scene", cut_id=scene_id, time_filter=True)
    # browse_view 的 cut 是一整条步骤描述；对外只给事件编号，别的工具都按它取状态。
    # 没有章节标记的小工程算不出投影切点，就退到「它最后一个成员事件」。
    step = view.get("cut") or {}
    cut = step.get("event_id") if isinstance(step, dict) else step
    if not cut:
        members = _scene_events(snapshot, scene)
        cut = members[-1] if members else scene_id
    cut = str(cut)
    return {
        "scene": _labelled(scene),
        "cut": cut,
        "step": step,
        "nodes": view.get("nodes", []),
        "state": _state_of(ops, _related_ids(ops, scene_id), cut),
    }


def _event_state(ops, params: dict) -> dict:
    _need(params, "event_id")
    event_id = str(params["event_id"])
    snapshot = ops.graph_snapshot()
    event = next((item for item in snapshot["nodes"] if item["id"] == event_id), None)
    if event is None:
        raise ToolError(f"事件不存在：{event_id}")
    return {
        "event": _labelled(event),
        "cut": event_id,
        "related": _related_ids(ops, event_id),
        "state": _state_of(ops, _related_ids(ops, event_id), event_id),
    }


def _statistics(ops, _params: dict) -> dict:
    snapshot = ops.graph_snapshot()
    records = ops.property_records(include_retracted=True)
    kinds = Counter(record.get("kind") for record in records)
    definitions = {record["id"] for record in records if record.get("kind") == "property_definition"}
    values = [record for record in records if record.get("kind") == "property_value"]
    audit = ops.property_audit()
    info = ops.graph_project_info()
    return {
        "revision": info["revision"],
        # 编号从 v7 起按类型各一套：读到别的版本就知道编号怎么读。
        "schema_version": info.get("schema_version"),
        "nodes": dict(_node_types(snapshot)),
        "node_total": len(snapshot.get("nodes", [])),
        "sources": [
            {"id": source["id"], "title": source.get("title"), "chars": source.get("length")}
            for source in ops.sources()
        ],
        "properties": {
            "definitions": len(definitions),
            "value_records": len(values),
            "retractions": kinds.get("property_retraction", 0),
            "records_by_kind": dict(kinds),
        },
        "audit": audit.get("counts", {}),
    }


def _search(ops, params: dict) -> dict:
    _need(params, "query")
    view = ops.graph_query(query=str(params["query"]), limit=int(params.get("limit") or 50))
    if isinstance(view, dict) and isinstance(view.get("nodes"), list):
        view = view | {"nodes": [_labelled(node) for node in view["nodes"]]}
    return {"query": params["query"], "result": view}


def _import_source(ops, params: dict) -> dict:
    _need(params, "title", "text")
    source_id = ops.import_source(str(params["title"]), str(params["text"]))
    return {"status": "accepted", "source_id": source_id}


def _property_categories(ops, _params: dict) -> dict:
    return {"vocabulary": ops.property_categories()}


def _list_sources(ops, _params: dict) -> dict:
    return {"sources": [
        {"id": source["id"], "title": source.get("title"), "chars": source.get("length")}
        for source in ops.sources()
    ]}


def _read_source(ops, params: dict) -> dict:
    _need(params, "source_id")
    source = ops.source(str(params["source_id"]))
    text = source.get("text", "")
    start = max(0, int(params.get("start") or 0))
    length = max(1, int(params.get("length") or 2000))
    return {
        "id": source["id"],
        "title": source.get("title"),
        "chars": len(text),
        "start": start,
        "text": text[start:start + length],
    }


def _audit(ops, params: dict) -> dict:
    node_ids = params.get("node_ids")
    return ops.property_audit(set(node_ids) if node_ids else None)


def _list_candidates(ops, params: dict) -> dict:
    status = params.get("status") or "pending"
    return {
        "status": status,
        "changes": ops.change_candidates(status),
        "properties": ops.property_candidates(status),
    }


# -- writing ---------------------------------------------------------------


def _revision(ops) -> int:
    return int(ops.graph_project_info()["revision"])


def _add_property(ops, params: dict) -> dict:
    _need(params, "node_id", "property_id", "value", "event_id")
    snapshot = ops.graph_snapshot()
    if not any(node["id"] == params["node_id"] for node in snapshot["nodes"]):
        raise ToolError(f"节点不存在：{params['node_id']}")
    if not any(node["id"] == params["event_id"] for node in snapshot["nodes"]):
        raise ToolError(f"事件不存在：{params['event_id']}")

    records = ops.property_records(include_retracted=True)
    known = {record["id"] for record in records if record.get("kind") == "property_definition"}
    batch: list[dict] = []
    if params["property_id"] not in known:
        batch.append({
            "kind": "property_definition",
            "id": params["property_id"],
            "value_type": "any",
            "cardinality": "one",
        })
    record: dict[str, Any] = {
        "kind": "property_value",
        "id": f"value_{os.urandom(6).hex()}",
        "node_id": params["node_id"],
        "property_id": params["property_id"],
        "value": params["value"],
        "event_id": params["event_id"],
        "source": params.get("source") or "narration",
        "scope": params.get("scope") or "world",
    }
    for key in ("category", "holder_id"):
        if params.get(key):
            record[key] = params[key]
    quote = (params.get("quote") or "").strip()
    source_id = params.get("source_id")
    if quote and source_id:
        record["evidence"] = {"source_id": source_id, "quote": quote}
        hits = ops.locate(source_id, quote)
        record["disclosure"] = {"source_id": source_id, "start": hits[0]["start"] if hits else 0}
    else:
        # 没有引文就没有原文位置：放在原文末尾，让它在同一个事件里算「最后说的」，
        # 而不是排到该事件已有记录的前面去。
        fallback = source_id or next(
            (source["id"] for source in ops.sources()), None
        )
        if fallback:
            record["disclosure"] = {
                "source_id": fallback, "start": len(ops.source(fallback)["text"]),
            }
    batch.append(record)

    revision = _revision(ops)
    diagnostics = ops.property_precheck(batch, revision)
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    if errors:
        return {"status": "rejected", "diagnostics": diagnostics}
    candidate = ops.property_propose(batch, revision, source="manual", note="工具补录")
    after = ops.property_accept(candidate, 1)
    return {"status": "accepted", "revision": after, "record_id": record["id"]}


def _correct_property(ops, params: dict) -> dict:
    _need(params, "record_id", "value")
    records = ops.property_records(include_retracted=True)
    target = next((item for item in records if item.get("id") == params["record_id"]), None)
    if target is None or target.get("kind") != "property_value":
        raise ToolError(f"取值记录不存在：{params['record_id']}")

    fixed = dict(target)
    fixed["id"] = f"value_{os.urandom(6).hex()}"
    fixed["value"] = params["value"]
    if params.get("event_id"):
        fixed["event_id"] = params["event_id"]
    quote = (params.get("quote") or "").strip()
    if quote and params.get("source_id"):
        fixed["evidence"] = {"source_id": params["source_id"], "quote": quote}

    batch = [
        # 更正就是两半：追加对的，撤回错的。记录不原地改。
        {
            "kind": "property_retraction",
            "id": f"retract_{os.urandom(6).hex()}",
            "target_id": target["id"],
            "reason": params.get("reason") or "更正：以新记录为准",
        },
        fixed,
    ]
    revision = _revision(ops)
    diagnostics = ops.property_precheck(batch, revision)
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    if errors:
        return {"status": "rejected", "diagnostics": diagnostics}
    candidate = ops.property_propose(batch, revision, source="correction", note="工具更正")
    after = ops.property_accept(candidate, 1)
    return {"status": "accepted", "revision": after, "record_id": fixed["id"]}


def _retract(ops, params: dict) -> dict:
    _need(params, "ids", "reason")
    ids = params["ids"] if isinstance(params["ids"], list) else [params["ids"]]
    result = ops.property_retract(
        [str(item) for item in ids], str(params["reason"]),
        event_id=str(params["event_id"]) if params.get("event_id") else None,
    )
    return {"status": "accepted", **result}


def _revise_node(ops, params: dict) -> dict:
    _need(params, "node_id")
    allowed = ("name", "node_type", "aliases", "identity_note")
    changes = {key: params[key] for key in allowed if params.get(key) not in (None, "")}
    if not changes:
        raise ToolError("没有可应用的修改（name / node_type / aliases / identity_note）")
    snapshot = ops.graph_snapshot()
    node = next((item for item in snapshot["nodes"] if item["id"] == params["node_id"]), None)
    if node is None:
        raise ToolError(f"节点不存在：{params['node_id']}")
    updated = dict(node) | changes
    revision = _revision(ops)
    diagnostics = ops.change_precheck([updated], [], revision)
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    if errors:
        return {"status": "rejected", "diagnostics": diagnostics}
    candidate = ops.change_propose(
        [updated], [], revision, source="correction", note=f"工具修订：{node.get('name')}"
    )
    after = ops.change_accept(candidate, 1)
    return {"status": "accepted", "revision": after, "node_id": params["node_id"], "changes": changes}


def _relation_map(ops, params: dict) -> dict:
    """人物关系网：人是点，关系是有方向的边。

    一条关系就是一条属性记录（属性名是关系性质，值指向对方），所以两个人之间
    可以有两支箭头——「A 父亲 B」和「B 儿子 A」——也可以只有一支（「A 崇拜 B」）。
    关系性质带括号时（「恋人（冷战中）」），括号里的是状态，单独给出来。
    """
    from .browse_view import anchor_event, cut_for, moment_tree, related_nodes
    from .order_ids import order_key

    snapshot = ops.graph_snapshot()
    nodes = {item["id"]: item for item in snapshot["nodes"]}
    events = {item["id"]: item.get("name", item["id"]) for item in snapshot["nodes"]}
    # 阅读位置：停在哪一步，就只画那一步之前**读者已经知道**的关系。
    # 编号是按阅读顺序发的，所以比编号就是比阅读位置。
    cut = str(params.get("cut") or "")
    anchor = cut_for(snapshot, cut) if cut else None
    if anchor is None and cut and cut in nodes:
        # 节点还没有披露位置（手写的库）时，退到它锚定的事件——比编号就够了。
        step = anchor_event(snapshot, nodes[cut])
        anchor = {"event_id": step["id"], "step_name": nodes[cut].get("name") or cut}
    cut_key = order_key(anchor["event_id"]) if anchor else None

    def past_cut(event_id: str) -> bool:
        """这条记录是在切点之后才披露的吗（编号按阅读顺序发，比编号就行）。"""
        here = order_key(event_id)
        if cut_key is None or here is None:
            return False
        return here > cut_key
    persons = [
        {"id": item["id"], "name": item.get("name", item["id"]),
         "label_id": item.get("label_id", item["id"])}
        for item in snapshot["nodes"] if item.get("node_type") == "person"
    ]
    edges: list[dict] = []
    for record in ops.property_records():
        if record.get("kind") != "property_value":
            continue
        left, right = str(record.get("node_id") or ""), str(record.get("value") or "")
        if left == right:
            continue  # 自己指向自己不是关系（审核会报 self_reference）
        if nodes.get(left, {}).get("node_type") != "person":
            continue
        if nodes.get(right, {}).get("node_type") != "person":
            continue
        if past_cut(str(record.get("event_id") or "")):
            continue  # 还没读到这一步
        raw = str(record.get("property_id") or "")
        kind, state = raw, ""
        for mark in ("（", "("):
            if mark in raw:
                head, _, tail = raw.partition(mark)
                kind, state = head.strip(), tail.rstrip("）)").strip()
                break
        edges.append({
            "record_id": record.get("id"),
            "from": left, "from_name": nodes[left].get("name", left),
            "to": right, "to_name": nodes[right].get("name", right),
            "kind": kind, "state": state,
            "event_id": record.get("event_id"),
            "event_name": events.get(record.get("event_id") or "", ""),
        })
    edges.sort(key=lambda item: (item["from_name"], item["kind"], item["to_name"]))

    # 只看相关：只留跟这一步有关的人（这一步出场的人、以及关系指向他们的人）。
    related_only = bool(params.get("related_only"))
    related_ids: set[str] = set()
    if related_only and cut:
        related_ids = {
            item for item in related_nodes(snapshot, cut, ops.property_records())
            if nodes.get(item, {}).get("node_type") == "person"
        }
        # 只要一头相关就画：读者想看的是「这一步里的人跟谁有关系」，
        # 两头都要求相关会把这一步里的人的关系全剪掉。
        edges = [e for e in edges if e["from"] in related_ids or e["to"] in related_ids]

    # 没写阅读位置、也没开筛选时，全书的人都画（孤点说明「书里没写他和谁有关」）；
    # 一旦按位置或相关收窄，就只留连线两端——筛选后还摆一屏孤点，等于没筛。
    if cut_key is not None or related_only:
        linked = {e["from"] for e in edges} | {e["to"] for e in edges}
        persons = [item for item in persons if item["id"] in linked]
    return {
        "revision": snapshot["revision"],
        "persons": persons,
        "edges": edges,
        "cut": cut,
        "cut_name": (anchor or {}).get("step_name", ""),
        # 时间点选择器用的那棵树（与数据浏览同一份）
        "moments": moment_tree(snapshot),
    }


def _attach_scenes(ops, params: dict) -> dict:
    """把若干场景挂到一个大事件下面（立刻生效）。

    大事件常常在后面才成形：先写了几章散场，某天回头发现「这些其实是一件事」。
    这时不用重提——把场景挂上去就行。场景本来属于哪个大事件会被替换掉（一个场景
    只属于一个大事件），大事件原有的其它场景保留。
    """
    _need(params, "storyline_id")
    raw = params.get("scene_ids")
    scene_ids = [str(item) for item in (raw if isinstance(raw, list) else [raw]) if item]
    if not scene_ids:
        raise ToolError("请提供要挂上的场景编号（scene_ids）")
    snapshot = ops.graph_snapshot()
    nodes = {item["id"]: item for item in snapshot["nodes"]}
    story = nodes.get(str(params["storyline_id"]))
    if story is None:
        raise ToolError(f"节点不存在：{params['storyline_id']}")
    if story.get("node_type") != "storyline":
        raise ToolError(f"{params['storyline_id']} 不是大事件（storyline）")
    wrong = [item for item in scene_ids if nodes.get(item, {}).get("node_type") != "scene"]
    if wrong:
        raise ToolError(f"这些不是场景：{'、'.join(wrong)}")

    existing = [item for item in (story.get("events") or []) if isinstance(item, str)]
    merged = existing + [item for item in scene_ids if item not in existing]
    if params.get("replace"):
        merged = list(dict.fromkeys(scene_ids))
    updated = dict(story) | {"events": merged}
    revision = _revision(ops)
    diagnostics = ops.change_precheck([updated], [], revision)
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    if errors:
        return {"status": "rejected", "diagnostics": diagnostics}
    candidate = ops.change_propose(
        [updated], [], revision, source="correction",
        note=f"归入大事件：{story.get('name')}",
    )
    after = ops.change_accept(candidate, 1)
    return {
        "status": "accepted", "revision": after,
        "storyline_id": story["id"], "events": merged,
    }


def _merge(ops, params: dict) -> dict:
    _need(params, "alias_id", "target_id")
    return {"status": "accepted", **ops.property_merge(params["alias_id"], params["target_id"])}


def _rename(ops, params: dict) -> dict:
    _need(params, "to_id")
    names = params.get("names") or []
    if isinstance(names, str):
        names = [names]
    if not names:
        raise ToolError("请提供要改名的属性名（names）")
    return {"status": "accepted", **ops.property_rename([str(item) for item in names], params["to_id"])}


def _categorize(ops, params: dict) -> dict:
    _need(params, "ids", "category")
    ids = params["ids"] if isinstance(params["ids"], list) else [params["ids"]]
    return {
        "status": "accepted",
        **ops.property_categorize([str(item) for item in ids], params["category"]),
    }


def _accept(ops, params: dict) -> dict:
    _need(params, "candidate_id")
    version = int(params.get("version") or 1)
    revision = ops.change_accept(str(params["candidate_id"]), version)
    return {"status": "accepted", "revision": revision}


def _reject(ops, params: dict) -> dict:
    _need(params, "candidate_id")
    ops.change_reject(str(params["candidate_id"]), int(params.get("version") or 1))
    return {"status": "rejected", "candidate_id": params["candidate_id"]}


def _extraction_path(ops, source_id: str) -> Path:
    """提取的中间产物：工程旁边（或包里），按原文分文件。界面和工具用同一个位置。"""
    from .project_package import layout

    return layout(ops.path).extraction / f"{source_id}.json"


def _harness(ops) -> tuple[Any, str, Path]:
    """模型设置、密钥、工作目录：和界面、协作者用的是同一份。"""
    from .harness import harness_env

    try:
        return harness_env(ops)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _read_extraction(ops, source_id: str) -> dict:
    path = _extraction_path(ops, source_id)
    if not path.is_file():
        raise ToolError("还没有提取结果：先跑 extract_propose")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_extraction(ops, source_id: str, data: dict) -> dict:
    """落盘一份提议。`raw` 已经在 workdir/raw.txt 里，不重复存。"""
    saved = dict(data)
    saved.pop("raw", None)
    saved["source_id"] = source_id
    path = _extraction_path(ops, source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return saved


def _extraction_counts(data: dict) -> dict:
    nodes = data.get("nodes") or []
    return {
        "nodes": len(nodes),
        "events": sum(1 for node in nodes if node.get("type") == "event"),
        "properties": len(data.get("properties") or []),
        "rejected": len(data.get("rejected") or []),
    }


def _extract_propose(ops, params: dict) -> dict:
    """第一步：模型读整章，出节点、事件、属性。只读原文，不带库里的旧值。"""
    from .extraction import read_chapter

    _need(params, "source_id")
    source_id = str(params["source_id"])
    source = ops.source(source_id)
    settings, api_key, workdir = _harness(ops)
    data = read_chapter(
        source["title"], source["text"], settings=settings, api_key=api_key,
        workdir=workdir / source_id / "sdk", ops=ops,
    )
    data["source_hash"] = source["hash"]
    saved = _save_extraction(ops, source_id, data)
    return {
        "status": "accepted",
        "source_id": source_id,
        **_extraction_counts(saved),
        "cache": str(_extraction_path(ops, source_id)),
    }


def _extract_review(ops, params: dict) -> dict:
    """第二步：拿库里的值对照提议，只丢确定的重复。"""
    from .extraction import review_chapter

    _need(params, "source_id")
    source_id = str(params["source_id"])
    data = _read_extraction(ops, source_id)
    settings, api_key, workdir = _harness(ops)
    reviewed = review_chapter(
        ops, data, settings=settings, api_key=api_key,
        workdir=workdir / source_id / "sdk",
    )
    saved = _save_extraction(ops, source_id, reviewed)
    review = saved.get("review") or {}
    return {
        "status": "accepted",
        "source_id": source_id,
        "kept": len(saved.get("properties") or []),
        "dropped": len(review.get("dropped") or []),
        "unsure": len(review.get("unresolved") or []),
        "supersede": len(saved.get("supersede") or []),
        "rounds": review.get("rounds"),
    }


def _extract_build(ops, params: dict) -> dict:
    """第三步：把提议落成对象和记录，只算不写。

    编号、锚点、场景归属、取代关系都在这里定，所以 agent 不必（也不该）自己
    造 id——它只要看结果、判断对不对。
    """
    from .extraction import build_candidate

    _need(params, "source_id")
    source_id = str(params["source_id"])
    built = build_candidate(ops, _read_extraction(ops, source_id))
    return {
        "source_id": source_id,
        "empty": bool(built.get("empty")),
        "errors": bool(built.get("errors")),
        "revision": built.get("revision"),
        "node_count": built.get("node_count"),
        "scene_links": built.get("scene_links"),
        "diagnostics": built.get("diagnostics") or [],
        "objects": built.get("objects") or [],
        "property_records": built.get("property_records") or [],
    }


def _extract_commit(ops, params: dict) -> dict:
    """第四步：可信的直接进图，拿不准的留成待审候选。

    `review="all"` 是旧行为：全部进候选，等人逐条看。
    """
    from .extraction import commit_extraction

    _need(params, "source_id")
    source_id = str(params["source_id"])
    data = _read_extraction(ops, source_id)
    result = commit_extraction(ops, data, review=str(params.get("review") or "doubtful"))
    return {"source_id": source_id, **result}


def _extract(ops, params: dict) -> dict:
    """一次跑完：读、对照、落候选（`commit=true` 时）。分步工具用的是同一批函数。"""
    _need(params, "source_id")
    result = _extract_propose(ops, params)
    result = result | _extract_review(ops, params)
    if not params.get("commit"):
        return result
    return result | _extract_commit(ops, params)


TOOLS: dict[str, Tool] = {}


def _register(tool: Tool) -> None:
    TOOLS[tool.name] = tool


_register(Tool(
    "list_projects",
    "列出某个目录下的工程（只读，不打开）",
    {"dir": {"type": "string", "description": "工程目录，默认 data/graph-projects"}},
    _list_projects,
))
_register(Tool(
    "project_info",
    "工程标题、修订号、节点数、原文数、待审候选数",
    {},
    _project_info,
))
_register(Tool(
    "list_nodes",
    "按阅读位置列出节点；可按类型、关键字筛选，返回分类计数",
    {
        "granularity": {"type": "string", "enum": ["scene", "event"], "description": "步进单位"},
        "cut": {"type": "string", "description": "阅读位置（事件编号）；省略为最新"},
        "node_type": {"type": "string", "description": "person/group/item/location/event/scene"},
        "query": {"type": "string", "description": "名称或说明里的关键字"},
        "related_only": {"type": "boolean", "description": "只看这一步相关的节点"},
        "limit": {"type": "integer", "description": "最多返回多少条，默认 200"},
    },
    _list_nodes,
))
_register(Tool(
    "get_node",
    "一个节点的全部字段与它在某阅读位置上的属性",
    {
        "node_id": {"type": "string", "required": True},
        "cut": {"type": "string", "description": "阅读位置（事件编号）；省略为最新"},
    },
    _get_node,
))
_register(Tool(
    "node_properties",
    "一个节点在某阅读位置上的属性（失效的不返回）",
    {
        "node_id": {"type": "string", "required": True},
        "cut": {"type": "string"},
    },
    _node_properties,
))
_register(Tool(
    "scene_state",
    "一个场景：它包含的节点，以及这一场结束时各方是什么状态",
    {"scene_id": {"type": "string", "required": True}},
    _scene_state,
))
_register(Tool(
    "event_state",
    "一个事件：相关节点，以及读到这一步时各方是什么状态",
    {"event_id": {"type": "string", "required": True}},
    _event_state,
))
_register(Tool(
    "list_moments",
    "阅读位置的层级：场景 → 事件，一列一层；cut 只能用这里的编号",
    {},
    _list_moments,
))
_register(Tool(
    "node_material",
    "一个节点的资料卡：标题、详情、带说明的图片，各自的有效区间",
    {
        "node_id": {"type": "string", "required": True},
        "cut": {"type": "string", "description": "阅读位置（事件编号）；省略为最新"},
    },
    _material_view,
))
_register(Tool(
    "statistics",
    "统计：节点分类计数、原文、属性记录种类、撤回数、审核计数",
    {},
    _statistics,
))
_register(Tool(
    "search",
    "按关键字检索节点与属性记录",
    {
        "query": {"type": "string", "required": True},
        "limit": {"type": "integer", "description": "默认 50"},
    },
    _search,
))
_register(Tool(
    "list_sources",
    "列出工程里的原文",
    {},
    _list_sources,
))
_register(Tool(
    "property_categories",
    "每个节点类型的大类表：显示用的分组，不是事实",
    {},
    _property_categories,
))
_register(Tool(
    "read_source",
    "读取一段原文（按字符位置切片）",
    {
        "source_id": {"type": "string", "required": True},
        "start": {"type": "integer", "description": "起始字符位置，默认 0"},
        "length": {"type": "integer", "description": "读取多少字，默认 2000"},
    },
    _read_source,
))
_register(Tool(
    "audit_properties",
    "属性审核：重复值、缺定义、缺证据、锚点缺失等结构问题",
    {"node_ids": {"type": "array", "items": {"type": "string"}, "description": "只审这些节点"}},
    _audit,
))
_register(Tool(
    "list_candidates",
    "列出候选（默认待审）",
    {"status": {"type": "string", "enum": ["pending", "accepted", "rejected"]}},
    _list_candidates,
))

_register(Tool(
    "add_property",
    "补录一条属性披露：立刻生效；更早的值从这次披露起失效",
    {
        "node_id": {"type": "string", "required": True},
        "property_id": {"type": "string", "required": True, "description": "属性名，如 state"},
        "value": {"type": ["string", "number", "boolean"],
                  "description": "值：字符串、数字或节点 id"},
        "event_id": {"type": "string", "required": True, "description": "披露事件编号"},
        "category": {"type": "string", "description": "显示大类，默认按属性名推断"},
        "source": {"type": "string", "description": "narration/speech/thought/observation"},
        "scope": {"type": "string", "enum": ["world", "cognition", "claim"]},
        "holder_id": {"type": "string", "description": "认知或主张的持有人"},
        "quote": {"type": "string", "description": "原文引文（与 source_id 一起给）"},
        "source_id": {"type": "string"},
    },
    _add_property,
    writes=True,
))
_register(Tool(
    "correct_property",
    "更正一条取值：追加正确的记录并撤回原来那条",
    {
        "record_id": {"type": "string", "required": True},
        "value": {"type": ["string", "number", "boolean"]},
        "event_id": {"type": "string", "description": "新记录的披露事件，默认沿用"},
        "reason": {"type": "string"},
        "quote": {"type": "string"},
        "source_id": {"type": "string"},
    },
    _correct_property,
    writes=True,
))
_register(Tool(
    "retract_properties",
    "把记录设为失效：不删除，但从这个位置起不再显示",
    {
        "ids": {"type": "array", "items": {"type": "string"}, "required": True},
        "reason": {"type": "string", "required": True},
        "event_id": {"type": "string", "description": "从这次披露起失效；省略为「读错了」"},
    },
    _retract,
    writes=True,
))
_register(Tool(
    "add_material_card",
    "给节点加一张资料卡：标题、详情、生效/失效各挑一个场景或事件",
    {
        "node_id": {"type": "string", "required": True},
        "title": {"type": "string", "required": True},
        "detail": {"type": "string"},
        "valid_from": {"type": "string", "description": "生效时间点（场景或事件编号）"},
        "valid_to": {"type": "string", "description": "失效时间点；空为一直有效"},
        "cut": {"type": "string", "description": "回读时用的阅读位置"},
    },
    _add_material_card,
    writes=True,
))
_register(Tool(
    "update_material_card",
    "改一张资料卡：只改给了的字段",
    {
        "node_id": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "title": {"type": "string"},
        "detail": {"type": "string"},
        "valid_from": {"type": "string"},
        "valid_to": {"type": "string"},
        "cut": {"type": "string"},
    },
    _update_material_card,
    writes=True,
))
_register(Tool(
    "delete_material_card",
    "删一张资料卡；它的图片一起走",
    {
        "node_id": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "cut": {"type": "string"},
    },
    _delete_material_card,
    writes=True,
))
_register(Tool(
    "add_material_image",
    "给一张资料卡加图：拷进工程，可带一句说明",
    {
        "node_id": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "path": {"type": "string", "required": True, "description": "图片文件路径"},
        "caption": {"type": "string"},
        "cut": {"type": "string"},
    },
    _add_material_image,
    writes=True,
))
_register(Tool(
    "delete_material_image",
    "删掉一张资料卡里的某张图",
    {
        "node_id": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "image_id": {"type": "string", "required": True},
        "cut": {"type": "string"},
    },
    _delete_material_image,
    writes=True,
))
_register(Tool(
    "revise_node",
    "修订一个节点的名称、类型、别名或说明（立刻生效）",
    {
        "node_id": {"type": "string", "required": True},
        "name": {"type": "string"},
        "node_type": {"type": "string", "enum": sorted(["person", "group", "item", "location", "event", "scene", "storyline", "other"])},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "identity_note": {"type": "string"},
    },
    _revise_node,
    writes=True,
))
_register(Tool(
    "relation_map",
    "人物关系网：人是点，关系是有方向的边（A 父亲 B 与 B 儿子 A 是两条）",
    {},
    _relation_map,
))
_register(Tool(
    "attach_scenes",
    "把一个或多个场景挂到大事件下面（立刻生效；场景原有的归属会被替换）",
    {
        "storyline_id": {"type": "string", "required": True},
        "scene_ids": {"type": "array", "items": {"type": "string"}, "required": True},
        "replace": {"type": "boolean", "description": "true 时只留这些场景，false 时追加（默认）"},
    },
    _attach_scenes,
    writes=True,
))
_register(Tool(
    "merge_properties",
    "把两个属性名合成一个方面（别名并入目标）",
    {
        "alias_id": {"type": "string", "required": True},
        "target_id": {"type": "string", "required": True},
    },
    _merge,
    writes=True,
))
_register(Tool(
    "rename_property",
    "把一个方面改名（取值不改写，撤回这条改名即恢复）",
    {
        "names": {"type": "array", "items": {"type": "string"}, "required": True},
        "to_id": {"type": "string", "required": True},
    },
    _rename,
    writes=True,
))
_register(Tool(
    "categorize_records",
    "把记录移到某个显示大类",
    {
        "ids": {"type": "array", "items": {"type": "string"}, "required": True},
        "category": {"type": "string", "required": True},
    },
    _categorize,
    writes=True,
))
_register(Tool(
    "accept_candidate",
    "接受一个候选（节点/属性/提取结果）",
    {
        "candidate_id": {"type": "string", "required": True},
        "version": {"type": "integer", "description": "默认 1"},
    },
    _accept,
    writes=True,
))
_register(Tool(
    "reject_candidate",
    "拒绝一个候选",
    {
        "candidate_id": {"type": "string", "required": True},
        "version": {"type": "integer"},
    },
    _reject,
    writes=True,
))
_register(Tool(
    "import_source",
    "导入一段原文：标题 + 正文；返回原文编号，extract_source 要用它",
    {
        "title": {"type": "string", "required": True},
        "text": {"type": "string", "required": True},
    },
    _import_source,
    writes=True,
))
_register(Tool(
    "extract_propose",
    "提取第一步：模型读整章，出节点、事件、属性（只读原文，不带库里的旧值）",
    {"source_id": {"type": "string", "required": True}},
    _extract_propose,
    writes=True,
))
_register(Tool(
    "extract_review",
    "提取第二步：拿库里已有的值对照提议，只丢确定的重复",
    {"source_id": {"type": "string", "required": True}},
    _extract_review,
    writes=True,
))
_register(Tool(
    "extract_build",
    "提取第三步：把提议落成对象和记录，只算不写——编号、锚点、场景、取代都在这里定",
    {"source_id": {"type": "string", "required": True}},
    _extract_build,
))
_register(Tool(
    "extract_commit",
    "提取第四步：可信的直接进图，拿不准的留成待审候选（review=all 退回全部待审）",
    {
        "source_id": {"type": "string", "required": True},
        "review": {
            "type": "string",
            "description": "doubtful（默认，只把拿不准的留给人）或 all（全部留给人）",
        },
    },
    _extract_commit,
    writes=True,
))
_register(Tool(
    "extract_source",
    "一次跑完四步：读、对照、落候选（`commit=true` 时生成待审候选）",
    {
        "source_id": {"type": "string", "required": True},
        "commit": {"type": "boolean", "description": "是否直接生成候选"},
    },
    _extract,
    writes=True,
))


def describe() -> list[dict]:
    """The tool list in the shape MCP asks for."""
    return [
        {"name": tool.name, "description": tool.summary, "inputSchema": tool.schema()}
        for tool in TOOLS.values()
    ]


def call(name: str, params: dict | None = None, *, project: str | Path | None = None) -> dict:
    """Run one tool. ``project`` is a project file, not a directory."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"没有这个工具：{name}")
    args = dict(params or {})
    unknown = set(args) - set(tool.params)
    if unknown:
        raise ToolError("不认识的参数：" + "、".join(sorted(unknown)))
    for key, spec in tool.params.items():
        if spec.get("required") and args.get(key) in (None, ""):
            raise ToolError(f"缺少参数：{key}")
    if name == "list_projects":
        return tool.handler(None, args)
    if project in (None, ""):
        raise ToolError("请提供 --project（工程 .sqlite3 文件）")
    path = Path(project)
    if not path.exists():
        raise ToolError(f"工程不存在：{path}")
    # 裸库和工程包目录都收；`open_graph_project` 会把包目录归到包里的库。
    ops = open_graph_project(path)
    try:
        return tool.handler(ops, args)
    finally:
        close = getattr(ops, "close", None)
        if callable(close):
            close()


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
