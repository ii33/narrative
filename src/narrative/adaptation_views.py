"""Read-only BrowsePage projections on the independent adaptation order.

Source disclosure/evidence remain provenance. They never determine adaptation
visibility, temporal validity, related membership or ordering.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from collections import Counter
from contextlib import closing

from .browse_view import GRANULARITIES, LABELS, MOMENT_LEVELS, matches
from .property_labels import label_for

MOMENTS = set(LABELS)


class _View:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.nodes = {node["id"]: node for node in snapshot["nodes"]}
        self.ordered = sorted(self.nodes.values(), key=lambda node: node["sort_id"])
        self.definitions = {row["id"]: row for row in snapshot["properties"]
                            if row.get("kind") == "property_definition"}
        self.records = [row for row in snapshot["properties"] if row.get("kind") == "property_value"]
        self.parents = {}
        for node in self.ordered:
            for child in node.get("events", []):
                self.parents.setdefault(child, []).append(node["id"])
        self.introductions = {node["id"]: node["sort_id"] for node in self.ordered}
        def introduce(identifier, anchor):
            if identifier in self.nodes and anchor in self.nodes and self.active(anchor):
                self.introductions[identifier] = min(self.introductions[identifier], self.rank(anchor))
        for node in self.ordered:
            if not self.active(node["id"]):
                continue
            if node["node_type"] in MOMENTS:
                for child in self.descendants(node["id"]):
                    introduce(node["id"], child)
            if node["node_type"] in {"event", "action"}:
                for participant in node.get("participants", []):
                    introduce(participant, node["id"])
        for row in self.records:
            if not self.active(row.get("node_id")):
                continue
            anchor = row.get("event_id")
            introduce(row.get("node_id"), anchor)
            for target in self.targets(row):
                introduce(target, anchor)
        # Authored introduction overrides inferred involvement, even if a
        # participant/reference was deliberately placed in an earlier event.
        for node in self.ordered:
            if node.get("point_id") in self.nodes:
                self.introductions[node["id"]] = self.rank(node["point_id"])

    def node(self, identifier):
        if identifier not in self.nodes:
            raise ValueError("改编节点不存在")
        return self.nodes[identifier]

    def active(self, identifier):
        node = self.nodes.get(identifier)
        return bool(node and not node.get("suppressed") and not node.get("retracted"))

    def rank(self, identifier):
        return self.node(identifier)["sort_id"]

    def introduction(self, node):
        return self.introductions[node["id"]]

    def visible(self, identifier, cutoff=None, context=None):
        node = self.nodes.get(identifier)
        return bool(self.active(identifier) and node is not None
                    and (context is None or node.get("context_id", "canon") == context)
                    and (not node.get("point_id") or self.active(node["point_id"]))
                    and (cutoff is None or self.introduction(node) <= cutoff))

    def descendants(self, identifier):
        found, queue = set(), list(self.node(identifier).get("events", []))
        while queue:
            child = queue.pop()
            if child in found or child not in self.nodes:
                continue
            found.add(child)
            queue.extend(self.nodes[child].get("events", []))
        return sorted(found, key=self.rank)

    def anchor(self, identifier):
        node = self.node(identifier)
        if node["node_type"] not in MOMENTS:
            raise ValueError("改编阅读位置须为叙事节点")
        if not self.active(identifier):
            raise ValueError("屏蔽节点不能作为有效阅读位置")
        members = [self.nodes[child] for child in self.descendants(identifier) if self.active(child)
                   and self.nodes[child]["node_type"] in {"event", "action"}]
        return max(members, key=lambda row: row["sort_id"]) if members else node

    def cut(self, identifier):
        if not identifier:
            return None
        node, anchor = self.node(identifier), self.anchor(identifier)
        return {"step_id": identifier, "step_name": node["name"], "step_kind": node["node_type"],
                "event_id": anchor["id"], "event_name": anchor["name"], "sort_id": anchor["sort_id"]}

    def ref(self, identifier, cutoff=None, context=None):
        if not self.visible(identifier, cutoff, context):
            return None
        node = self.nodes[identifier]
        return {key: node[key] for key in ("id", "name", "node_type", "sort_id")}

    def targets(self, row):
        refs = list(row.get("related_nodes") or [])
        if self.definitions.get(row.get("property_id"), {}).get("value_type") in {"node", "either"}:
            value = row.get("value")
            refs.extend(value if isinstance(value, list) else [value])
        return list(dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref in self.nodes))

    def record_visible(self, row, cutoff=None):
        owner = self.nodes.get(row.get("node_id"))
        if not owner or not self.visible(owner["id"], cutoff):
            return False
        context = owner.get("context_id", "canon")
        if row.get("holder_id") and not self.visible(row["holder_id"], cutoff, context):
            return False
        start, end = row.get("event_id"), row.get("end_event_id")
        if start and (not self.visible(start, cutoff, context)):
            return False
        if end and (end not in self.nodes or not self.active(end)):
            return False
        if cutoff is None:
            if end:
                return False
        elif end and self.rank(end) <= cutoff:
            return False
        value = row.get("value")
        typed = self.definitions.get(row.get("property_id"), {}).get("value_type") in {"node", "either"}
        if typed:
            values = value if isinstance(value, list) else [value]
            if any(isinstance(ref, str) and ref in self.nodes and not self.visible(ref, cutoff, context) for ref in values):
                return False
        return True

    def record_key(self, row):
        # Stable list order handles simultaneous values; IDs have no temporal meaning.
        return self.rank(row["event_id"]) if row.get("event_id") in self.nodes else -1

    def steps(self, kind):
        return [{"id": node["id"], "label_id": node["id"], "name": node["name"],
                 "kind": kind, "label": LABELS[kind], "sort_id": node["sort_id"], "start": None}
                for node in self.ordered if node["node_type"] == kind and self.active(node["id"])]

    def timeline(self):
        rows, placed = [], set()
        def emit(identifier, depth):
            if identifier in placed or not self.active(identifier):
                return
            node = self.nodes[identifier]
            if node["node_type"] not in MOMENTS:
                return
            placed.add(identifier)
            rows.append({"id": identifier, "label_id": identifier, "name": node["name"],
                         "node_type": node["node_type"], "sort_id": node["sort_id"], "depth": depth})
            for child in sorted(node.get("events", []), key=self.rank):
                emit(child, depth + 1)
        roots = [node for node in self.ordered if not any(self.active(parent) for parent in self.parents.get(node["id"], []))]
        for node in [*roots, *self.ordered]:
            emit(node["id"], 0)
        return rows

    def moments(self):
        levels = [dict(level) for level in MOMENT_LEVELS]
        ranks = {level["key"]: index for index, level in enumerate(levels)}
        rows, buckets = [], {}
        def bucket(kind):
            identifier = "~adaptation-orphan-" + kind
            if identifier not in buckets:
                rank = ranks[kind]
                parent = bucket(levels[rank - 1]["key"]) if rank else ""
                buckets[identifier] = {"id": identifier, "label_id": "", "name": "未归入" + LABELS[kind],
                    "level": kind, "parent_id": parent, "rank": rank, "kind_label": LABELS[kind],
                    "summary": "", "synthetic": True}
            return identifier
        for node in self.ordered:
            kind = node["node_type"]
            if kind not in MOMENTS or not self.active(node["id"]):
                continue
            parent, rank = "", ranks[kind]
            if rank:
                expected = levels[rank - 1]["key"]
                queue, seen = list(self.parents.get(node["id"], [])), set()
                while queue:
                    candidate = queue.pop(0)
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    if self.active(candidate) and self.nodes[candidate]["node_type"] == expected:
                        parent = candidate
                        break
                    queue.extend(self.parents.get(candidate, []))
                parent = parent or bucket(expected)
            rows.append({"id": node["id"], "label_id": node["id"], "name": node["name"],
                         "level": kind, "parent_id": parent, "rank": rank, "kind_label": LABELS[kind],
                         "summary": "", "synthetic": False, "sort_id": node["sort_id"]})
        return {"levels": levels if rows else [], "nodes": rows + list(buckets.values())}

    def involvement(self, identifier, cutoff=None):
        owner = self.node(identifier)
        result = {"people": [], "locations": []}
        if owner["node_type"] not in MOMENTS or not self.visible(identifier, cutoff):
            return result
        context = owner.get("context_id", "canon")
        members = [child for child in [identifier, *self.descendants(identifier)]
                   if self.nodes[child]["node_type"] in {"event", "action"} and self.visible(child, cutoff, context)]
        found = {}
        def add(target, event, record=None):
            ref = self.ref(target, cutoff, context)
            if not ref or ref["node_type"] not in {"person", "location"}:
                return
            row = found.setdefault(target, ref | {"event_ids": [], "record_ids": []})
            if event not in row["event_ids"]:
                row["event_ids"].append(event)
            if record and record not in row["record_ids"]:
                row["record_ids"].append(record)
        for event in members:
            for target in self.nodes[event].get("participants", []):
                add(target, event)
        for row in self.records:
            if row.get("node_id") not in members or row.get("scope", "world") != "world" or not self.record_visible(row, cutoff):
                continue
            for target in self.targets(row):
                add(target, row["node_id"], row["id"])
        for row in sorted(found.values(), key=lambda row: (min(self.rank(event) for event in row["event_ids"]), row["sort_id"])):
            row["event_ids"].sort(key=self.rank)
            result["people" if row["node_type"] == "person" else "locations"].append(row)
        return result

    def related(self, identifier, cutoff):
        owner = self.node(identifier)
        context = owner.get("context_id", "canon")
        own = {identifier, *self.descendants(identifier)}
        found, queue = set(own), list(self.parents.get(identifier, []))
        while queue:
            parent = queue.pop()
            if parent in found:
                continue
            found.add(parent)
            queue.extend(self.parents.get(parent, []))
        for child in own:
            if self.visible(child, cutoff, context):
                found.update(self.nodes[child].get("participants", []))
        structural = {child for child in found if self.nodes.get(child, {}).get("node_type") != "person"}
        for row in self.records:
            if row.get("node_id") in structural and row.get("scope", "world") == "world" and self.record_visible(row, cutoff):
                found.update(self.targets(row))
        return {child for child in found if self.visible(child, cutoff, context)}

    def decorate_record(self, row, cutoff=None):
        owner = self.nodes[row["node_id"]]
        context = owner.get("context_id", "canon")
        definition = self.definitions.get(row["property_id"], {})
        value = row.get("value")
        value_node = self.ref(value, cutoff, context) if isinstance(value, str) and definition.get("value_type") in {"node", "either"} else None
        return copy.deepcopy(row) | {
            "valid": True, "is_latest": False, "value_node": value_node,
            "event_name": self.nodes.get(row.get("event_id"), {}).get("name", ""),
            "holder_name": self.nodes.get(row.get("holder_id"), {}).get("name", ""),
            "scope": row.get("scope", "world"), "category": row.get("category") or "其他",
            "category_source": "explicit", "property_label": label_for(row["property_id"]),
            "related": [ref for target in self.targets(row) if (ref := self.ref(target, cutoff, context))],
        }

    def properties(self, identifier, cut=None, *, derived=False):
        owner = self.node(identifier)
        boundary = self.cut(cut)
        cutoff = boundary["sort_id"] if boundary else None
        records = [row for row in self.records if row.get("node_id") == identifier and not (
            row.get("scope", "world") != "world" and row.get("holder_id") not in (None, identifier))]
        if derived and owner["node_type"] in {"storyline", "big_event"}:
            progress = {"storyline": ("big_event", "storyline_progress"), "big_event": ("scene", "big_event_progress")}[owner["node_type"]]
            borrowed = [row for row in self.records if row.get("node_id") in self.descendants(identifier)
                        and self.nodes[row["node_id"]]["node_type"] == progress[0]
                        and row.get("property_id") == progress[1] and self.record_visible(row, cutoff)]
            if borrowed:
                source = max(borrowed, key=self.record_key)
                records.append(source | {"derived": True, "derived_from": source["node_id"]})
        later = sum(1 for row in records if cutoff is not None and self.record_key(row) > cutoff)
        groups = {}
        for row in sorted(records, key=self.record_key):
            if not self.visible(identifier, cutoff) or not self.record_visible(row, cutoff):
                continue
            key = row["property_id"], row.get("category") or "其他", row.get("scope", "world"), row.get("holder_id")
            groups.setdefault(key, []).append(self.decorate_record(row, cutoff))
        items = []
        for (property_id, category, scope, holder_id), values in groups.items():
            latest = values[-1]
            latest["is_latest"] = True
            definition = self.definitions.get(property_id, {})
            item = {"property_id": property_id, "property_ids": [property_id], "aspect": property_id,
                    "label": label_for(property_id), "category": category, "category_source": "explicit",
                    "other_categories": [], "scope": scope, "scopes": [scope], "holder_id": holder_id,
                    "holder_name": latest["holder_name"], "value_type": definition.get("value_type", "any"),
                    "value": latest.get("value"), "value_node": latest["value_node"], "event_id": latest.get("event_id"),
                    "start": None, "disclosed_at": latest["event_name"], "is_current": True,
                    "record_count": len(values), "later_count": 0, "records": values}
            if latest.get("derived"):
                item.update(derived=True, from_scene=self.ref(latest["derived_from"], cutoff))
            items.append(item)
        categories = list(dict.fromkeys(item["category"] for item in items))
        inbound, claims, heard = [], [], []
        for row in sorted(self.records, key=self.record_key):
            if not self.visible(identifier, cutoff) or not self.record_visible(row, cutoff):
                continue
            subject = row["node_id"]
            decorated = self.decorate_record(row, cutoff)
            generic = decorated | {"record_id": row["id"], "node_id": subject,
                                   "node_name": self.nodes[subject]["name"], "node_type": self.nodes[subject]["node_type"]}
            if subject != identifier and identifier in self.targets(row):
                inbound.append(generic)
            if subject != identifier and row.get("holder_id") == identifier and row.get("scope", "world") != "world":
                claims.append(generic)
            if subject == identifier and row.get("holder_id") not in (None, identifier) and row.get("scope", "world") != "world":
                heard.append(generic)
        children = [copy.deepcopy(self.nodes[child]) | {"label_id": child,
                    "type_label": LABELS.get(self.nodes[child]["node_type"], "节点"), "start": None}
                    for child in sorted(owner.get("events", []), key=self.rank) if self.visible(child, cutoff, owner.get("context_id", "canon"))]
        return {"node_id": identifier, "node_type": owner["node_type"], "revision": self.snapshot["revision"],
                "vocabulary": categories, "categories": [{"key": category, "count": len(group := [item for item in items if item["category"] == category]),
                    "record_count": sum(item["record_count"] for item in group), "items": group} for category in categories],
                "properties": items, "inbound": inbound, "claims": claims, "heard": heard, "children": children,
                "involvement": self.involvement(identifier, cutoff), "cut": cut or None, "later_count": later,
                "suppressed": bool(owner.get("suppressed")), "domain": "adaptation"}


class AdaptationViewsMixin:
    def _adaptation_view(self):
        method = "adaptation_snapshot"
        return _View(getattr(self, method)())

    def adaptation_browse_view(self, *, granularity="scene", cut_id="", node_type="", query="",
                              related_only=False, time_filter=False, include_suppressed=False) -> dict:
        view = self._adaptation_view()
        cut = view.cut(cut_id)
        cutoff = cut["sort_id"] if cut else None
        related = view.related(cut_id, cutoff) if related_only and cut_id else None
        candidates = [node for node in view.ordered if
                      (view.active(node["id"]) or include_suppressed and node.get("suppressed"))
                      and (not time_filter or cutoff is None or view.introduction(node) <= cutoff)
                      and (related is None or node["id"] in related) and matches(node, query.strip())]
        counts = Counter(node["node_type"] for node in candidates)
        shown = [copy.deepcopy(node) | {"label_id": node["id"]} for node in candidates if not node_type or node["node_type"] == node_type]
        kind = granularity if granularity in GRANULARITIES else "scene"
        alt = {}
        for target in GRANULARITIES:
            options = [node for node in view.ordered if node["node_type"] == target and view.active(node["id"])]
            exact = next((node for node in options if cut and view.anchor(node["id"])["id"] == cut["event_id"]), None)
            parents = {parent for node in options if cut_id in view.descendants(node["id"]) for parent in [node["id"]]}
            alt[target] = exact["id"] if exact else next((node["id"] for node in options if node["id"] in parents), "")
        return {"revision": view.snapshot["revision"], "source_revision": view.snapshot.get("source_revision"),
                "domain": "adaptation", "granularity": kind, "nodes": shown, "counts": dict(counts), "total": len(shown),
                "all_nodes": copy.deepcopy(view.ordered),
                "project_total": len(view.nodes), "steps": view.steps(kind), "events": view.steps("event"),
                "anchor_steps": sorted(view.steps("event") + view.steps("action"), key=lambda row: row["sort_id"]),
                "timeline": view.timeline(), "moments": view.moments(), "name_index": {node["id"]: node["name"] for node in view.ordered},
                "cut": cut, "cut_alt": alt, "related_only": bool(related_only), "related": sorted(related, key=view.rank) if related is not None else [],
                "time_filter": bool(time_filter), "include_suppressed": bool(include_suppressed),
                "involvement": view.involvement(cut_id, cutoff) if cut_id else {"people": [], "locations": []}}

    def adaptation_node_properties(self, node_id: str, cut=None) -> dict:
        return self._adaptation_view().properties(node_id, cut, derived=True)

    def adaptation_node_editor(self, node_id: str, cut=None) -> dict:
        view = self._adaptation_view()
        summary = view.properties(node_id, cut)
        return summary | {"node": copy.deepcopy(view.node(node_id)), "events": view.steps("event"),
                          "anchor_steps": sorted(view.steps("event") + view.steps("action"), key=lambda row: row["sort_id"]),
                          "at_cut": bool(cut), "cut_name": view.nodes.get(cut, {}).get("name", "")}

    def adaptation_node_detail(self, node_id: str, cut=None) -> dict:
        view = self._adaptation_view()
        node = view.node(node_id)
        boundary = view.cut(cut)
        cutoff = boundary["sort_id"] if boundary else None
        related = view.related(node_id, cutoff) if node["node_type"] in MOMENTS else {
            target for row in view.records if row.get("node_id") == node_id and view.record_visible(row, cutoff)
            for target in view.targets(row) if view.visible(target, cutoff)}
        if not view.visible(node_id, cutoff):
            related = set()
        decorated_node = copy.deepcopy(node)
        context = node.get("context_id", "canon")
        for field in ("events", "participants", "predecessors", "successors"):
            decorated_node[field] = sorted((target for target in node.get(field, []) if view.visible(target, cutoff, context)), key=view.rank)
        return {"node": decorated_node | {"label_id": node_id, "kind_label": LABELS.get(node["node_type"], "节点")},
                "revision": view.snapshot["revision"], "domain": "adaptation", "assertions": [],
                "related_nodes": [copy.deepcopy(view.nodes[target]) for target in sorted(related - {node_id}, key=view.rank)],
                "uncertainties": [], "pending_excluded": True, "span": None,
                "involvement": view.involvement(node_id, cutoff), "suppressed": bool(node.get("suppressed")),
                "visible_at_cut": view.visible(node_id, cutoff)}

    def adaptation_node_assets(self, node_id: str, cut=None) -> dict:
        view = self._adaptation_view()
        node = view.node(node_id)
        boundary = view.cut(cut)
        cutoff = boundary["sort_id"] if boundary else None
        moments = view.moments()
        result = {"node_id": node_id, "cut": cut or "", "cards": [], "hidden_count": 0, "diagnostics": [],
                  "moments": moments, "domain": "adaptation", "readonly": True}
        if not view.visible(node_id, cutoff):
            return result
        root_method = "_asset_root"
        root = getattr(self, root_method)()
        database = root / "assets.sqlite3"
        if not database.is_file():
            return result
        with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as db:
            cards = [json.loads(row[0]) for row in db.execute("SELECT data FROM assets ORDER BY rowid")]
        category = {"person": "appearance", "item": "item", "location": "location"}.get(node["node_type"])
        for card in cards:
            if card.get("category") != category or card.get("node_id") != node_id or card.get("domain") != "adaptation":
                continue
            start, end = card.get("valid_from"), card.get("valid_to")
            if any(point and (point not in view.nodes or not view.active(point)) for point in (start, end)) or (
                start and end and view.rank(start) >= view.rank(end)):
                result["diagnostics"].append({"asset_id": card["id"], "message": "资产时间区间无效，请在资产页调整"})
                result["hidden_count"] += 1
                continue
            if (cutoff is None and end) or (cutoff is not None and ((start and view.rank(start) > cutoff) or (end and view.rank(end) <= cutoff))):
                result["hidden_count"] += 1
                continue
            decorated = copy.deepcopy(card)
            for field in ("images", "reference_images"):
                decorated[field] = [image | {"path": str(root / image["file"])} for image in card.get(field, [])]
            result["cards"].append(decorated)
        return result
