"""The single authoritative strict screenplay / episode / act / segment tree."""
from __future__ import annotations

from .screenplay_store import new_id, now, object_fields

KIND_TABLE = {"screenplay": "screenplays", "episode": "script_episodes", "act": "script_acts", "segment": "script_segments"}


def require(state, kind, identifier):
    row = state[KIND_TABLE[kind]].get(identifier)
    if not row:
        raise ValueError(f"{kind}对象不存在")
    return row


def entry_for(state, kind, identifier):
    entries = [e for e in state["script_structure_entries"].values()
               if e["child_kind"] == kind and e["child_id"] == identifier]
    if len(entries) != 1:
        raise ValueError("组织对象须恰好有一个入口")
    return entries[0]


def child_entries(state, parent_kind, parent_id):
    return sorted([e for e in state["script_structure_entries"].values()
                   if e["parent_kind"] == parent_kind and e["parent_id"] == parent_id], key=lambda e: e["position"])


def tree(state, screenplay_id):
    def walk(kind, identifier):
        result = []
        for entry in child_entries(state, kind, identifier):
            child = require(state, entry["child_kind"], entry["child_id"])
            result.append({"entry": entry, "kind": entry["child_kind"], "id": child["id"], "object": child,
                           "children": walk(entry["child_kind"], child["id"])})
        return result
    return walk("screenplay", screenplay_id)


def ordered_segment_ids(state, screenplay_id):
    result = []
    def visit(kind, identifier):
        for entry in child_entries(state, kind, identifier):
            if entry["child_kind"] == "segment":
                result.append(entry["child_id"])
            else:
                visit(entry["child_kind"], entry["child_id"])
    visit("screenplay", screenplay_id)
    return result


def ancestors(state, child_kind, child_id):
    result = []
    kind, identifier = child_kind, child_id
    while kind != "screenplay":
        entry = entry_for(state, kind, identifier)
        kind, identifier = entry["parent_kind"], entry["parent_id"]
        result.append({"kind": kind, "id": identifier, "object": require(state, kind, identifier)})
    return list(reversed(result))


def _renumber(state, parent_kind, parent_id):
    for index, entry in enumerate(child_entries(state, parent_kind, parent_id), 1):
        entry["position"] = index * 1024
        if entry["child_kind"] == "episode":
            state["script_episodes"][entry["child_id"]]["position"] = entry["position"]


def move_entry(state, entry, parent_kind, parent_id, after_entry_id=None):
    if parent_kind not in {"screenplay", "episode", "act"}:
        raise ValueError("组织目标类型无效")
    require(state, parent_kind, parent_id)
    old_kind, old_id = entry["parent_kind"], entry["parent_id"]
    siblings = [e for e in child_entries(state, parent_kind, parent_id) if e["id"] != entry["id"]]
    if after_entry_id is None:
        index = 0
    else:
        index = next((i + 1 for i, e in enumerate(siblings) if e["id"] == after_entry_id), -1)
        if index < 0:
            raise ValueError("排序参照入口不属于目标父级")
    entry.update(parent_kind=parent_kind, parent_id=parent_id, updated_at=now(), revision=entry["revision"] + 1)
    siblings.insert(index, entry)
    for index, sibling in enumerate(siblings, 1):
        sibling["position"] = index * 1024
        if sibling["child_kind"] == "episode":
            state["script_episodes"][sibling["child_id"]]["position"] = sibling["position"]
    if (old_kind, old_id) != (parent_kind, parent_id):
        _renumber(state, old_kind, old_id)
    if entry["child_kind"] == "act":
        state["script_acts"][entry["child_id"]]["episode_id"] = parent_id if parent_kind == "episode" else None


def add_entry(state, screenplay_id, parent_kind, parent_id, child_kind, child_id, after_entry_id=None):
    stamp = now()
    entry = {"id": new_id("structure"), "revision": 1, "created_at": stamp, "updated_at": stamp,
             "screenplay_id": screenplay_id, "parent_kind": parent_kind, "parent_id": parent_id,
             "child_kind": child_kind, "child_id": child_id, "position": 0}
    state["script_structure_entries"][entry["id"]] = entry
    move_entry(state, entry, parent_kind, parent_id, after_entry_id)
    return entry


def default_parent(state, screenplay):
    if screenplay["format"] == "single":
        return "screenplay", screenplay["id"]
    episodes = [e for e in child_entries(state, "screenplay", screenplay["id"]) if e["child_kind"] == "episode"]
    if not episodes:
        raise ValueError("剧集须先创建集")
    return "episode", episodes[0]["child_id"]


def create_episode(state, screenplay_id, title, number="1", brief=None, target_duration_seconds=None,
                   identifier=None, after_entry_id=None):
    if identifier and identifier in state["script_episodes"]:
        raise ValueError("集ID已存在")
    episode = {**object_fields("episode", identifier or None), "screenplay_id": screenplay_id, "title": title, "number": number,
               "brief": {} if brief is None else brief, "target_duration_seconds": target_duration_seconds, "position": 0}
    state["script_episodes"][episode["id"]] = episode
    add_entry(state, screenplay_id, "screenplay", screenplay_id, "episode", episode["id"], after_entry_id)
    return episode


def validate_structure(state):
    counts = {}
    positions = set()
    for entry in state["script_structure_entries"].values():
        screenplay = require(state, "screenplay", entry["screenplay_id"])
        parent_kind, child_kind = entry["parent_kind"], entry["child_kind"]
        parent = require(state, parent_kind, entry["parent_id"])
        child = require(state, child_kind, entry["child_id"])
        if (parent["id"] if parent_kind == "screenplay" else parent["screenplay_id"]) != screenplay["id"] or child["screenplay_id"] != screenplay["id"]:
            raise ValueError("组织入口禁止跨剧本引用")
        allowed = {"screenplay": {"episode"} if screenplay["format"] == "series" else {"act", "segment"},
                   "episode": {"act", "segment"}, "act": {"segment"}}
        if child_kind not in allowed.get(parent_kind, set()):
            raise ValueError("集／幕／段落组织层级无效")
        if parent_kind == "episode" and screenplay["format"] != "series":
            raise ValueError("单片不可包含集")
        if child_kind == "act" and child["episode_id"] != (entry["parent_id"] if parent_kind == "episode" else None):
            raise ValueError("幕的集归属与组织入口不一致")
        if child_kind == "episode" and child["position"] != entry["position"]:
            raise ValueError("集顺序必须由组织入口统一维护")
        key = child_kind, child["id"]
        counts[key] = counts.get(key, 0) + 1
        place = parent_kind, parent["id"], entry["position"]
        if place in positions:
            raise ValueError("同级组织顺序重复")
        positions.add(place)
    for kind in ("episode", "act", "segment"):
        for row in state[KIND_TABLE[kind]].values():
            if counts.get((kind, row["id"])) != 1:
                raise ValueError("每个集、幕和段落必须恰好有一个组织入口")
    for screenplay in state["screenplays"].values():
        episodes = [e for e in state["script_episodes"].values() if e["screenplay_id"] == screenplay["id"]]
        if screenplay["format"] == "single" and episodes:
            raise ValueError("单片不可保留集对象")
        if screenplay["format"] == "series" and not episodes:
            raise ValueError("剧集至少需要一集")
