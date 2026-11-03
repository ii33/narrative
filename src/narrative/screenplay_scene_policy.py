"""Editorial scene standards, with only deterministic source-reference checks."""
from __future__ import annotations

import copy

POLICY_VERSION = "scene-standards/2"


def scene_table_policy(resolved):
    source = resolved.get("input") or {}
    return {"version": POLICY_VERSION, "decision_owner": "model",
        "rules": ["同一连续时空的多个事件默认合为一场；事件和剧情节拍不是场。",
                  "情绪、招式、胜负和镜头变化不单独构成场界。",
                  "根据实际地点、时间或叙述时空变化判断是否另起一场；不按事件数量凑场数。",
                  "只使用当前有效事件与逐点状态；旧场表、屏蔽事件和范围外剧情不是生成依据。",
                  "场表只写场级剧情，不写特写、慢镜、镜头调度或特效制作方案。"],
        "source_event_ids": list(source.get("source_event_ids", []))}


def prepare_generated_scene_table(drafts, resolved):
    """The model decides scene count/boundaries; the program checks references."""
    rows = copy.deepcopy(drafts)
    events = (resolved.get("input") or {}).get("source_event_ids", [])
    active = [row for row in rows if row["enabled"] and row["kind"] == "scene"]
    for row in rows:
        if not row["enabled"]:
            continue
        plan = row["plan"]
        if not plan["summary"].strip():
            raise ValueError("拆场提交每个启用场至少需要非空概要")
        if any(identifier not in events for identifier in plan["source_event_ids"]):
            raise ValueError("场表不能引用屏蔽或范围外事件")
    # A single scene's provenance is unambiguous; this is bookkeeping, not a
    # partition rule. Multiple scenes need no forced assignment or boundary tags.
    if len(active) == 1 and not active[0]["plan"]["source_event_ids"]:
        active[0]["plan"]["source_event_ids"] = list(events)
    return rows


def scene_source_diagnostics(units, resolved):
    allowed = set((resolved.get("input") or {}).get("source_event_ids", []))
    issues = []
    for unit in units:
        if not unit["enabled"]:
            continue
        invalid = [identifier for identifier in unit["plan"].get("source_event_ids", []) if identifier not in allowed]
        if invalid:
            issues.append({"severity": "error", "code": "SCENE_SOURCE_UNAVAILABLE", "unit_id": unit["id"],
                "source_event_ids": invalid, "message": "场表来源包含屏蔽、删除或范围外事件，须重新核对概要；旧正文保留"})
    return issues
