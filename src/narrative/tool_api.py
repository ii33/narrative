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
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .extraction_draft import RESULT_SCHEMA
from .project_catalog import list_projects, open_graph_project
from .screenplay_tools import register as _register_screenplay_tools


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
    scope: str = "project"

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


def _duplicate_project(_ops, params: dict) -> dict:
    from .operations import Operations
    _need(params, "source", "destination")
    for key in ("source", "destination"):
        _validate_adaptation_argument(params[key], {"type": "string"}, key)
    try:
        copied = Operations.duplicate_graph_project(params["source"], params["destination"])
    except (ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc
    info = copied.graph_project_info()
    return {"status": "copied", "path": str(Path(params["destination"]).resolve()),
            "title": info["title"], "revision": info["revision"]}


def _project_info(ops, _params: dict) -> dict:
    info = ops.graph_project_info()
    snapshot = ops.graph_snapshot()
    return {
        "project": {key: info[key] for key in ("title", "name", "revision", "schema_version") if key in info},
        "nodes": dict(_node_types(snapshot)),
        "sources": len(ops.sources()),
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
    return {"node": node, "cut": cut, "properties": _properties(ops, node_id, cut)}


def _node_properties(ops, params: dict) -> dict:
    _need(params, "node_id")
    cut = _cut(params)
    requested = str(params["node_id"])
    return {"node_id": requested, "cut": cut, "properties": _properties(ops, requested, cut)}


def _related_ids(ops, step_id: str) -> list[str]:
    from .browse_view import related_nodes

    snapshot = ops.graph_snapshot()
    return sorted(related_nodes(snapshot, step_id))


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
        if node is None or node.get("node_type") in {"event", "action", "scene", "big_event", "storyline"}:
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


def _material_view(ops, params: dict) -> dict:
    _need(params, "node_id")
    return ops.material_view(str(params["node_id"]), cut=_cut(params) or "")


def _add_material_card(ops, params: dict) -> dict:
    _need(params, "node_id", "title")
    card = ops.material_add_card(
        str(params["node_id"]),
        str(params["title"]),
        str(params.get("detail") or ""),
        str(params.get("valid_from") or ""),
        str(params.get("valid_to") or ""),
    )
    return {"status": "accepted", "card": card} | _material_view(ops, params)


def _update_material_card(ops, params: dict) -> dict:
    _need(params, "node_id", "card_id")
    card = ops.material_update_card(
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
    result = ops.material_remove_card(str(params["node_id"]), str(params["card_id"]))
    return {"status": "accepted", **result} | _material_view(ops, params)


def _add_material_image(ops, params: dict) -> dict:
    _need(params, "node_id", "card_id", "path")
    record = ops.material_add_image(
        str(params["node_id"]),
        str(params["card_id"]),
        Path(str(params["path"])),
        str(params.get("caption") or ""),
    )
    return {"status": "accepted", "image": record} | _material_view(ops, params)


def _delete_material_image(ops, params: dict) -> dict:
    _need(params, "node_id", "card_id", "image_id")
    result = ops.material_remove_image(
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
    records = ops.property_records()
    kinds = Counter(record.get("kind") for record in records)
    definitions = {record["id"] for record in records if record.get("kind") == "property_definition"}
    values = [record for record in records if record.get("kind") == "property_value"]
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
            "expired_values": sum(1 for row in records if row.get("valid_to")),
            "records_by_kind": dict(kinds),
        },
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


def _source_id(ops, params: dict) -> str:
    supplied = params.get("source_id")
    if supplied not in (None, ""):
        if not isinstance(supplied, str):
            raise ToolError("source_id须为真实原文编号")
        return supplied
    sources = ops.sources()
    if len(sources) != 1:
        raise ToolError("仅一份原文时可省略source_id；当前须明确提供真实SRC编号")
    return sources[0]["id"]


def _read_source(ops, params: dict) -> dict:
    source = ops.source(_source_id(ops, params))
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


def _locate_source(ops, params: dict) -> dict:
    """One source read and bounded quote batches; no hash or evidence objects."""
    _need(params, "quotes")
    source_id = _source_id(ops, params)
    return {"source_id": source_id,
            "results": ops.locate(source_id, params["quotes"], limit_per_quote=params.get("limit_per_quote", 20))}


def _list_source_slices(ops, params: dict) -> dict:
    source_id = _source_id(ops, params)
    try:
        return {"source_id": source_id, "slices": ops.source_slices(source_id)}
    except (ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from exc


def _read_source_slice(ops, params: dict) -> dict:
    _need(params, "slice_id")
    try:
        return ops.source_slice(_source_id(ops, params), str(params["slice_id"]))
    except (ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from exc


def _create_source_slices(ops, params: dict) -> dict:
    _need(params, "slices")
    slices = params["slices"]
    if not isinstance(slices, list) or not slices:
        raise ToolError("slices must be a non-empty array")
    for item in slices:
        if not isinstance(item, dict) or set(item) - {"title", "start", "end"}:
            raise ToolError("Each slice requires only title, start and end")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            raise ToolError("Each slice requires a non-empty title")
        if any(type(item.get(key)) is not int for key in ("start", "end")):
            raise ToolError("Slice start and end must be integer Unicode codepoint offsets")
    source_id = _source_id(ops, params)
    try:
        created = ops.create_source_slices(source_id, slices)
    except (ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from exc
    return {"status": "created", "source_id": source_id, "slices": created}


def _pattern_slices(ops, params: dict, *, create: bool = False) -> dict:
    from .pattern_slices import compact

    _need(params, "pattern")
    args = {key: params[key] for key in ("pattern", "start_index", "limit") if key in params}
    args["source_id"] = _source_id(ops, params)
    try:
        if create:
            result = ops.create_pattern_slices(**args)
        else:
            result = ops.preview_pattern_slices(**args)
        return compact(result)
    except (ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from exc


def _event_merge(ops, params: dict, *, create: bool = False) -> dict:
    _need(params, "source_event_id", "target_event_id", "expected_revision", "evidence", "reason")
    try:
        method = ops.merge_events if create else ops.preview_event_merge
        return method(**params)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _audit(ops, params: dict) -> dict:
    node_ids = params.get("node_ids")
    return ops.property_audit(set(node_ids) if node_ids else None)




# -- writing ---------------------------------------------------------------


def _revision(ops) -> int:
    return int(ops.graph_project_info()["revision"])


def _add_property(ops, params: dict) -> dict:
    _need(params, "node_id", "property_id", "value", "event_id")
    snapshot = ops.graph_snapshot()
    if not any(node["id"] == params["node_id"] for node in snapshot["nodes"]):
        raise ToolError(f"节点不存在：{params['node_id']}")
    anchor_node = next((node for node in snapshot["nodes"] if node["id"] == params["event_id"]), None)
    if anchor_node is None:
        raise ToolError(f"事件不存在：{params['event_id']}")

    records = ops.property_records()
    definitions = {record["id"]: record for record in records if record.get("kind") == "property_definition"}
    definition = definitions.get(params["property_id"])
    value_type = params.get("value_type")
    allowed_types = {"string", "number", "boolean", "node", "either", "any"}
    if value_type is not None and (not isinstance(value_type, str) or value_type not in allowed_types):
        raise ToolError("value_type必须为string/number/boolean/node/either/any")
    if "symmetric" in params and type(params["symmetric"]) is not bool:
        raise ToolError("symmetric必须为布尔值")
    batch: list[dict] = []
    if definition is None:
        if value_type is None:
            raise ToolError("新属性定义必须明确提供value_type；节点引用用node/either，不根据P编号字符串猜类型")
        batch.append({"kind": "property_definition", "id": params["property_id"],
                      "value_type": value_type, "cardinality": "one",
                      "symmetric": params.get("symmetric", False)})
    else:
        if value_type is not None and value_type != definition["value_type"]:
            raise ToolError("value_type与已有定义冲突；请通过显式业务修改定义")
        if "symmetric" in params and params["symmetric"] != definition.get("symmetric", False):
            raise ToolError("symmetric与已有定义冲突；请通过显式业务修改定义")
    record: dict[str, Any] = {
        "kind": "property_value",
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
    source_id = (params.get("source_id") or (anchor_node.get("disclosure") or {}).get("source_id")
                 or (anchor_node.get("evidence") or {}).get("source_id") or _source_id(ops, params))
    if quote:
        hits = ops.locate(source_id, [quote], limit_per_quote=2)[0]["matches"]
        if len(hits) != 1:
            raise ToolError("引文缺失或不唯一，请补充准确上下文")
        record["evidence"] = {"source_id": source_id, "quote": quote}
        record["disclosure"] = {"source_id": source_id, "start": hits[0]["start"]}
    else:
        metadata = next((row for row in snapshot["sources"] if row["id"] == source_id), None)
        if metadata is None:
            raise ToolError("原文不存在")
        record["disclosure"] = {"source_id": source_id, "start": metadata["length"]}
    batch.append(record)

    revision = _revision(ops)
    return ops.apply_changes([], batch, revision, "工具补录")


def _correct_property(ops, params: dict) -> dict:
    _need(params, "record_id", "value", "reason")
    options = {key: params[key] for key in (
        "event_id", "source_id", "quote", "category", "scope", "holder_id"
    ) if key in params}
    return {"status": "accepted", **ops.correct_property(
        params["record_id"], params["value"], params["reason"], **options,
    )}


def _expire(ops, params: dict) -> dict:
    _need(params, "record_ids", "source_id", "start", "reason")
    return {"status": "accepted", **ops.expire_properties(
        params["record_ids"], params["source_id"], params["start"],
        event_id=params.get("event_id"), reason=params["reason"],
    )}


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
    return ops.apply_changes([updated], [], revision, f"工具修订：{node.get('name')}") | {
        "node_id": params["node_id"], "changes": changes,
    }


def _relation_map(ops, params: dict) -> dict:
    """人物关系网：人是点，关系是有方向的边。

    属性名是关系性质，值指向对方；同向、同性质/状态/作用域的多次披露汇总为一条边，
    原记录与证据保留在 records 中。不同性质仍分开，所以两个人之间
    可以有两支箭头——「A 父亲 B」和「B 儿子 A」——也可以只有一支（「A 崇拜 B」）。
    关系性质带括号时（「恋人（冷战中）」），括号里的是状态，单独给出来。
    """
    from .browse_view import cut_for, moment_tree, related_nodes
    from .property_core import canonical_definitions
    from .property_store import relation_target
    from .temporal_state import ended_at, point, position

    snapshot = ops.graph_snapshot()
    nodes = {item["id"]: item for item in snapshot["nodes"]}
    events = {item["id"]: item.get("name", item["id"]) for item in snapshot["nodes"]}
    cut = str(params.get("cut") or "")
    anchor = cut_for(snapshot, cut) if cut else None
    persons = [
        {"id": item["id"], "name": item.get("name", item["id"]),
         "label_id": item.get("label_id", item["id"])}
        for item in snapshot["nodes"] if item.get("node_type") == "person"
    ]
    edges: list[dict] = []
    relation_edges: dict[tuple, dict] = {}
    records = ops.property_records()
    cut_position = position(anchor, snapshot) if anchor else None
    invalid = ended_at(records, snapshot, anchor)
    editorial = [record for record in records if record.get("id") not in invalid]
    definitions = canonical_definitions(editorial)
    for record in records:
        if record.get("kind") != "property_value" or record.get("id") in invalid:
            continue
        property_id = str(record.get("property_id") or "")
        canonical = property_id
        if relation_target(record | {"property_id": canonical}, nodes, definitions) is None:
            continue
        if cut_position is not None and (position(point(record), snapshot) or (float("inf"), 0)) > cut_position:
            continue
        left, right = str(record.get("node_id") or ""), str(record.get("value") or "")
        if left == right:
            continue  # 自己指向自己不是关系（审核会报 self_reference）
        if nodes.get(left, {}).get("node_type") != "person":
            continue
        if nodes.get(right, {}).get("node_type") != "person":
            continue
        raw = canonical
        kind, state = raw, ""
        for mark in ("（", "("):
            if mark in raw:
                head, _, tail = raw.partition(mark)
                kind, state = head.strip(), tail.rstrip("）)").strip()
                break
        scope = str(record.get("scope") or "world")
        holder = str(record.get("holder_id") or "")
        context = str(record.get("context_id") or "canon")
        key = (left, right, kind, state, scope, holder, context)
        existing = relation_edges.get(key)
        if existing is not None:
            existing["record_ids"].append(record.get("id"))
            existing["records"].append(dict(record))
            if record.get("event_id") not in existing["event_ids"]:
                existing["event_ids"].append(record.get("event_id"))
            continue
        edge = {
            "record_id": record.get("id"),
            "record_ids": [record.get("id")],
            "records": [dict(record)],
            "from": left, "from_name": nodes[left].get("name", left),
            "to": right, "to_name": nodes[right].get("name", right),
            "kind": kind, "state": state,
            "symmetric": definitions.get(canonical, {}).get("symmetric") is True,
            "scope": scope, "holder_id": holder, "context_id": context,
            "event_id": record.get("event_id"),
            "event_ids": [record.get("event_id")],
            "event_name": events.get(record.get("event_id") or "", ""),
        }
        relation_edges[key] = edge
        edges.append(edge)
    edges.sort(key=lambda item: (item["from_name"], item["kind"], item["to_name"]))

    # 只看相关：只留跟这一步有关的人（这一步出场的人、以及关系指向他们的人）。
    related_only = bool(params.get("related_only"))
    related_ids: set[str] = set()
    if related_only and cut:
        related_ids = {
            item for item in related_nodes(snapshot, cut, ops.property_records())
            if nodes.get(item, {}).get("node_type") == "person"
        }
        # 两头都得是这一步里的人：只要一头相关，枢纽人物的所有关系都会被拖进来，
        # 图上又出现这一场根本没出场的人。
        edges = [
            e for e in edges
            if e["from"] in related_ids and e["to"] in related_ids
        ]

    # 没写阅读位置、也没开筛选时，全书的人都画（孤点说明「书里没写他和谁有关」）；
    # 一旦按位置或相关收窄，就只留连线两端——筛选后还摆一屏孤点，等于没筛。
    if related_only and cut:
        # 只看相关时，「相关的人」本身就是答案：一个人出场的场景就画一个人。
        persons = [item for item in persons if item["id"] in related_ids]
    elif cut_position is not None:
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


def _attach_children(ops, params: dict) -> dict:
    """按明确父子编号归入直属容器，完整结构由 Operations 静态校验。"""
    _need(params, "parent_id", "child_ids")
    child_ids = params["child_ids"]
    if not isinstance(child_ids, list) or not child_ids or any(not isinstance(item, str) for item in child_ids):
        raise ToolError("child_ids须为非空节点编号数组")
    nodes = {item["id"]: item for item in ops.graph_snapshot()["nodes"]}
    parent = nodes.get(params["parent_id"])
    if parent is None:
        raise ToolError("父节点不存在")
    child_type = {"storyline": "big_event", "big_event": "scene", "scene": "event", "event": "action"}.get(str(parent.get("node_type") or ""))
    if child_type is None:
        raise ToolError("parent_id须指向主要故事、大事件、场景或事件")
    if any(nodes.get(item, {}).get("node_type") not in ({"scene", "event"} if parent["node_type"] == "scene" else {child_type}) for item in child_ids):
        raise ToolError(f"直属子节点类型须为{child_type}")
    existing = [] if params.get("replace") else list(parent.get("events") or [])
    merged = list(dict.fromkeys(existing + child_ids))
    return ops.apply_changes([dict(parent) | {"events": merged}], [], _revision(ops), "归入直属容器")



def _merge(ops, params: dict) -> dict:
    _need(params, "source_id", "target_id", "reason", "expected_revision")
    return {"status": "accepted", **ops.merge_properties(
        params["source_id"], params["target_id"], params["reason"], params["expected_revision"],
    )}


def _rename(ops, params: dict) -> dict:
    _need(params, "from_id", "to_id", "reason", "expected_revision")
    return {"status": "accepted", **ops.rename_property(
        params["from_id"], params["to_id"], params["reason"], params["expected_revision"],
    )}


def _categorize(ops, params: dict) -> dict:
    _need(params, "ids", "category")
    ids = params["ids"] if isinstance(params["ids"], list) else [params["ids"]]
    return {
        "status": "accepted",
        **ops.property_categorize([str(item) for item in ids], params["category"]),
    }


































def _preview_extraction(ops, params: dict) -> dict:
    _need(params, "result")
    return ops.preview_extraction(_source_id(ops, params), params["result"],
                                  params.get("start", 0), params.get("end"))


def _submit_extraction(ops, params: dict) -> dict:
    _need(params, "result")
    return ops.submit_extraction(_source_id(ops, params), params["result"],
                                 params.get("start", 0), params.get("end"))


TOOLS: dict[str, Tool] = {}


def _register(tool: Tool) -> None:
    TOOLS[tool.name] = tool


_register(Tool(
    "list_projects",
    "列出某个目录下的工程（只读，不打开）",
    {"dir": {"type": "string", "description": "工程目录，默认 data/graph-projects"}},
    _list_projects,
    scope="workspace",
))
_register(Tool(
    "duplicate_project",
    "创建完整、独立的工程副本；目标为新的.nproj入口，不覆盖已有工程，源工程不变",
    {"source": {"type": "string", "required": True, "description": "源.nproj入口路径"},
     "destination": {"type": "string", "required": True, "description": "新副本.nproj入口路径"}},
    _duplicate_project, writes=True, scope="workspace",
))
_register(Tool(
    "check_project",
    "只读静态检查当前有效图与属性，返回固定修订号和可定位诊断；不创建候选或写入",
    {},
    lambda ops, params: ops.check_project(),
))
_register(Tool(
    "project_info",
    "工程标题、修订号、节点数与原文数",
    {},
    _project_info,
))
_register(Tool(
    "list_nodes",
    "按阅读位置列出节点；可按类型、关键字筛选，返回分类计数",
    {
        "granularity": {"type": "string", "enum": ["scene", "event"], "description": "步进单位"},
        "cut": {"type": "string", "description": "阅读位置（事件编号）；省略为最新"},
        "node_type": {"type": "string", "description": "person/group/item/location/event/action/scene"},
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
    "统计：节点分类计数、原文、属性记录种类与失效记录数",
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
    "locate_source",
    "批量逐字定位引文，返回输入索引、字符坐标与唯一性；多匹配截断明确标记，不创建证据",
    {"source_id": {"type": "string"},
     "quotes": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 1000, "required": True},
     "limit_per_quote": {"type": "integer", "minimum": 1, "maximum": 1000,
                         "description": "每条最多返回坐标，默认20；截断时match_count为下界、count_is_exact=false，仍报告歧义"}},
    _locate_source,
))

_register(Tool(
    "list_source_slices",
    "列出一份原文已保存的切片，按原文位置排列；不创建切片",
    {"source_id": {"type": "string", "required": True}},
    _list_source_slices,
))
_register(Tool(
    "read_source_slice",
    "读取一份原文中的单个切片，返回完整内容和原文位置",
    {
        "source_id": {"type": "string", "required": True},
        "slice_id": {"type": "string", "required": True},
    },
    _read_source_slice,
))
_register(Tool(
    "create_source_slices",
    "为无规律内容按语义提供手动ranges批量创建切片；有规律标题优先使用pattern切片工具，不改原文",
    {
        "source_id": {"type": "string", "required": True},
        "slices": {
            "type": "array", "required": True, "minItems": 1,
            "description": "整批校验后创建；start/end 为原文中从 0 开始的 Python Unicode codepoint 位置，end 不包含在切片内，不是 UTF-8 字节或 UTF-16 单元",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["title", "start", "end"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "start": {"type": "integer", "minimum": 0},
                    "end": {"type": "integer", "minimum": 1},
                },
            },
        },
    },
    _create_source_slices,
    writes=True,
))

_PATTERN_PARAMS = {
    "source_id": {"type": "string", "required": True},
    "pattern": {"type": "string", "required": True,
                "description": "自行从原文样本确定的标题正则；MULTILINE，一次扫描全篇，匹配非空单行标题；2秒超时"},
    "start_index": {"type": "integer", "minimum": 0, "description": "从0开始的匹配标题索引，不是字符位置；默认0"},
    "limit": {"type": "integer", "minimum": 1, "description": "选取章节数量；省略为剩余全部。末选章以其下一标题为终点"},
}
_register(Tool(
    "preview_pattern_slices",
    "小样确认标题模式后，一次程序扫描并计算相邻章节范围；只读。返回完整计数及最多6个明确标记的首尾样例，章前内容明确排除",
    _PATTERN_PARAMS,
    lambda ops, params: _pattern_slices(ops, params),
))
_register(Tool(
    "create_pattern_slices",
    "按标题模式和章节索引范围一次自动扫描、校验并创建切片，无需预览或抄写哈希与坐标",
    _PATTERN_PARAMS,
    lambda ops, params: _pattern_slices(ops, params, create=True),
    writes=True,
))

_EVENT_MERGE_PARAMS = {
    "source_event_id": {"type": "string", "required": True, "description": "要归入已有事件的重复事件ID"},
    "target_event_id": {"type": "string", "required": True, "description": "保留的更早披露事件ID"},
    "expected_revision": {"type": "integer", "required": True},
    "reason": {"type": "string", "required": True, "description": "依据原文说明两ID为何确为同一次发生"},
    "evidence": {"type": "array", "required": True, "minItems": 1,
                 "items": {"type": "object", "required": ["source_id", "start", "end", "quote"],
                    "additionalProperties": False, "properties": {
                        "source_id": {"type": "string"}, "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 1}, "quote": {"type": "string"}}}},
}
_register(Tool("preview_event_merge", "核对原文后预检两个事件ID的显式合并；不按同名自动合并，不写入",
               _EVENT_MERGE_PARAMS, lambda ops, params: _event_merge(ops, params)))
_register(Tool("merge_events", "凭覆盖两事件的逐字证据合并重复事件；保留历史和canonical映射，当前引用统一指向target",
               _EVENT_MERGE_PARAMS, lambda ops, params: _event_merge(ops, params, create=True), writes=True))

_register(Tool(
    "audit_properties",
    "检查属性证据与结构：重复值、缺定义、缺证据、锚点缺失等诊断",
    {"node_ids": {"type": "array", "items": {"type": "string"}, "description": "只审这些节点"}},
    _audit,
))

_register(Tool(
    "add_property",
    "补录一条属性披露：立刻生效；更早的值从这次披露起失效",
    {
        "node_id": {"type": "string", "required": True, "description": "属性主体；身份、关系或作用角色中为参照方A"},
        "property_id": {"type": "string", "required": True, "description": "属性名；关系描述对方相对node的身份、关系或作用中的角色：node=A,property=父亲,value=B表示B是A的父亲"},
        "value": {"type": ["string", "number", "boolean"],
                  "description": "值：字符串、数字或节点ID；节点引用为被指向的对方，普通字面值不改写"},
        "value_type": {"type": "string", "enum": ["string", "number", "boolean", "node", "either", "any"], "description": "新定义必填，已知定义省略沿用；node/either为节点引用，string/any不能因P样字符串视为关系"},
        "symmetric": {"type": "boolean", "description": "新关系定义是否对称，默认false；已有定义省略沿用，冲突拒绝"},
        "event_id": {"type": "string", "required": True, "description": "披露事件编号"},
        "category": {"type": "string", "description": "显示大类，默认按属性名推断"},
        "source": {"type": "string", "description": "narration/speech/thought/observation"},
        "scope": {"type": "string", "enum": ["world", "cognition", "claim"], "description": "客观world、人物所知所信cognition、言说主张claim；非world须给认知者/说话人holder_id，不按对白形式机械分类"},
        "holder_id": {"type": "string", "description": "认知者或说话人ID；node_id仍是被认知/谈论的主体，可与holder_id不同"},
        "quote": {"type": "string", "description": "原文引文（与 source_id 一起给）"},
        "source_id": {"type": "string"},
    },
    _add_property,
    writes=True,
))
_register(Tool(
    "correct_property",
    "修正录入错误：直接更正原取值记录；故事中发生新状态请追加属性值，旧单值自动失效",
    {
        "record_id": {"type": "string", "required": True},
        "value": {"type": ["string", "number", "boolean", "object", "array"], "required": True},
        "event_id": {"type": "string", "description": "正确的披露事件，默认沿用"},
        "reason": {"type": "string", "required": True},
        "quote": {"type": "string"},
        "source_id": {"type": "string"},
    },
    _correct_property,
    writes=True,
))
_register(Tool(
    "expire_properties",
    "在原文披露边界结束旧值有效期（valid_to），不创建撤回记录；录入错误请更正或删除",
    {
        "record_ids": {"type": "array", "items": {"type": "string"}, "required": True},
        "source_id": {"type": "string", "required": True},
        "start": {"type": "integer", "required": True},
        "reason": {"type": "string", "required": True},
        "event_id": {"type": "string"},
    }, _expire, writes=True,
))
_register(Tool(
    "delete_property_records", "物理删除错误的属性记录；不表示故事中的状态变化",
    {"record_ids": {"type": "array", "items": {"type": "string"}, "required": True},
     "reason": {"type": "string", "required": True}},
    lambda ops, p: ops.delete_property_records(p["record_ids"], p["reason"]), writes=True,
))
_register(Tool(
    "rollback_preview", "只读预览按原文时间点删除后缀数据的范围",
    {"source_id": {"type": "string", "required": True},
     "start": {"type": "integer", "required": True},
     "expected_revision": {"type": "integer", "required": True}},
    lambda ops, p: ops.rollback_preview(p["source_id"], p["start"], p["expected_revision"]),
))
_register(Tool(
    "rollback_to", "按原文时间点物理删除后缀数据并清理引用；先用rollback_preview核对范围",
    {"source_id": {"type": "string", "required": True},
     "start": {"type": "integer", "required": True},
     "expected_revision": {"type": "integer", "required": True},
     "reason": {"type": "string", "required": True}},
    lambda ops, p: ops.rollback_to(p["source_id"], p["start"], p["expected_revision"], p["reason"]), writes=True,
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
        "node_type": {"type": "string", "enum": sorted(["person", "group", "item", "location", "event", "action", "scene", "big_event", "storyline", "other"])},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "identity_note": {"type": "string"},
    },
    _revise_node,
    writes=True,
))
_register(Tool(
    "relation_map",
    "人物关系网：保留方向；定义明确symmetric的关系显示双向，A父亲B与B儿子A分别保留",
    {"cut": {"type": "string", "description": "阅读位置节点ID；省略表示最新"},
     "related_only": {"type": "boolean", "description": "仅显示与阅读位置相关的人物"}},
    _relation_map,
))
_register(Tool(
    "attach_children",
    "把已有节点归入直属容器：主要故事→大事件→场景→事件→动作；静态校验后直接提交",
    {
        "parent_id": {"type": "string", "required": True},
        "child_ids": {"type": "array", "items": {"type": "string"}, "required": True},
        "replace": {"type": "boolean", "description": "true 时只留这些子节点，false 时追加（默认）"},
    },
    _attach_children,
    writes=True,
))
_register(Tool(
    "merge_properties", "仅合并已查证同义且schema一致的属性定义及引用；相同主体或对象不是同义依据，不同关系性质须保留",
    {"source_id": {"type": "string", "required": True},
     "target_id": {"type": "string", "required": True},
     "reason": {"type": "string", "required": True},
     "expected_revision": {"type": "integer", "required": True}},
    _merge, writes=True,
))
_register(Tool(
    "rename_property", "物理重命名属性定义并更新引用",
    {"from_id": {"type": "string", "required": True},
     "to_id": {"type": "string", "required": True},
     "reason": {"type": "string", "required": True},
     "expected_revision": {"type": "integer", "required": True}},
    _rename, writes=True,
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
    "import_source",
    "导入一段原文：标题 + 正文；返回统一对话读写工具使用的原文编号",
    {
        "title": {"type": "string", "required": True},
        "text": {"type": "string", "required": True},
    },
    _import_source,
    writes=True,
))




for _draft_name, _draft_handler, _draft_writes in (
    ("preview_extraction", _preview_extraction, False),
    ("submit_extraction", _submit_extraction, True),
):
    _register(Tool(
        _draft_name,
        ("检查完整事实草稿的原文证据、关系性质、发生过程与失效锚点，只返回诊断" if not _draft_writes else
         "把本任务已核对的完整事实草稿自动入库，保留证据与不确定性；不发起另一个模型任务"),
        {"source_id": {"type": "string", "required": True},
         "result": {"type": "object", "properties": RESULT_SCHEMA["properties"],
                    "additionalProperties": False, "required": True,
                    "description": "草稿须有nodes和properties；已有节点用正式ID，无须重列；同批引用按nodes数组用@1起的序号，避免重复名字；关系性质写property而非category；node=A,property=父亲,value=B表示B是A的父亲"},
         "start": {"type": "integer", "description": "完整原文的起始字符位置，默认0"},
         "end": {"type": "integer", "description": "完整原文的结束位置，不包含，默认全文末尾"}},
        _draft_handler, writes=_draft_writes,
    ))


_CHANGE_PARAMS = {
    "graph_objects": {"type": "array", "items": {"type": "object"}, "required": True,
                      "description": "完整新增或更新对象；kind可为node/context/timeline/source_order，结构见get_schema；节点events表达嵌套归属"},
    "property_records": {"type": "array", "items": {"type": "object"}, "required": True,
                         "description": "追加或更新合法属性记录；新增取值无需填写id，由程序分配PV编号，新单值自动结束旧值有效期；结构见get_schema"},
    "expected_revision": {"type": "integer", "required": True,
                          "description": "刚读取的工程修订号，过期即拒绝，不覆盖他人修改"},
}


def _project_changes(ops, params, *, write=False):
    _need(params, "expected_revision")
    try:
        args = {key: params.get(key, []) for key in ("graph_objects", "property_records")}
        if write:
            return ops.apply_changes(**args, expected_revision=params["expected_revision"], reason=params.get("reason", ""))
        return ops.preview_changes(**args, expected_revision=params["expected_revision"])
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


_register(Tool("get_schema", "读取当前图对象和属性记录的结构合同、字段及图JSON schema，不写入", {},
               lambda ops, params: ops.get_schema()))
_register(Tool("read_project_state", "读取当前完整图结构与原始属性记录（含有效区间），用于查漏补缺；图的投影审计字段不可原样写回", {
    },
    lambda ops, params: _read_project_state(ops, params)))
_register(Tool("preview_changes", "对通用工程修改做静态预检：证据、引用、结构及修订号；不写入", _CHANGE_PARAMS,
               lambda ops, params: _project_changes(ops, params)))
_register(Tool("apply_changes", "静态校验后原子写入通用图对象与属性记录，保留变更审计，不等待人工接受", _CHANGE_PARAMS | {
    "reason": {"type": "string", "required": True, "description": "这批修改的依据与目的，记入审计"}},
    lambda ops, params: _project_changes(ops, params, write=True), writes=True))


for _name in ("read_source", "locate_source", "list_source_slices", "read_source_slice",
              "create_source_slices", "preview_pattern_slices", "create_pattern_slices",
              "preview_extraction", "submit_extraction"):
    TOOLS[_name].params["source_id"] = {"type": "string", "description": "真实SRC编号；工程恰好一份原文时可省略，多份必须指定"}


def _read_project_state(ops, _params: dict) -> dict:
    state = ops.read_project_state()
    graph = {key: value for key, value in state["graph"].items()
             if key in {"revision", "schema_version", "objects", "sources"}}
    graph["sources"] = [{key: source[key] for key in ("id", "title", "length", "imported_revision")
                         if key in source} for source in graph.get("sources", [])]
    return state | {"graph": graph}


# Adaptation and visual assets share the same public Operations boundary.
def _validate_adaptation_argument(value, spec, path):
    """Enforce the published transport schema before invoking Operations."""
    kind = spec.get("type")
    types = kind if isinstance(kind, list) else [kind]
    valid = {"string": isinstance(value, str), "integer": type(value) is int,
             "boolean": type(value) is bool, "object": isinstance(value, dict),
             "array": isinstance(value, list), "null": value is None,
             "number": type(value) in (int, float)}
    if kind and not any(valid.get(item, False) for item in types):
        raise ToolError(f"{path} 类型须为 {kind}")
    if "enum" in spec and value not in spec["enum"]:
        raise ToolError(f"{path} 须为 {spec['enum']}")
    if isinstance(value, dict) and "properties" in spec:
        fields = spec["properties"]
        if spec.get("additionalProperties") is False and set(value) - set(fields):
            raise ToolError(f"{path} 有不认识的字段：{sorted(set(value) - set(fields))}")
        for key, child in value.items():
            if key in fields:
                _validate_adaptation_argument(child, fields[key], f"{path}.{key}")
    if isinstance(value, list) and "items" in spec:
        for index, child in enumerate(value):
            _validate_adaptation_argument(child, spec["items"], f"{path}[{index}]")


def _adaptation_tool(name, summary, params, *, writes=False):
    def invoke(ops, args):
        for key, value in args.items():
            _validate_adaptation_argument(value, params[key], key)
        try:
            result = getattr(ops, name)(**args)
            return {"moments": result} if name in {"asset_moments", "import_adaptation_candidates"} else result
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)) from exc
    _register(Tool(name, summary, params, invoke, writes=writes))


_ADAPT_REV = {"expected_revision": {"type": "integer", "description": "改编独立修订号，来自adaptation_snapshot；不是提取revision"},
              "reason": {"type": "string", "description": "操作原因，记入审计"}}
_ADAPT_NODE = {"node_id": {"type": "string", "required": True}}
_ADAPT_MOVE = {"after_id": {"type": ["string", "null"], "description": "改编节点ID；省略或null放到开头（复制默认紧随原节点）"},
               "parent_id": {"type": ["string", "null"], "description": "新父节点ID；省略或null保留原归属"}}
_ASSET_DOMAIN = {"type": "string", "enum": ["adaptation", "extraction"], "description": "默认adaptation；两数据域不互相写入"}
_ASSET_IMAGE = {"type": "object", "properties": {"id": {"type": "string"}, "path": {"type": "string"},
                "file": {"type": "string"}, "caption": {"type": "string"}}, "additionalProperties": False}
_ASSET_PAYLOAD = {"type": "object", "required": True, "description": "无id新建，有id编辑；图片传本地path或已有image id，省略图片字段保留原图",
    "properties": {"id": {"type": "string"}, "category": {"type": "string", "enum": ["appearance", "item", "location", "material"]},
                   "node_id": {"type": "string"}, "title": {"type": "string"}, "detail": {"type": "string"},
                   "domain": _ASSET_DOMAIN, "valid_from": {"type": "string"}, "valid_to": {"type": "string"},
                   "prompt": {"type": "string"}, "images": {"type": "array", "items": _ASSET_IMAGE},
                   "reference_images": {"type": "array", "items": _ASSET_IMAGE}, "revision": {"type": "integer"}}, "additionalProperties": False}
_adaptation_tool("adaptation_snapshot", "读取独立改编节点、排序、属性及同步冲突", {})
_adaptation_tool("check_adaptation", "静态检查改编结构、引用和属性有效区间", {})
_adaptation_tool("load_project_style", "读取工程共享风格约定及独立修订；单剧本特殊要求保留在creative_brief", {})
_adaptation_tool("save_project_style", "保存工程共享风格约定，可空；使用独立修订并记录审计，不改正文", {
    "text": {"type": "string", "required": True}, "expected_revision": {"type": "integer", "required": True},
    "reason": {"type": "string"}}, writes=True)
_adaptation_tool("load_prompt_settings", "读取工程可编辑提示词、内置默认、有效值和设置修订", {})
_adaptation_tool("save_prompt_override", "修改单项工程提示词；text=null恢复默认；保留设置修订检查与审计，下一任务使用新配置", {
    "prompt_id": {"type": "string", "required": True},
    "text": {"type": ["string", "null"], "required": True},
    "expected_revision": {"type": "integer", "required": True}, "reason": {"type": "string"}}, writes=True)
_adaptation_tool("import_adaptation_candidates", "列出可多选导入的提取时间点及层级；不写入", {})
_adaptation_tool("import_adaptation", "从提取导入指定多个时间点、必要结构与相关有效状态，保留本地改编", _ADAPT_REV | {
    "moment_ids": {"type": "array", "items": {"type": "string"}, "required": True,
                   "description": "从import_adaptation_candidates选择的非空时间点ID列表；容器包含下属，依赖与区间边界由程序补齐"}}, writes=True)
_adaptation_tool("adaptation_browse_view", "读取与数据浏览同结构的改编视图，排序与状态按改编sort_id计算", {
    "granularity": {"type": "string", "enum": ["scene", "event", "action"]},
    "cut_id": {"type": ["string", "null"]}, "node_type": {"type": "string"}, "query": {"type": "string"},
    "related_only": {"type": "boolean"}, "time_filter": {"type": "boolean"}, "include_suppressed": {"type": "boolean"}})
for _view_name in ("adaptation_node_properties", "adaptation_node_editor", "adaptation_node_detail", "adaptation_node_assets"):
    _adaptation_tool(_view_name, "读取改编节点详情、时点状态或关联资产；不读取提取资料卡", _ADAPT_NODE | {
        "cut": {"type": ["string", "null"], "description": "改编阅读切点ID，省略为当前"}})
_adaptation_tool("sync_adaptation", "增量同步提取到改编；保留改编编辑，记录冲突", _ADAPT_REV, writes=True)
_adaptation_tool("edit_adaptation_node", "新增或编辑改编节点；使用独立sort_id排序，不修改提取", _ADAPT_REV | _ADAPT_MOVE | {
    "node_id": {"type": ["string", "null"], "description": "省略或null为新增；已有节点传改编ID"}, "changes": {"type": "object", "required": True, "description": "节点当前字段，如name/node_type/events/participants；point_id指定出场时点；新物品initial_holder须明确节点ID、无或未知；id/sort_id由程序分配"}}, writes=True)
for _operation in ("copy_adaptation_node", "move_adaptation_node"):
    _adaptation_tool(_operation, "复制或移动独立改编节点，重排改编顺序", _ADAPT_REV | _ADAPT_NODE | _ADAPT_MOVE, writes=True)
_adaptation_tool("delete_adaptation_node", "显式物理删除改编节点及包含子树并清理引用；保留剧本正文，返回失效时间段诊断", _ADAPT_NODE | {
    "expected_revision": {"type": "integer", "required": True}, "reason": {"type": "string", "required": True}}, writes=True)
_adaptation_tool("suppress_adaptation_node", "屏蔽或恢复改编节点及整个包含子树，不影响提取", _ADAPT_REV | _ADAPT_NODE | {
    "suppressed": {"type": "boolean"}}, writes=True)
_adaptation_tool("set_adaptation_property", "在改编时间点新增、更改或失效属性", _ADAPT_REV | _ADAPT_NODE | {
    "property_id": {"type": "string", "required": True}, "value": {},
    "event_id": {"type": ["string", "null"], "description": "改编生效时点；失效操作指定结束时点"}, "record_id": {"type": ["string", "null"], "description": "更改或失效的已有属性取值ID；省略新增"},
    "expire": {"type": "boolean"}}, writes=True)
_adaptation_tool("list_assets", "按类别、角色与改编时间点读取视觉设定卡", {
    "category": {"type": "string", "enum": ["appearance", "item", "location", "material"], "required": True},
    "moment": {"type": "string"}, "node_id": {"type": "string"}, "domain": _ASSET_DOMAIN})
_adaptation_tool("get_asset", "读取视觉设定、图片、参考图和提示词", {
    "asset_id": {"type": "string", "required": True}})
_adaptation_tool("save_asset", "新增或编辑独立视觉资产；参考图复制保存，原件保留", {
    "payload": _ASSET_PAYLOAD, "expected_revision": {"type": "integer", "description": "资产独立修订号，来自list_assets/get_asset"}}, writes=True)
_adaptation_tool("delete_asset", "删除独立视觉设定卡，保留原始附件", {
    "asset_id": {"type": "string", "required": True}, "expected_revision": {"type": "integer"}}, writes=True)
_adaptation_tool("asset_moments", "读取按改编顺序排列的资产时间点", {"domain": _ASSET_DOMAIN})


def _extraction_settings(ops, params, *, write=False):
    settings = ops.load_project_settings()
    if write:
        granularity = params["granularity"]
        if not isinstance(granularity, str) or granularity not in {"standard", "fine"}:
            raise ToolError("提取粒度须为standard或fine")
        settings = ops.save_project_settings(settings | {"extraction_granularity": granularity})
    return {"extraction_granularity": settings["extraction_granularity"]}


_register(Tool("get_extraction_settings", "读取工程提取粒度，默认fine精细；不返回供应商密钥", {},
               lambda ops, args: _extraction_settings(ops, args)))
_register(Tool("set_extraction_granularity", "设置工程提取粒度，保留当前模型配置和已提取数据", {
    "granularity": {"type": "string", "enum": ["standard", "fine"], "required": True}},
    lambda ops, args: _extraction_settings(ops, args, write=True), writes=True))


_register_screenplay_tools(Tool, _register, _validate_adaptation_argument)


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
    if params is not None and not isinstance(params, dict):
        raise ToolError("参数须为 JSON 对象")
    args = dict(params or {})
    unknown = set(args) - set(tool.params)
    if unknown:
        raise ToolError("不认识的参数：" + "、".join(sorted(unknown)))
    for key, spec in tool.params.items():
        if spec.get("required") and args.get(key) in (None, ""):
            raise ToolError(f"缺少参数：{key}")
    if tool.scope == "workspace":
        return tool.handler(None, args)
    if project in (None, ""):
        raise ToolError("请提供 --project（.nproj 工程入口文件）")
    path = Path(project)
    if not path.exists():
        raise ToolError(f"工程不存在：{path}")
    # Public tools accept the private entry file; database paths remain internal.
    ops = open_graph_project(path)
    try:
        from .tool_fingerprint_refs import decode_tool_parameters, encode_tool_result
        try:
            resolved = decode_tool_parameters(ops, args)
            from .tool_execution_context import current_tool_execution_metadata
            for key, value in current_tool_execution_metadata().items():
                if key in tool.params:
                    resolved[key] = value
            result = tool.handler(ops, resolved)
            return encode_tool_result(ops, result)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
    finally:
        close = getattr(ops, "close", None)
        if callable(close):
            close()


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
