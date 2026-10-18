"""Evidence-preserving state, knowledge and relation projections.

Only accepted records participate. Story time is supplied by the user: this
module never calls a model, parses prose, assumes an initial state, or promotes
someone's claim into a world fact. Missing and expired values stay unknown.
"""

from dataclasses import dataclass, field
from math import isfinite
from typing import Any

from .timeline import _bounds, _graph


def _items(snapshot: dict, key: str) -> list[dict]:
    value = snapshot.get(key, [])
    return list(value.values()) if isinstance(value, dict) else list(value)


def _evidence(*objects: dict) -> list[dict]:
    result: list[dict] = []
    for obj in objects:
        for evidence in obj.get("evidence", []):
            item = evidence.model_dump() if hasattr(evidence, "model_dump") else evidence
            if item not in result:
                result.append(item)
    return result


def _diagnostic(
    rule: str, obj: str, message: str, *, severity="info", verdict="insufficient", evidence=None
):
    return {
        "rule_id": rule,
        "severity": severity,
        "verdict": verdict,
        "object_id": obj,
        "message": message,
        "coverage": "显式故事时间、状态有效期、世界规则与人物认知获取路径；不推断文本外事实",
        "suggestion": "",
        "evidence": evidence or [],
        "time": None,
    }


def event_effects(event: dict) -> list[dict]:
    """Return explicit and action-derived effects without applying them."""
    action = event["action"]
    effects = list(event.get("effects", []))
    common = {"persistence": event.get("persistence"), "valid_until": event.get("valid_until")}
    if action == "move" and event.get("actor_id") and event.get("location_id"):
        effects.insert(
            0,
            common
            | {
                "subject_id": event["actor_id"],
                "property_id": "location",
                "value_entity_id": event["location_id"],
                "persistence": "until_changed",
            },
        )
    elif action == "transfer" and event.get("item_id") and event.get("target_id"):
        effects.insert(
            0,
            common
            | {
                "subject_id": event["item_id"],
                "property_id": "held_by",
                "value_entity_id": event["target_id"],
                "persistence": "until_changed",
            },
        )
    elif action in {"dress", "injure", "treat"}:
        subject = event.get("target_id") or event.get("actor_id")
        if subject:
            prop = "outfit" if action == "dress" else "injury:" + event.get("slot", "default")
            effect = common | {
                "subject_id": subject,
                "property_id": prop,
                "value": event.get("state_value"),
            }
            if action == "dress" and event.get("item_id") and event.get("state_value") is None:
                effect.update(value_entity_id=event["item_id"])
            elif action == "injure" and event.get("state_value") is None:
                effect["value"] = "受伤"
            elif action == "treat" and event.get("state_value") is None:
                # Treatment is an action, not proof of recovery. An explicit
                # false/severity value is required to establish an outcome.
                effect["operation"] = "clear"
            effects.insert(0, effect)
    return effects


@dataclass
class _Write:
    node: str
    subject: str
    property_id: str
    value: Any = None
    entity: str | None = None
    persistence: str = "unknown"
    end: float | None = None
    end_event: str | None = None
    clear: bool = False
    evidence: list[dict] = field(default_factory=list)
    label: str = ""
    object_id: str = ""

    @property
    def key(self):
        return self.subject, self.property_id


class _Projection:
    def __init__(
        self,
        snapshot: dict,
        minute: float,
        boundary: str,
        event_cut: str | None = None,
        *,
        all_events: bool = False,
    ):
        self.snapshot = snapshot
        self.global_diagnostics = all_events
        self.minute = minute
        self.boundary = boundary
        self.event_cut = event_cut
        self.entities = {obj["id"]: obj for obj in _items(snapshot, "entities")}
        self.assertions = {obj["id"]: obj for obj in _items(snapshot, "assertions")}
        self.records = [
            obj
            for obj in _items(snapshot, "records")
            if obj.get("status", "accepted") == "accepted"
        ]
        self.properties = {obj["id"]: obj for obj in self.records if obj["kind"] == "property"}
        self.events = [
            obj for obj in _items(snapshot, "events") if obj.get("status", "accepted") == "accepted"
        ]
        self.event_ids = {obj["id"] for obj in self.events}
        self.nodes = list(self.events)
        self.record_nodes: dict[str, str] = {}
        for obj in self.records:
            if obj["kind"] in {"state", "relation", "knowledge"}:
                self.record_nodes[obj["id"]] = self._add_record_node(obj)
        self.active, _, self.ancestors, self.ordered, self.diagnostics = _graph(self.nodes)
        for event in self.events:
            bounds = _bounds(event)
            if bounds is None:
                continue
            duration = bounds[1] - bounds[0]
            if (event.get("min_duration") is not None and duration < event["min_duration"]) or (
                event.get("max_duration") is not None and duration > event["max_duration"]
            ):
                self.diagnostics.append(
                    _diagnostic(
                        "TIME_DURATION",
                        event["id"],
                        "事件时长与最短／最长时长约束冲突，效果不能确定",
                        severity="error",
                        verdict="conflict",
                        evidence=_evidence(event),
                    )
                )
        self.invalid = {
            item["object_id"]
            for item in self.diagnostics
            if item["rule_id"] in {"TIME_REF", "TIME_ORDER", "TIME_CYCLE", "TIME_DURATION"}
            and item["severity"] == "error"
        }
        self.invalid.update(
            node for node in self.active if self.ancestors.get(node, set()) & self.invalid
        )
        self.completed, self.future, self.ongoing = self._phases()
        self.diagnostics = [
            item
            for item in self.diagnostics
            if self.global_diagnostics or item.get("object_id") not in self.future
        ]
        self.blocked: set[str] = set()
        self.writes: list[_Write] = []
        self.knowledge_entries = self._knowledge_entries()
        self._evaluate_events()

    def _add_record_node(self, obj: dict) -> str:
        scope = obj.get("validity") or {}
        anchor = scope.get("start_event_id") or (
            obj.get("event_id") if obj["kind"] == "knowledge" else None
        )
        if anchor in self.event_ids:
            return str(anchor)
        node_id = "$record:" + obj["id"]
        node = {
            "id": node_id,
            "title": obj["id"],
            "action": "observe",
            "status": "accepted",
            "time_kind": "unknown",
        }
        if scope.get("start") is not None:
            node.update(time_kind="point", time_start=scope["start"])
        elif anchor:
            node.update(time_kind="relative", after_event_id=anchor)
        self.nodes.append(node)
        return node_id

    def _phases(self):
        if self.event_cut:
            completed = set(self.ancestors.get(self.event_cut, set()))
            if self.boundary == "after":
                completed.add(self.event_cut)
            future = {
                node for node in self.active if self.event_cut in self.ancestors.get(node, set())
            }
            if self.boundary == "before":
                future.add(self.event_cut)
            completed.difference_update(self.invalid)
            future.difference_update(self.invalid)
            return completed, future, set()
        completed, future, ongoing = set(), set(), set()
        for node, event in self.active.items():
            bounds = _bounds(event)
            if bounds is None:
                continue
            start, end = bounds
            if end < self.minute or (end == self.minute and self.boundary == "after"):
                completed.add(node)
            elif start > self.minute or (start == self.minute and self.boundary == "before"):
                future.add(node)
            else:
                ongoing.add(node)
            if start <= self.minute:
                completed.update(self.ancestors.get(node, set()))
            if end >= self.minute:
                future.update(
                    other for other in self.active if node in self.ancestors.get(other, set())
                )
        completed.difference_update(self.invalid)
        future.difference_update(self.invalid)
        return completed, future, ongoing

    def _scope_status(
        self, scope: dict, *, node: str | None = None, timeless=False, cut: str | None = None
    ) -> str:
        """Resolve inclusive start/exclusive end on the selected boundary."""
        if node:
            if cut:
                if node == cut or cut in self.ancestors.get(node, set()):
                    return "future"
                if node not in self.ancestors.get(cut, set()):
                    return "unknown"
            elif node in self.future:
                return "future"
            elif node not in self.completed:
                return "unknown"
        start, end = scope.get("start"), scope.get("end")
        start_event, end_event = scope.get("start_event_id"), scope.get("end_event_id")
        if not node and not timeless and start is None and not start_event:
            return "unknown"
        event_cut = cut or self.event_cut
        if event_cut:
            if (
                start_event
                and start_event != event_cut
                and start_event not in self.ancestors.get(event_cut, set())
            ):
                return (
                    "future" if event_cut in self.ancestors.get(start_event, set()) else "unknown"
                )
            if end_event:
                if end_event in self.ancestors.get(event_cut, set()) or (
                    end_event == event_cut and not cut and self.boundary == "after"
                ):
                    return "expired"
                if end_event != event_cut and event_cut not in self.ancestors.get(end_event, set()):
                    return "unknown"
            # Numeric ends can be decided at numeric event cuts, but cannot be
            # attached to an otherwise unresolved relative position.
            bounds = _bounds(self.active.get(event_cut, {}))
            if bounds:
                point = bounds[0] if cut or self.boundary == "before" else bounds[1]
                before = bool(cut) or self.boundary == "before"
                if start is not None and (point < start or point == start and before):
                    return "future"
                if end is not None and (point > end or point == end and not before):
                    return "expired"
            elif (start is not None and not node) or end is not None:
                return "unknown"
        else:
            if start is not None and (
                self.minute < start or self.minute == start and self.boundary == "before"
            ):
                return "future"
            if end is not None and (
                self.minute > end or self.minute == end and self.boundary == "after"
            ):
                return "expired"
            if start_event and start_event not in self.completed:
                return "future" if start_event in self.future else "unknown"
            if end_event:
                if end_event in self.completed:
                    return "expired"
                if end_event not in self.future:
                    return "unknown"
        return "known"

    def _effects(self, event: dict) -> list[dict]:
        return event_effects(event)

    def _make_write(self, node: str, effect: dict, obj: dict) -> _Write:
        prop = effect["property_id"]
        definition = self.properties.get(prop, {})
        persistence = (
            effect.get("persistence")
            or definition.get("persistence")
            or ("until_changed" if prop in {"location", "held_by"} else "unknown")
        )
        if effect.get("valid_until") is not None:
            persistence = "bounded"
        return _Write(
            node=node,
            subject=effect["subject_id"],
            property_id=prop,
            value=effect.get("value"),
            entity=effect.get("value_entity_id"),
            persistence=persistence,
            end=effect.get("valid_until"),
            clear=effect.get("operation") == "clear",
            evidence=_evidence(obj),
            label=obj.get("title") or obj["id"],
            object_id=obj["id"],
        )

    def _valid_write(self, write: _Write) -> bool:
        definition = self.properties.get(write.property_id)
        message = None
        if write.subject not in self.entities:
            message = "状态主体不存在"
        elif write.entity is not None and write.entity not in self.entities:
            message = "状态引用的实体不存在"
        elif definition:
            subject_types = definition.get("subject_types", [])
            reference_type = definition.get("reference_type")
            value_type = definition["value_type"]
            if subject_types and self.entities[write.subject]["entity_type"] not in subject_types:
                message = "状态主体类型不符合属性定义"
            elif not write.clear:
                valid = {
                    "entity": write.entity is not None,
                    "boolean": type(write.value) is bool and write.entity is None,
                    "number": type(write.value) in (int, float) and write.entity is None,
                    "string": isinstance(write.value, str) and write.entity is None,
                }.get(value_type, False)
                if not valid:
                    message = "状态值类型不符合属性定义"
                elif (
                    reference_type
                    and write.entity
                    and self.entities[write.entity]["entity_type"] != reference_type
                ):
                    message = "状态实体值类型不符合属性定义"
        elif write.property_id.startswith("injury:") and not write.clear:
            if (
                self.entities[write.subject]["entity_type"] != "person"
                or not isinstance(write.value, str)
                or write.entity is not None
            ):
                message = "伤情须为人物的文字状态；治疗本身不证明康复"
        elif write.property_id in {"location", "held_by", "outfit"} and not write.clear:
            reference_type = {"location": "location", "held_by": "person", "outfit": "item"}[
                write.property_id
            ]
            subject_type = "item" if write.property_id == "held_by" else "person"
            if (
                self.entities[write.subject]["entity_type"] != subject_type
                or not write.entity
                or self.entities[write.entity]["entity_type"] != reference_type
            ):
                message = "内置状态的主体或实体值类型不匹配"
        elif write.property_id not in {
            "location",
            "held_by",
            "outfit",
        } and not write.property_id.startswith("injury:"):
            message = "状态属性未定义，不作自动投影"
        if message:
            self.diagnostics.append(
                _diagnostic(
                    "STATE_TYPE",
                    write.object_id,
                    message,
                    severity="error",
                    verdict="unsupported",
                    evidence=write.evidence,
                )
            )
            return False
        return True

    def _evaluate_events(self):
        for obj in self.records:
            if obj["kind"] != "state":
                continue
            scope = obj.get("validity") or {}
            effect = obj | {"valid_until": scope.get("end")}
            write = self._make_write(self.record_nodes[obj["id"]], effect, obj)
            write.end_event = scope.get("end_event_id")
            if write.end_event:
                write.persistence = "bounded"
            if self._valid_write(write):
                self.writes.append(write)
        # Register every possible effect before checking preconditions. An
        # unordered event may precede the event being checked even if its ID or
        # display position puts it later in a deterministic traversal.
        for event in self.events:
            for effect in self._effects(event):
                write = self._make_write(event["id"], effect, event)
                if self._valid_write(write):
                    self.writes.append(write)
        for eid in self.ordered:
            if eid not in self.event_ids:
                continue
            event = self.active[eid]
            conditions = list(event.get("preconditions", []))
            if event["action"] == "transfer" and event.get("actor_id") and event.get("item_id"):
                conditions.append(
                    {
                        "subject_id": event["item_id"],
                        "property_id": "held_by",
                        "operator": "eq",
                        "value_entity_id": event["actor_id"],
                    }
                )
            blocked = False
            for condition in conditions:
                value = self._state(condition["subject_id"], condition["property_id"], cut=eid)
                satisfied = self._condition(condition, value)
                if satisfied is False:
                    blocked = True
                    if self.global_diagnostics or eid not in self.future:
                        self.diagnostics.append(
                            _diagnostic(
                                "STATE_PRECONDITION",
                                eid,
                                "事件前置条件确定不满足，全部状态效果不应用",
                                severity="error",
                                verdict="conflict",
                                evidence=_evidence(event) + value["evidence"],
                            )
                        )
                elif satisfied is None and (self.global_diagnostics or eid not in self.future):
                    self.diagnostics.append(
                        _diagnostic(
                            "STATE_UNKNOWN"
                            if event["action"] == "transfer"
                            and condition["property_id"] == "held_by"
                            else "STATE_PRECONDITION_UNKNOWN",
                            eid,
                            "前置状态未知；保留事件记载的效果，不据此反推此前状态",
                            evidence=_evidence(event),
                        )
                    )
            for assertion_id in event.get("knowledge_used", []):
                knowledge = self._knowledge(event.get("actor_id"), assertion_id, cut=eid)
                if self._acquired(knowledge):
                    continue
                closed = self._closed_knowledge_rule(event.get("actor_id"), assertion_id, cut=eid)
                if closed:
                    blocked = True
                if self.global_diagnostics or eid not in self.future:
                    self.diagnostics.append(
                        _diagnostic(
                            "KNOWLEDGE_PATH",
                            eid,
                            "使用的信息缺少此前的有效获取路径"
                            + (
                                "；封闭范围规则判为冲突，事件效果不应用"
                                if closed
                                else "；开放范围下保持未知"
                            ),
                            severity="error" if closed else "warning",
                            verdict="conflict" if closed else "insufficient",
                            evidence=_evidence(event) + knowledge.get("evidence", []),
                        )
                    )
            if blocked:
                self.blocked.add(eid)

    def _latest(self, entries: list, *, cut: str | None = None) -> list:
        if cut:
            relevant = [
                item
                for item in entries
                if item.node != cut and cut not in self.ancestors.get(item.node, set())
            ]
        else:
            relevant = [item for item in entries if item.node not in self.future]
        return [
            item
            for item in relevant
            if not any(
                item.node != other.node and item.node in self.ancestors.get(other.node, set())
                for other in relevant
            )
        ]

    def _state(self, subject: str, prop: str, *, cut: str | None = None) -> dict:
        latest = self._latest(
            [
                write
                for write in self.writes
                if write.key == (subject, prop) and write.node not in self.blocked
            ],
            cut=cut,
        )
        evidence = _evidence(*[{"evidence": write.evidence} for write in latest])
        result = {
            "subject_id": subject,
            "property_id": prop,
            "value": None,
            "value_entity_id": None,
            "status": "unknown",
            "reason": "截至查询位置无有效状态记录；缺记录不表示否定",
            "evidence": evidence,
            "record_ids": list(dict.fromkeys(write.object_id for write in latest)),
        }
        if not latest:
            return result
        if len(latest) > 1 and not all(self._same_write(latest[0], write) for write in latest[1:]):
            result["reason"] = "影响同一属性的事件同时、重叠或先后未定，不能选择其中一个效果"
            result["status"] = "conflict"
            return result
        write = latest[0]
        phase = self._scope_status(
            {"end": write.end, "end_event_id": write.end_event}, node=write.node, cut=cut
        )
        if write.node in self.invalid:
            result["reason"] = "状态的事件时间约束有冲突，不能确定效果"
        elif phase != "known":
            result["temporal_status"] = phase
            result["reason"] = (
                "状态有效期已结束，结束后不恢复更早状态"
                if phase == "expired"
                else "事件未完成或相对查询位置未定，状态未知"
            )
        elif write.clear:
            result["reason"] = "状态被显式清除；该动作未证明新的结果"
        elif write.persistence in {"unknown", "instant"}:
            bounds = _bounds(self.active.get(write.node, {}))
            at_completion = bool(
                bounds
                and not cut
                and not self.event_cut
                and self.minute == bounds[1]
                and self.boundary == "after"
            )
            at_completion |= bool(
                self.event_cut == write.node and self.boundary == "after" and not cut
            )
            if at_completion:
                result.update(
                    value=write.value,
                    value_entity_id=write.entity,
                    status="known",
                    reason=f"记录“{write.label}”明确的完成时刻状态；未假定持续",
                )
            else:
                result["reason"] = "记录未说明状态持续多久，完成时刻以外保持未知"
        elif write.persistence == "bounded" and write.end is None and not write.end_event:
            result["reason"] = "有界状态缺少有效期终点，状态未知"
        else:
            result.update(
                value=write.value,
                value_entity_id=write.entity,
                status="known",
                reason=f"由记录“{write.label}”及明确有效期确定",
            )
        return result

    @staticmethod
    def _same_write(first: _Write, other: _Write) -> bool:
        """Equivalent representations share an anchor and their full time policy.

        Comparing only values would hide unordered changes or extend a bounded
        observation through another record's persistence. Provenance stays on
        the original writes; only their projection is shared.
        """
        return (
            first.node == other.node
            and first.key == other.key
            and type(first.value) is type(other.value)
            and first.value == other.value
            and first.entity == other.entity
            and first.clear == other.clear
            and first.persistence == other.persistence
            and first.end == other.end
            and first.end_event == other.end_event
        )

    @staticmethod
    def _condition(condition: dict, state: dict) -> bool | None:
        operator = condition.get("operator", "eq")
        known = state["status"] == "known"
        if operator == "known":
            return known
        if operator == "unknown":
            return not known
        if not known:
            return None
        if condition.get("value_entity_id") is not None:
            equal = state["value_entity_id"] == condition["value_entity_id"]
        else:
            value = state["value"]
            expected = condition.get("value")
            equal = (
                state["value_entity_id"] is None
                and type(value) is type(expected)
                and value == expected
            )
        return equal if operator == "eq" else not equal

    def _knowledge_entries(self) -> list[dict]:
        entries = []
        for obj in self.records:
            if obj["kind"] == "knowledge":
                entries.append(obj | {"node": self.record_nodes[obj["id"]]})
        for obj in self.assertions.values():
            if obj.get("status", "accepted") == "accepted" and obj.get("scope") == "knowledge":
                # Legacy records remain epistemic, even when certainty is high.
                entries.append(
                    obj
                    | {
                        "id": "$assertion:" + obj["id"],
                        "assertion_id": obj["id"],
                        "attitude": {
                            "certain": "knows",
                            "suspects": "suspects",
                            "denies": "denies",
                        }.get(obj.get("certainty") or "unknown", "unknown"),
                        "acquisition": obj.get("learning_method") or "unknown",
                        "node": None,
                    }
                )
        for event in self.events:
            if event["action"] not in {"observe", "tell"} or not event.get("assertion_id"):
                continue
            holder = event.get("target_id") if event["action"] == "tell" else event.get("actor_id")
            if holder:
                entries.append(
                    {
                        "id": "$event:" + event["id"],
                        "kind": "knowledge",
                        "holder_id": holder,
                        "assertion_id": event["assertion_id"],
                        "attitude": event.get("knowledge_attitude", "informed"),
                        "acquisition": "told" if event["action"] == "tell" else "observed",
                        "source_character_id": event.get("actor_id")
                        if event["action"] == "tell"
                        else None,
                        "event_id": event["id"],
                        "node": event["id"],
                        "evidence": _evidence(event),
                        "validity": {"end": event.get("valid_until")},
                    }
                )
        return entries

    def _knowledge(self, holder: str | None, assertion: str, *, cut: str | None = None) -> dict:
        entries = [
            obj
            for obj in self.knowledge_entries
            if obj.get("holder_id") == holder
            and obj["assertion_id"] == assertion
            and obj.get("node") not in self.blocked
        ]
        relevant = []
        for obj in entries:
            node = obj.get("node")
            if node and (
                (cut and (node == cut or cut in self.ancestors.get(node, set())))
                or (not cut and node in self.future)
            ):
                continue
            relevant.append(obj)
        latest = [
            obj
            for obj in relevant
            if not any(
                obj.get("node")
                and other.get("node")
                and obj["node"] != other["node"]
                and obj["node"] in self.ancestors.get(other["node"], set())
                for other in relevant
            )
        ]
        result = {
            "holder_id": holder,
            "assertion_id": assertion,
            "attitude": "unknown",
            "acquisition": "unknown",
            "status": "unknown",
            "reason": "没有在此位置之前有效的信息获取记录；不据此认定人物不知情",
            "evidence": _evidence(*latest),
            "record_ids": [obj["id"] for obj in latest],
        }
        if not latest:
            return result
        if len(latest) != 1 and not all(self._same_knowledge(latest[0], obj) for obj in latest[1:]):
            return result | {
                "status": "conflict",
                "reason": "多条认知的先后、态度、获取渠道、来源或有效期不一致，无法确定当前认知",
            }
        obj = latest[0]
        state = self._scope_status(obj.get("validity") or {}, node=obj.get("node"), cut=cut)
        target = self.assertions.get(assertion)
        result.update(
            {key: obj.get(key) for key in ("source_character_id", "event_id", "validity")}
        )
        if not target or target.get("status", "accepted") != "accepted":
            return result | {"reason": "引用的命题不存在或未被采纳"}
        if state != "known" or obj.get("node") in self.invalid:
            return result | {
                "status": "expired" if state == "expired" else "unknown",
                "reason": "认知记录有效期已结束"
                if state == "expired"
                else "认知获取时点或有效期未确定",
            }
        attitude = obj.get("attitude", "unknown")
        result.update(
            attitude=attitude,
            acquisition=obj.get("acquisition", "unknown"),
            status="forgotten" if attitude == "forgotten" else "known",
            reason="明确记录已遗忘；不抹去此前获取历史"
            if attitude == "forgotten"
            else "该记录只表明人物收到信息或持有态度，不证明命题在世界中为真",
        )
        return result

    @staticmethod
    def _same_knowledge(first: dict, other: dict) -> bool:
        def signature(obj: dict) -> tuple:
            scope = obj.get("validity") or {}
            node = obj.get("node")
            return (
                node,
                obj.get("attitude", "unknown"),
                obj.get("acquisition", "unknown"),
                obj.get("source_character_id"),
                obj.get("event_id") or node,
                scope.get("start"),
                scope.get("start_event_id") or node,
                scope.get("end"),
                scope.get("end_event_id"),
            )

        # Two unanchored legacy assertions are not one acquisition event.
        return bool(first.get("node")) and signature(first) == signature(other)

    @staticmethod
    def _acquired(knowledge: dict) -> bool:
        return (
            knowledge["status"] == "known"
            and knowledge["attitude"] not in {"unknown", "forgotten", "denies"}
            and knowledge.get("acquisition") not in {None, "unknown", ""}
        )

    def _closed_knowledge_rule(
        self, holder: str | None, assertion: str, *, cut: str | None = None
    ) -> bool:
        return any(
            rule["kind"] == "rule"
            and rule.get("rule_type") == "knowledge_required"
            and rule.get("closed_world")
            and rule.get("assertion_id") == assertion
            and (not rule.get("entity_ids") or holder in rule["entity_ids"])
            and self._scope_status(rule.get("validity") or {}, timeless=True, cut=cut) == "known"
            for rule in self.records
        )

    def _rules(self, states: list[dict], knowledge: list[dict]) -> list[dict]:
        indexed = {(obj["subject_id"], obj["property_id"]): obj for obj in states}
        diagnostics = []
        for rule in self.records:
            if rule["kind"] != "rule":
                continue
            phase = self._scope_status(rule.get("validity") or {}, timeless=True)
            if phase in {"future", "expired"}:
                continue
            verdict, message = "insufficient", "规则有效期未确定"
            severity = "info"
            if phase == "known":
                if rule["rule_type"] == "knowledge_required":
                    aid = rule.get("assertion_id")
                    holders = rule.get("entity_ids") or [
                        obj["id"]
                        for obj in self.entities.values()
                        if obj["entity_type"] == "person"
                    ]
                    if not aid or aid not in self.assertions or not holders:
                        verdict, message = "unsupported", "认知规则缺少有效的命题或人物范围"
                    else:
                        acquired = [
                            self._acquired(
                                next(
                                    (
                                        item
                                        for item in knowledge
                                        if item["holder_id"] == holder
                                        and item["assertion_id"] == aid
                                    ),
                                    self._knowledge(holder, aid),
                                )
                            )
                            for holder in holders
                        ]
                        if all(acquired):
                            verdict, message = (
                                "consistent",
                                "规则范围内的人物均有此前有效的信息获取路径",
                            )
                        elif rule.get("closed_world"):
                            verdict, message = "conflict", "封闭范围内缺少规则所需的信息获取路径"
                        else:
                            verdict, message = (
                                "insufficient",
                                "开放范围内缺获取记录，不能认定为已知或不知",
                            )
                else:
                    conditions = rule.get("conditions", [])
                    values = [
                        self._condition(
                            condition,
                            indexed.get(
                                (condition["subject_id"], condition["property_id"]),
                                {"status": "unknown"},
                            ),
                        )
                        for condition in conditions
                    ]
                    if not values:
                        verdict, message = "unsupported", "规则未包含可执行条件"
                    elif rule["rule_type"] == "require":
                        verdict = (
                            "conflict"
                            if False in values
                            else "insufficient"
                            if None in values
                            else "consistent"
                        )
                        message = {
                            "conflict": "必要条件确定不满足",
                            "insufficient": "必要条件存在未知状态",
                            "consistent": "当前状态满足全部必要条件",
                        }[verdict]
                    elif rule["rule_type"] == "forbid":
                        verdict = (
                            "consistent"
                            if False in values
                            else "insufficient"
                            if None in values
                            else "conflict"
                        )
                        message = {
                            "conflict": "当前状态触发禁止条件",
                            "insufficient": "禁止条件存在未知状态",
                            "consistent": "当前状态未触发禁止条件",
                        }[verdict]
                    else:
                        verdict, message = "unsupported", "不支持的规则类型"
                severity = (
                    rule.get("severity", "warning")
                    if verdict == "conflict"
                    else "warning"
                    if verdict in {"insufficient", "unsupported"}
                    else "info"
                )
                if rule.get("rule_type") == "knowledge_required" and verdict == "insufficient":
                    severity = "warning"
            diagnostics.append(
                _diagnostic(
                    "WORLD_RULE",
                    rule["id"],
                    message,
                    severity=severity,
                    verdict=verdict,
                    evidence=_evidence(rule),
                )
            )
        return diagnostics

    def result(self) -> dict:
        resources = {write.key for write in self.writes}
        resources.update(
            (obj["id"], "location")
            for obj in self.entities.values()
            if obj["entity_type"] == "person"
        )
        resources.update(
            (obj["id"], "held_by") for obj in self.entities.values() if obj["entity_type"] == "item"
        )
        for definition in self.properties.values():
            resources.update(
                (obj["id"], definition["id"])
                for obj in self.entities.values()
                if not definition.get("subject_types")
                or obj["entity_type"] in definition["subject_types"]
            )
        states = [self._state(subject, prop) for subject, prop in sorted(resources)]
        knowledge = [
            self._knowledge(holder, assertion)
            for holder, assertion in sorted(
                {(obj["holder_id"], obj["assertion_id"]) for obj in self.knowledge_entries}
            )
        ]
        relations = []
        for obj in self.records:
            if obj["kind"] != "relation":
                continue
            status = self._scope_status(
                obj.get("validity") or {}, node=self.record_nodes[obj["id"]]
            )
            relations.append(
                obj
                | {
                    "record_status": obj.get("status", "accepted"),
                    "status": status,
                    "reason": "关系仅在明确有效期内成立；人物主张不生成关系"
                    if status == "known"
                    else {
                        "expired": "关系有效期已结束",
                        "future": "关系尚未生效",
                        "unknown": "关系有效期或起点未确定",
                    }[status],
                    "evidence": _evidence(obj),
                }
            )
        assertions = []
        for obj in self.assertions.values():
            phase = (
                self._scope_status(obj.get("validity") or {})
                if obj.get("status", "accepted") == "accepted"
                else obj["status"]
            )
            assertions.append(
                obj
                | {
                    "record_status": obj.get("status", "accepted"),
                    "temporal_status": phase,
                    "world_fact": obj.get("scope", "world") == "world",
                    "reason": "显式世界断言；未自动转换成状态属性"
                    if obj.get("scope", "world") == "world"
                    else "人物主张或认知；不得提升为世界事实",
                }
            )
        values: dict[str, dict[str, str | None]] = {"holders": {}, "locations": {}}
        evidence: dict[str, dict[str, list[dict]]] = {"holders": {}, "locations": {}}
        reasons: dict[str, dict[str, str]] = {"holders": {}, "locations": {}}
        for obj in states:
            kind = {"location": "locations", "held_by": "holders"}.get(obj["property_id"])
            if kind:
                values[kind][obj["subject_id"]] = (
                    obj["value_entity_id"] if obj["status"] == "known" else None
                )
                evidence[kind][obj["subject_id"]] = obj["evidence"]
                reasons[kind][obj["subject_id"]] = obj["reason"]
        diagnostics = self.diagnostics + self._rules(states, knowledge)
        for obj in states:
            if obj["status"] != "known" and obj["evidence"]:
                diagnostics.append(
                    _diagnostic(
                        "STATE_TIME_UNKNOWN",
                        obj["subject_id"],
                        obj["reason"],
                        severity="warning" if obj["status"] == "conflict" else "info",
                        evidence=obj["evidence"],
                    )
                )
        return {
            "minute": self.minute if not self.event_cut else None,
            "event_id": self.event_cut,
            "boundary": self.boundary,
            "revision": self.snapshot.get("revision"),
            **values,
            "evidence": evidence,
            "reasons": reasons,
            "states": states,
            "knowledge": knowledge,
            "relations": relations,
            "assertions": assertions,
            "diagnostics": diagnostics,
        }


def query(snapshot: dict, minute: float, boundary: str = "after") -> dict:
    """Project one immutable repository snapshot at a numeric story-time cut."""
    try:
        numeric = float(minute)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("查询时刻须为有限数值") from error
    if isinstance(minute, bool) or not isfinite(numeric):
        raise ValueError("查询时刻须为有限数值")
    if boundary not in {"before", "after"}:
        raise ValueError("查询边界须为 before 或 after")
    return _Projection(snapshot, numeric, boundary).result()


def diagnose(snapshot: dict) -> list[dict]:
    """Check all accepted event preconditions and information paths in one pass.

    This graph-based validation is independent of a UI's selected query time.
    Event conditions are evaluated strictly before each event, never against the
    project's final state. Query-specific state uncertainty is omitted here.
    """
    numeric: list[float] = []
    for event in _items(snapshot, "events"):
        bounds = _bounds(event)
        if bounds is not None and bounds[1] is not None:
            numeric.append(float(bounds[1]))
    numeric += [
        float(obj["validity"]["start"])
        for obj in _items(snapshot, "records")
        if obj.get("validity", {}).get("start") is not None
    ]
    last = max(numeric, default=0)
    projection = _Projection(snapshot, last, "after", all_events=True)
    return projection.diagnostics


def query_event(snapshot: dict, event_id: str) -> dict:
    """Return full conservative states before/after a selected event.

    A relative event can be queried by its graph cut without inventing minutes.
    Unordered events remain possible on both sides of that cut.
    """
    events = _items(snapshot, "events")
    selected = next((event for event in events if event["id"] == event_id), None)
    if selected is None:
        raise ValueError("事件不存在")
    snapshot = dict(snapshot)
    if selected.get("status", "accepted") != "accepted":
        # Keep the selected position while preventing all of its effects.
        snapshot["events"] = [
            event
            if event["id"] != event_id
            else event
            | {
                "status": "accepted",
                "action": "observe",
                "effects": [],
                "assertion_id": None,
                "knowledge_used": [],
            }
            for event in events
        ]
    before = _Projection(snapshot, 0, "before", event_id).result()
    after = _Projection(snapshot, 0, "after", event_id).result()
    return {
        "event_id": event_id,
        "before": before,
        "after": after,
        "diagnostics": before["diagnostics"]
        + [item for item in after["diagnostics"] if item not in before["diagnostics"]],
    }
