"""Reference and type checks over the proposed complete project snapshot."""

from typing import Literal

from .models import Diagnostic


def validate_records(
    objects: dict[str, dict], changed: set[str], revision: int
) -> list[Diagnostic]:
    results: list[Diagnostic] = []

    def issue(
        obj,
        message,
        rule="REF",
        severity: Literal["error", "warning", "info"] = "error",
        verdict: Literal["consistent", "conflict", "insufficient", "unsupported"] = "conflict",
    ):
        results.append(
            Diagnostic(
                rule_id=rule,
                object_id=obj["id"],
                message=message,
                coverage="全库引用、属性类型、来源与有效期",
                severity=severity,
                verdict=verdict,
                evidence=obj.get("evidence", []),
            )
        )

    def ref(obj, key, kind=None, entity_type=None, required=False, active=False):
        value = obj.get(key)
        if not value:
            if required:
                issue(obj, f"引用不能为空：{key}")
            return None
        target = objects.get(value)
        if not target or (kind and target["kind"] != kind):
            issue(obj, f"引用对象不存在或类型不符：{key}={value}")
            return None
        if entity_type and target.get("entity_type") != entity_type:
            issue(obj, f"{key} 须为 {entity_type} 类型；修改会破坏已有事件引用或人物引用")
        if (
            active
            and obj.get("status", "accepted") == "accepted"
            and target.get("status", "accepted") != "accepted"
        ):
            issue(obj, f"引用对象已被取代或有争议：{key}={value}")
        return target

    def entity(obj, key, expected=None, required=False):
        return ref(obj, key, "entity", expected, required)

    def temporal(obj, validity):
        for key in ("start_event_id", "end_event_id"):
            if validity.get(key):
                ref(obj | validity, key, "event", active=True)
        start = validity.get("start")
        end = validity.get("end")
        if start is None and validity.get("start_event_id") in objects:
            event = objects[validity["start_event_id"]]
            start = (
                event.get("time_end")
                if event.get("time_end") is not None
                else event.get("time_start")
            )
        if end is None and validity.get("end_event_id") in objects:
            event = objects[validity["end_event_id"]]
            end = (
                event.get("time_end")
                if event.get("time_end") is not None
                else event.get("time_start")
            )
        if start is not None and end is not None and end <= start:
            issue(obj, "有效期终点必须晚于起点", "TIME_VALIDITY")

    def property_check(obj, value):
        subject = entity(value | {"id": obj["id"]}, "subject_id", required=True)
        property_id = value["property_id"]
        builtin = {
            "location": ("entity", ["person"], "location"),
            "held_by": ("entity", ["item"], "person"),
            "outfit": ("entity", ["person"], "item"),
        }
        if property_id in builtin:
            value_type, subject_types, reference_type = builtin[property_id]
        elif property_id.startswith("injury:") and property_id[7:]:
            value_type, subject_types, reference_type = "string", ["person"], None
        else:
            definition = objects.get(property_id)
            if not definition or definition["kind"] != "property":
                issue(obj, f"自定义属性未定义：{property_id}")
                return
            if (
                obj.get("status", "accepted") == "accepted"
                and definition.get("status", "accepted") != "accepted"
            ):
                issue(obj, f"属性定义已被取代或有争议：{property_id}")
            value_type = definition["value_type"]
            subject_types = definition.get("subject_types", [])
            reference_type = definition.get("reference_type")
        if subject and subject_types and subject.get("entity_type") not in subject_types:
            issue(obj, f"属性 {property_id} 不适用于该实体类型")
        scalar = value.get("value")
        reference = value.get("value_entity_id")
        if scalar is not None and reference is not None:
            issue(obj, f"属性 {property_id} 不能同时使用标量值和实体引用")
        if value_type == "entity":
            if scalar is not None:
                issue(obj, f"属性 {property_id} 必须使用 value_entity_id")
            if reference:
                entity(value | {"id": obj["id"]}, "value_entity_id", reference_type)
        else:
            if reference is not None:
                issue(obj, f"属性 {property_id} 不接受实体引用")
            expected = {
                "string": lambda item: isinstance(item, str),
                "boolean": lambda item: isinstance(item, bool),
                "number": lambda item: (
                    isinstance(item, (int, float)) and not isinstance(item, bool)
                ),
            }[value_type]
            if scalar is not None and not expected(scalar):
                issue(obj, f"属性 {property_id} 的值须为 {value_type}")

    for obj in objects.values():
        kind = obj["kind"]
        temporal(obj, obj.get("validity", {}))
        for dependency in obj.get("derived_from", []):
            ref(obj | {"derived_from_ref": dependency}, "derived_from_ref", active=True)
        if obj.get("origin") == "inference" and not obj.get("derived_from"):
            issue(obj, "推导记录必须指定有效 derived_from 来源")
        if obj.get("origin") in {"creative", "production"} and obj["id"] in changed:
            issue(obj, "创作／制作假设单独标记，不视为原著事实", "ORIGIN", "info", "insufficient")
        if kind == "assertion":
            entity(obj, "subject_id", required=True)
            for key in ("holder_id", "source_character_id"):
                entity(obj, key, "person")
            entity(obj, "value_entity_id")
        elif kind == "event":
            start = obj.get("time_start")
            end = obj.get("time_end") if obj.get("time_end") is not None else start
            if obj.get("min_duration") is not None or obj.get("max_duration") is not None:
                if start is None or end is None:
                    issue(
                        obj,
                        "事件时间未定，无法核验时长约束",
                        "TIME_DURATION",
                        "info",
                        "insufficient",
                    )
                elif (
                    obj.get("min_duration") is not None
                    and end - start < obj["min_duration"]
                    or obj.get("max_duration") is not None
                    and end - start > obj["max_duration"]
                ):
                    issue(obj, "事件时长与最短／最长时长约束冲突", "TIME_DURATION")
            required = {
                "move": {"actor_id": "person", "location_id": "location"},
                "transfer": {"actor_id": "person", "target_id": "person", "item_id": "item"},
                "dress": {"actor_id": "person", "item_id": "item"},
                "injure": {"target_id": "person"},
                "treat": {"target_id": "person"},
                "tell": {"actor_id": "person", "target_id": "person"},
                "act": {"actor_id": "person"},
            }.get(obj["action"], {})
            for key, expected in {
                "actor_id": "person",
                # An observed object can be any entity (a letter, place, animal,
                # or person); receiving, dressing, and injury targets remain people.
                "target_id": None if obj["action"] in {"observe", "act"} else "person",
                "item_id": "item",
                "location_id": "location",
            }.items():
                entity(obj, key, expected, key in required)
            for participant in obj.get("participants", []):
                entity(
                    obj | {"participant_id": participant["entity_id"]},
                    "participant_id",
                    None if obj["action"] == "narrative" else "person",
                    required=True,
                )
            ref(obj, "after_event_id", "event", active=True)
            ref(obj, "assertion_id", "assertion", required=obj["action"] == "tell", active=True)
            for assertion_id in obj.get("knowledge_used", []):
                ref(
                    obj | {"knowledge_ref": assertion_id}, "knowledge_ref", "assertion", active=True
                )
            for value in [*obj.get("effects", []), *obj.get("preconditions", [])]:
                property_check(obj, value)
            for effect in obj.get("effects", []):
                if (
                    effect.get("valid_until") is not None
                    and end is not None
                    and effect["valid_until"] <= end
                ):
                    issue(obj, "状态效果有效期终点必须晚于事件完成时刻", "TIME_VALIDITY")
        elif kind == "property":
            if obj["id"] in {"location", "held_by", "outfit"} or obj["id"].startswith("injury:"):
                issue(obj, "内置属性名称保留，不能用自定义属性覆盖")
            if obj["value_type"] != "entity" and obj.get("reference_type"):
                issue(obj, "只有实体类型属性可指定 reference_type")
        elif kind == "state":
            property_check(obj, obj)
        elif kind == "relation":
            entity(obj, "subject_id", required=True)
            entity(obj, "object_id", required=True)
        elif kind == "knowledge":
            entity(obj, "holder_id", "person", required=True)
            entity(obj, "source_character_id", "person")
            ref(obj, "assertion_id", "assertion", required=True, active=True)
            ref(obj, "event_id", "event", active=True)
        elif kind == "rule":
            for entity_id in obj.get("entity_ids", []):
                entity(obj | {"entity_ref": entity_id}, "entity_ref", required=True)
            ref(
                obj,
                "assertion_id",
                "assertion",
                required=obj["rule_type"] == "knowledge_required",
                active=True,
            )
            for condition in obj.get("conditions", []):
                property_check(obj, condition)
        elif kind == "asset_binding":
            entity(obj, "entity_id", required=True)
        elif kind in {"scene", "intervention"}:
            for entity_id in obj.get("entity_ids", []):
                entity(obj | {"entity_ref": entity_id}, "entity_ref", required=True)
            for condition in obj.get("constraints", []):
                property_check(obj, condition)
            for requirement in obj.get("asset_requirements", []):
                entity(obj | {"entity_ref": requirement["entity_id"]}, "entity_ref", required=True)
            for binding_id in obj.get("binding_ids", []):
                ref(obj | {"binding_ref": binding_id}, "binding_ref", "asset_binding", active=True)
        elif kind == "creative_intent":
            entity(obj, "actor_id", "person", required=True)
            ref(obj, "intervention_id", "intervention", active=True)
            for event_id in obj.get("proposed_event_ids", []):
                ref(obj | {"event_ref": event_id}, "event_ref", "event", active=True)
            for condition in obj.get("constraints", []):
                property_check(obj, condition)
        elif kind == "event_link":
            if not obj.get("evidence"):
                issue(obj, "事件关系解读须有原文证据", "EVID")
            source = ref(obj, "source_event_id", "event", required=True, active=True)
            target = ref(obj, "target_event_id", "event", required=True, active=True)
            if source and target and source["id"] == target["id"]:
                issue(obj, "事件不能与自身建立情节关系")
        elif kind == "storyline":
            if not obj.get("evidence"):
                issue(obj, "故事线解读须有原文证据", "EVID")
            if len(obj["event_ids"]) != len(set(obj["event_ids"])):
                issue(obj, "故事线不能重复包含同一事件")
            for event_id in obj["event_ids"]:
                ref(obj | {"event_ref": event_id}, "event_ref", "event", active=True)
        elif kind == "classification":
            if not obj.get("evidence"):
                issue(obj, "分类解读须有原文证据", "EVID")
            target = ref(obj, "target_id", obj["target_kind"], required=True, active=True)
            if target and obj["target_kind"] == "entity" and target.get("entity_type") != "person":
                issue(obj, "人物分类目标须为人物实体")
        elif kind in {"artifact_observation", "artifact_acceptance"}:
            ref(obj, "scene_id", "scene", required=True, active=True)
            if obj["scene_revision"] > revision:
                issue(obj, "产物关联的场景修订不能来自未来")
            for attribute in obj.get("attributes", []):
                entity(obj | {"entity_ref": attribute["entity_id"]}, "entity_ref", required=True)
            for observation_id in obj.get("observation_ids", []):
                observation = ref(
                    obj | {"observation_ref": observation_id},
                    "observation_ref",
                    "artifact_observation",
                    active=True,
                )
                if observation and (
                    observation["scene_id"] != obj["scene_id"]
                    or observation["scene_revision"] != obj["scene_revision"]
                ):
                    issue(obj, "验收记录和产物观察必须属于同一场景及修订")

    # Walk every inference chain, including unchanged records. A correction must
    # not introduce a cycle through a record that is outside its change set.
    # Kahn's traversal also handles long imported chains without recursion.
    dependencies = {
        object_id: {ref for ref in obj.get("derived_from", []) if ref in objects}
        for object_id, obj in objects.items()
    }
    dependents: dict[str, set[str]] = {}
    for object_id, parents in dependencies.items():
        for parent in parents:
            dependents.setdefault(parent, set()).add(object_id)
    ready = [object_id for object_id, parents in dependencies.items() if not parents]
    while ready:
        resolved = ready.pop()
        for dependent in dependents.get(resolved, set()):
            dependencies[dependent].discard(resolved)
            if not dependencies[dependent]:
                ready.append(dependent)
        dependencies.pop(resolved)
    for object_id in dependencies:
        issue(objects[object_id], "derived_from 推导来源成环或依赖环内对象")
    return results
