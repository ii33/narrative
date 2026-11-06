"""Non-narrative event-state anchors kept outside imported timeline nodes."""
from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StateAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    id: str = Field(min_length=1)
    node_type: Literal["storyline", "big_event", "scene", "event", "action"]
    context_id: str = "canon"
    source_position: float | int
    offset: float = Field(ge=0, le=1)
    preceding_ids: list[str] = Field(max_length=1)
    following_ids: list[str] = Field(max_length=1)


def anchor_nodes(snapshot):
    """Internal reference geometry only; never append these points to nodes."""
    real = {node["id"]: node for node in snapshot["nodes"]}
    result = {}
    for anchor in snapshot.get("state_anchors", []):
        if anchor["id"] in real:
            continue
        preceding = next((real[key]["sort_id"] for key in anchor["preceding_ids"] if key in real), None)
        following = next((real[key]["sort_id"] for key in anchor["following_ids"] if key in real), None)
        if preceding is not None and following is not None and preceding < following:
            rank = preceding + (following - preceding) * anchor["offset"]
        elif preceding is not None:
            rank = preceding + anchor["offset"]
        elif following is not None:
            rank = following - anchor["offset"]
        else:
            rank = anchor["source_position"]
        result[anchor["id"]] = {"id": anchor["id"], "node_type": anchor["node_type"],
            "context_id": anchor.get("context_id", "canon"), "sort_id": rank}
    return result


def selection_anchors(source, visible_ids, reference_ids):
    points = {**anchor_nodes(source), **{node["id"]: node for node in source["nodes"]}}
    visible = sorted([points[key] for key in visible_ids if key in points and points[key]["node_type"] in {"storyline", "big_event", "scene", "event", "action"}], key=lambda row: row["sort_id"])
    result = []
    for identifier in sorted(reference_ids - visible_ids):
        point = points.get(identifier)
        if not point:
            raise ValueError("状态变更锚点不存在：" + identifier)
        context = point.get("context_id", "canon")
        same_context = [node for node in visible if node.get("context_id", "canon") == context]
        preceding = [node for node in same_context if node["sort_id"] < point["sort_id"]][-1:]
        following = [node for node in same_context if node["sort_id"] > point["sort_id"]][:1]
        if preceding and following:
            offset = (point["sort_id"] - preceding[0]["sort_id"]) / (following[0]["sort_id"] - preceding[0]["sort_id"])
        else:
            distance = abs(point["sort_id"] - (preceding or following or [point])[0]["sort_id"])
            offset = distance / (distance + 1)
        result.append({"id": identifier, "node_type": point["node_type"], "context_id": context,
            "source_position": point["sort_id"], "offset": offset,
            "preceding_ids": [node["id"] for node in preceding],
            "following_ids": [node["id"] for node in following]})
    return result


def merge_anchors(state, incoming):
    """Explicitly imported real identities replace metadata, never duplicate them."""
    real = {node["id"] for node in state["nodes"]}
    previous = {row["id"]: row for row in state.get("state_anchors", [])}
    for row in incoming:
        previous[row["id"]] = copy.deepcopy(row)
    state["state_anchors"] = [row for key, row in previous.items() if key not in real]
