"""Names and evidence for human review; private IDs remain operation keys only."""

from __future__ import annotations

import copy
import json
import re

from .property_labels import label_for

_KINDS = {"node": "节点", "context": "语境", "timeline": "时间轴", "source_order": "披露顺序"}
_NODES = {"person": "人物", "item": "物品", "location": "地点", "event": "事件",
          "scene": "场景", "storyline": "大事件", "group": "群体", "other": "其他实体"}
_CONTEXTS = {"canon": "世界叙述", "branch": "分支", "hypothesis": "假设", "viewpoint": "人物视角"}
_SCOPES = {"world": "世界", "cognition": "认知", "claim": "言说"}
_PRIVATE = re.compile(
    r"\b(?:val|ret|candidate|change|source|src|node|event|person|context|timeline|order|record|property|batch|run|extract)_[0-9a-f]{8,}\b"
    r"|\b(?=[0-9a-f]*[a-f])(?:[0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})\b"
    r"|\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I,
)


class _Labels:
    def __init__(self, snapshot: dict, records: list[dict], detail: dict | None = None):
        detail = detail or {}
        objects = list(snapshot.get("objects") or []) + list(snapshot.get("nodes") or [])
        objects += list(detail.get("graph_objects") or detail.get("objects") or [])
        self.objects = {str(row["id"]): row for row in objects if row.get("id")}
        self.records = {str(row["id"]): row for row in records + list(detail.get("property_records") or []) if row.get("id")}
        self.refs = {key: str(row.get("name") or row.get("label") or _KINDS.get(row.get("kind"), "未命名对象"))
                     for key, row in self.objects.items()}
        self.refs.update({str(row["id"]): str(row.get("title") or "未命名原文")
                          for row in snapshot.get("sources") or [] if row.get("id")})
        for key, row in self.records.items():
            if row.get("kind") == "property_definition":
                self.refs[key] = str(row.get("label") or label_for(key))
        for key, row in self.records.items():
            if row.get("kind") == "property_value":
                self.refs[key] = self.property_value(row)

    def text(self, value) -> str:
        text = str(value or "")
        if text in self.refs and not text.isdecimal():
            text = self.refs[text]
        for key in sorted(self.refs, key=len, reverse=True):
            if key == self.refs[key] or key.isdecimal():
                continue
            # Avoid replacing a short numeric ID inside ordinary prose or dates.
            if len(key) >= 8:
                text = text.replace(key, self.refs[key])
            elif not key.isdecimal():
                text = re.sub(r"(?<![\w])" + re.escape(key) + r"(?![\w])", lambda _, replacement=self.refs[key]: replacement, text)
        return _PRIVATE.sub("内部引用", text)

    def ref(self, key, missing="未找到关联对象") -> str:
        return self.text(self.refs.get(str(key or ""), missing))

    def value(self, row: dict) -> str:
        if row.get("value_node"):
            return self.ref(row["value_node"])
        value = row.get("value")
        # Literal business values are evidence, not references to sanitize.
        # Entity references have their own explicit value_node field above.
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def property_value(self, row: dict) -> str:
        node = self.ref(row.get("node_id"), "未找到所属节点")
        aspect = self.text(label_for(str(row.get("property_id") or "未命名属性")))
        return f"{node} · {aspect} = {self.value(row)}"

    def evidence(self, row: dict) -> str:
        evidence = row.get("evidence") or {}
        quote = evidence.get("quote") if isinstance(evidence, dict) else ""
        return f"原文：{quote}" if quote else ""


def candidate_rows(snapshot: dict, detail: dict, records: list[dict] | None = None) -> list[dict]:
    labels = _Labels(snapshot, list(records or []), detail)
    rows = []
    for obj in detail.get("graph_objects") or detail.get("objects") or []:
        kind = obj.get("kind")
        if kind == "node":
            name = labels.text(obj.get("name") or "未命名节点")
            extra = labels.text(obj.get("identity_note") or obj.get("subtype") or "")
        elif kind == "source_order":
            name = labels.ref(obj.get("source_id"), "未找到原文")
            extra = f"阅读顺序：{obj.get('ordinal', '未指定')}"
        else:
            name = labels.text(obj.get("label") or _KINDS.get(kind, "未命名对象"))
            extra = labels.text(_CONTEXTS.get(obj.get("context_type"), obj.get("calendar") or ""))
        rows.append({"id": obj.get("id") or "", "kind": _NODES.get(obj.get("node_type"), _KINDS.get(kind, "对象")),
                     "label": name, "extra": extra})
    for record in detail.get("property_records") or []:
        kind = record.get("kind")
        if kind == "property_value":
            event = labels.ref(record.get("event_id"), "事件未指定")
            extra = " · ".join(filter(None, [_SCOPES.get(record.get("scope") or "world", "世界"),
                                              "发生于：" + event, labels.evidence(record)]))
            rows.append({"id": record.get("id") or "", "kind": "属性", "label": labels.property_value(record), "extra": extra})
        elif kind == "property_retraction":
            target = labels.records.get(str(record.get("target_id") or ""))
            name = labels.property_value(target) if target and target.get("kind") == "property_value" else "原记录未找到，无法显示原值"
            extra = ["从「" + labels.ref(record.get("event_id"), "事件未指定") + "」起失效"]
            if record.get("reason"):
                extra.append("原因：" + labels.text(record["reason"]))
            quote = labels.evidence(record) or labels.evidence(target or {})
            if quote:
                extra.append(quote)
            rows.append({"id": record.get("id") or "", "kind": "失效", "label": name, "extra": " · ".join(extra)})
        elif kind == "property_definition":
            rows.append({"id": record.get("id") or "", "kind": "属性定义",
                         "label": labels.text(record.get("label") or label_for(str(record.get("id") or "未命名属性"))),
                         "extra": ""})
    states = {item["id"]: item["state"] for item in detail.get("application_state") or []}
    for row in rows:
        state = states.get(row["id"], "pending")
        row["application_state"] = state
        if state == "applied":
            row["extra"] = "已处理，不再重复写入 · " + row["extra"]
        elif state == "conflict":
            row["extra"] = "与正式资料冲突，未作修改 · " + row["extra"]
    return rows


def humanize_report(snapshot: dict, records: list[dict], report):
    """Resolve references inside display text while preserving IDs used by actions."""
    labels = _Labels(snapshot, records)
    result = copy.deepcopy(report)
    fields = {"message", "suggestion", "description", "label", "node_name", "event_name", "reason"}

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in fields and isinstance(item, str):
                    value[key] = labels.text(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return result
