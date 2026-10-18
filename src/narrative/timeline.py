"""Deterministic partial event order and conservative state projection.

Numeric times are user supplied relative minutes, never parsed from prose. A display
order is not a claim that unordered events happened in that order.
"""

from math import isfinite
from typing import Literal

from .models import Diagnostic


def _diagnostic(
    rule,
    event_id,
    message,
    verdict: Literal["consistent", "conflict", "insufficient", "unsupported"] = "insufficient",
    severity: Literal["error", "warning", "info"] = "info",
):
    return Diagnostic(
        rule_id=rule,
        object_id=event_id,
        message=message,
        verdict=verdict,
        severity=severity,
        coverage="v0.2 事件先后与移动／交接状态；不推断文本时间或文学因果",
    ).model_dump()


def _resource(event):
    if event["action"] == "transfer" and event.get("item_id"):
        return "holders", event["item_id"]
    if event["action"] == "move" and event.get("actor_id"):
        return "locations", event["actor_id"]
    return None


def _bounds(event):
    if event.get("time_kind") not in ("point", "interval"):
        return None
    start = event.get("time_start")
    if start is None:
        return None
    return start, event.get("time_end") if event.get("time_end") is not None else start


def _graph(events):
    active = {e["id"]: e for e in events if e.get("status", "accepted") == "accepted"}
    predecessors: dict[str, set[str]] = {eid: set() for eid in active}
    diagnostics = []
    for eid, event in active.items():
        anchor = event.get("after_event_id")
        if anchor:
            if anchor not in active:
                diagnostics.append(
                    _diagnostic(
                        "TIME_REF", eid, "先行事件不存在或不是有效事件", "conflict", "error"
                    )
                )
            else:
                predecessors[eid].add(anchor)
                left, right = _bounds(active[anchor]), _bounds(event)
                if left and right and left[1] >= right[0]:
                    diagnostics.append(
                        _diagnostic(
                            "TIME_ORDER", eid, "指定的先后关系与时间坐标矛盾", "conflict", "error"
                        )
                    )
    for left_id, left in active.items():
        left_time = _bounds(left)
        if left_time is None:
            continue
        for right_id, right in active.items():
            right_time = _bounds(right)
            if right_time is not None and left_time[1] < right_time[0]:
                predecessors[right_id].add(left_id)
    pending = {eid: set(preds) for eid, preds in predecessors.items()}
    ordered = []
    ancestors: dict[str, set[str]] = {}
    while pending:
        ready = sorted(
            (eid for eid, preds in pending.items() if not preds),
            key=lambda eid: (_bounds(active[eid]) or (float("inf"), 0), eid),
        )
        if not ready:
            for eid in pending:
                diagnostics.append(
                    _diagnostic(
                        "TIME_CYCLE", eid, "事件先后关系成环，无法计算状态", "conflict", "error"
                    )
                )
            break
        for eid in ready:
            ancestors[eid] = set(predecessors[eid])
            for parent in predecessors[eid]:
                ancestors[eid].update(ancestors[parent])
            ordered.append(eid)
            del pending[eid]
        for preds in pending.values():
            preds.difference_update(ready)
    for index, left_id in enumerate(ordered):
        left_resource = _resource(active[left_id])
        if left_resource is None:
            continue
        for right_id in ordered[index + 1 :]:
            if (
                left_resource == _resource(active[right_id])
                and left_id not in ancestors[right_id]
                and right_id not in ancestors[left_id]
            ):
                diagnostics.append(
                    _diagnostic(
                        "TIME_AMBIGUOUS",
                        right_id,
                        f"与事件 {left_id} 影响同一状态，但先后未确定（同时、重叠或时间未知）",
                        severity="warning",
                    )
                )
    return active, predecessors, ancestors, ordered, diagnostics


def _evaluate(events):
    active, predecessors, ancestors, ordered, diagnostics = _graph(events)
    writes: dict[str, tuple[tuple[str, str], str]] = {}

    def value_at(resource, scope):
        relevant = [eid for eid in scope if eid in writes and writes[eid][0] == resource]
        latest = [eid for eid in relevant if not any(eid in ancestors[other] for other in relevant)]
        if len(latest) != 1:
            return None
        return writes[latest[0]][1]

    for eid in ordered:
        event = active[eid]
        resource = _resource(event)
        if resource is None:
            continue
        if event["action"] == "transfer":
            holder = value_at(resource, ancestors[eid])
            unordered = any(
                other != eid
                and _resource(other_event) == resource
                and other not in ancestors[eid]
                and eid not in ancestors.get(other, set())
                for other, other_event in active.items()
            )
            if unordered:
                holder = None
            if holder is not None and holder != event.get("actor_id"):
                diagnostics.append(
                    _diagnostic(
                        "STATE_PRECONDITION",
                        eid,
                        f"交出者不是已知持有人（当前持有人 {holder}），该交接不能应用",
                        "conflict",
                        "error",
                    )
                )
                continue
            if holder is None:
                diagnostics.append(
                    _diagnostic(
                        "STATE_UNKNOWN", eid, "交接前持有人未知；不能据这次交接反推此前持有人"
                    )
                )
            if event.get("target_id"):
                writes[eid] = (resource, event["target_id"])
        elif event.get("location_id"):
            writes[eid] = (resource, event["location_id"])
    return active, predecessors, ancestors, ordered, diagnostics, writes, value_at


def build_timeline(events: list[dict], entities: list[dict], order: str = "story") -> dict:
    if order not in ("story", "disclosure"):
        raise ValueError("时间轴顺序须为 story 或 disclosure")
    active, predecessors, _, ordered, diagnostics, _, _ = _evaluate(events)
    names = {e["id"]: e["name"] for e in entities}
    rank = {eid: index for index, eid in enumerate(ordered)}
    if order == "disclosure":
        displayed = sorted(
            events,
            key=lambda e: (
                e.get("chapter_order", 0),
                e.get("disclosure_position") is None,
                e.get("disclosure_position") or 0,
                e["id"],
            ),
        )
    else:
        displayed = sorted(events, key=lambda e: (rank.get(e["id"], len(rank)), e["id"]))
    rows = []
    for index, event in enumerate(displayed):
        kind = event.get("time_kind", "unknown")
        bounds = _bounds(event)
        label = (
            str(bounds[0])
            if kind == "point" and bounds
            else (
                f"{bounds[0]}–{bounds[1]}"
                if bounds
                else "先行事件之后"
                if kind == "relative"
                else "时间未定"
            )
        )
        rows.append(
            event
            | {
                "labels": {
                    field: names.get(event.get(field), event.get(field))
                    for field in ("actor_id", "target_id", "item_id", "location_id")
                },
                "order_index": index,
                "phase": "已定坐标"
                if bounds
                else "仅相对顺序"
                if kind == "relative"
                else "时间未定",
                "time_label": label,
                "predecessors": sorted(predecessors.get(event["id"], set())),
                "active": event["id"] in active,
            }
        )
    return {"rows": rows, "diagnostics": diagnostics}


def state_at(events: list[dict], entities: list[dict], event_id: str) -> dict:
    selected = next((event for event in events if event["id"] == event_id), None)
    if selected is None:
        raise ValueError("事件不存在")
    excluded = selected.get("status", "accepted") != "accepted"
    if excluded:
        # A disputed event is a query position, never a state-changing action.
        events = [
            event
            if event["id"] != event_id
            else event | {"status": "accepted", "action": "observe"}
            for event in events
        ]
    active, _, ancestors, _, diagnostics, writes, value_at = _evaluate(events)
    if excluded:
        diagnostics.append(
            _diagnostic(
                "STATE_DISPUTED", event_id, "有争议或已取代事件不应用状态变化", severity="warning"
            )
        )
    empty: dict[str, dict[str, str | None]] = {
        "holders": {e["id"]: None for e in entities if e["entity_type"] == "item"},
        "locations": {e["id"]: None for e in entities if e["entity_type"] == "person"},
    }
    before = {kind: dict(values) for kind, values in empty.items()}
    after = {kind: dict(values) for kind, values in empty.items()}
    if event_id not in ancestors:
        return {"before": before, "after": after, "diagnostics": diagnostics}
    for kind, values in before.items():
        for ref in values:
            resource = kind, ref
            values[ref] = value_at(resource, ancestors[event_id])
            after[kind][ref] = value_at(resource, ancestors[event_id] | {event_id})
            # Unordered events may occur on either side of the selected event.
            if any(
                eid != event_id
                and _resource(event) == resource
                and eid not in ancestors[event_id]
                and event_id not in ancestors.get(eid, set())
                for eid, event in active.items()
            ):
                values[ref] = after[kind][ref] = None
    return {
        "before": before,
        "after": after,
        "diagnostics": diagnostics,
        "applied": event_id in writes,
    }


def state_at_time(
    events: list[dict], entities: list[dict], minute: float, boundary: str = "after"
) -> dict:
    """Project completed effects at a numeric cut without inventing an event order.

    Relative events can lie on either side of the cut. A numeric successor's start
    proves that its ancestors have completed; a numeric ancestor's end can prove
    that its descendants are still in the future. Only an unambiguous completed
    last write establishes a value. An interval in progress has no completed effect.
    """
    try:
        query_minute = float(minute)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("查询时刻须为有限数值") from error
    if isinstance(minute, bool) or not isfinite(query_minute):
        raise ValueError("查询时刻须为有限数值")
    if boundary not in ("before", "after"):
        raise ValueError("查询边界须为 before 或 after")

    active, _, ancestors, _, diagnostics, writes, _ = _evaluate(events)
    completed: set[str] = set()
    future: set[str] = set()
    ongoing: set[str] = set()
    for eid, event in active.items():
        bounds = _bounds(event)
        if bounds is None:
            continue
        start, end = bounds
        if end < query_minute or (end == query_minute and boundary == "after"):
            completed.add(eid)
        elif start > query_minute or (start == query_minute and boundary == "before"):
            future.add(eid)
        else:
            ongoing.add(eid)
        # "After" is strict, so these deductions also hold exactly at a boundary.
        if start <= query_minute:
            completed.update(ancestors.get(eid, set()))
        if end >= query_minute:
            future.update(other for other in active if eid in ancestors.get(other, set()))

    invalid = {
        diagnostic["object_id"]
        for diagnostic in diagnostics
        if diagnostic["rule_id"] in ("TIME_REF", "TIME_ORDER", "TIME_CYCLE")
        and diagnostic["severity"] == "error"
    }
    # Broken temporal constraints cannot establish a state. Keep their possible
    # effects visible as uncertainty rather than silently discarding the evidence.
    uncertain_order = invalid | {
        eid for eid in active if ancestors.get(eid, set()).intersection(invalid)
    }
    completed.difference_update(uncertain_order)
    future.difference_update(uncertain_order)
    rejected = {
        diagnostic["object_id"]
        for diagnostic in diagnostics
        if diagnostic["rule_id"] == "STATE_PRECONDITION" and diagnostic["severity"] == "error"
    }
    possible = dict(writes)
    for eid in uncertain_order:
        if eid not in active or eid in rejected:
            continue
        event = active[eid]
        resource = _resource(event)
        value = (
            event.get("target_id") if event["action"] == "transfer" else event.get("location_id")
        )
        if resource and value:
            possible[eid] = (resource, value)
    # The timeline retains global diagnostics. A query shows only problems that
    # could already matter at its cut; definite future actions do not warn yet.
    diagnostics = [
        diagnostic for diagnostic in diagnostics if diagnostic.get("object_id") not in future
    ]

    values: dict[str, dict[str, str | None]] = {
        "holders": {entity["id"]: None for entity in entities if entity["entity_type"] == "item"},
        "locations": {
            entity["id"]: None for entity in entities if entity["entity_type"] == "person"
        },
    }
    evidence: dict[str, dict[str, list[dict]]] = {kind: {} for kind in values}
    reasons: dict[str, dict[str, str]] = {kind: {} for kind in values}
    for kind, resources in values.items():
        for ref in resources:
            relevant = {
                eid
                for eid, (resource, _) in possible.items()
                if resource == (kind, ref) and eid not in future
            }
            latest = sorted(
                eid
                for eid in relevant
                if not any(eid in ancestors.get(other, set()) for other in relevant)
            )
            supporting: list[dict] = []
            for eid in latest:
                for source in active[eid].get("evidence", []):
                    if source not in supporting:
                        supporting.append(source)
            evidence[kind][ref] = supporting
            if not latest:
                reasons[kind][ref] = "截至查询时刻尚无事件能确定该状态；不从未来事件反推"
            elif len(latest) == 1 and latest[0] in completed:
                eid = latest[0]
                resources[ref] = possible[eid][1]
                title = active[eid].get("title") or eid
                reasons[kind][ref] = f"已知由事件“{title}”的完成效果确定"
            else:
                if any(eid in ongoing for eid in latest):
                    reason = "正在区间内，完成效果尚未应用"
                elif any(eid in uncertain_order for eid in latest):
                    reason = "事件时间约束存在冲突，无法确定状态"
                else:
                    reason = "事件与查询时刻或彼此无确定先后，状态未知"
                titles = [active[eid].get("title") or eid for eid in latest]
                reasons[kind][ref] = f"{reason}（事件：{'、'.join(titles)}）"
                diagnostics.append(_diagnostic("STATE_TIME_UNKNOWN", ref, reasons[kind][ref]))
    return {
        "minute": query_minute,
        "boundary": boundary,
        **values,
        "evidence": evidence,
        "reasons": reasons,
        "diagnostics": diagnostics,
    }
