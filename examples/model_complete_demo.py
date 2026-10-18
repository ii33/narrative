"""Create an isolated complete-model demonstration through Operations only."""

import hashlib
import json
from pathlib import Path

from narrative.operations import Operations


def build(destination: Path):
    if destination.exists():
        raise ValueError("演示目标必须为新项目")
    app = Operations(destination)
    text = "林舟在仓库。\n林舟把铜钥匙交给沈砚。\n林舟换上蓝衣。\n林舟左臂划伤。\n沈砚给林舟包扎左臂。\n林舟说门开着，并告知沈砚。\n门一直关着。"
    source = app.import_text("完整模型演示", text)

    def proof(quote):
        return [app.locate(source, quote)[0].model_dump()]

    def event(eid, title, action, minute, quote, **fields):
        return {
            "kind": "event",
            "id": eid,
            "title": title,
            "action": action,
            "time_kind": "point",
            "time_start": minute,
            "evidence": proof(quote),
            **fields,
        }

    manifest = {
        "adapter": "local-test-manifest",
        "entity": "林舟",
        "version": "1",
        "attributes": {"外衣": "蓝色"},
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    manifest_path = destination.parent / "model-demo-asset-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    records = [
        *[
            {"kind": "entity", "id": ref, "name": name, "entity_type": kind}
            for ref, name, kind in (
                ("a", "林舟", "person"),
                ("b", "沈砚", "person"),
                ("key", "铜钥匙", "item"),
                ("coat", "蓝衣", "item"),
                ("door", "门", "item"),
                ("warehouse", "仓库", "location"),
            )
        ],
        {
            "kind": "assertion",
            "id": "claim-open",
            "subject_id": "door",
            "predicate": "门开着",
            "value": True,
            "scope": "claim",
            "holder_id": "a",
            "certainty": "certain",
            "validity": {"start": 5},
            "evidence": proof("林舟说门开着，并告知沈砚。"),
        },
        {
            "kind": "property",
            "id": "door-open",
            "name": "是否打开",
            "value_type": "boolean",
            "subject_types": ["item"],
            "persistence": "until_changed",
            "origin": "creative",
        },
        {
            "kind": "state",
            "id": "closed-door",
            "subject_id": "door",
            "property_id": "door-open",
            "value": False,
            "validity": {"start": 0, "end": 60},
            "evidence": proof("门一直关着。"),
        },
        {
            "kind": "relation",
            "id": "companions",
            "subject_id": "a",
            "predicate": "同行",
            "object_id": "b",
            "directed": False,
            "certainty": "certain",
            "validity": {"start": 0, "end": 10},
            "origin": "creative",
        },
        event(
            "move", "林舟位于仓库", "move", 0, "林舟在仓库。", actor_id="a", location_id="warehouse"
        ),
        event(
            "transfer",
            "钥匙交给沈砚",
            "transfer",
            1,
            "林舟把铜钥匙交给沈砚。",
            actor_id="a",
            target_id="b",
            item_id="key",
        ),
        event(
            "dress",
            "林舟换上蓝衣",
            "dress",
            2,
            "林舟换上蓝衣。",
            actor_id="a",
            item_id="coat",
            persistence="bounded",
            valid_until=10,
        ),
        event(
            "injure",
            "左臂划伤",
            "injure",
            3,
            "林舟左臂划伤。",
            target_id="a",
            slot="left_arm",
            state_value="划伤",
            persistence="until_changed",
        ),
        event(
            "treat",
            "包扎左臂",
            "treat",
            4,
            "沈砚给林舟包扎左臂。",
            actor_id="b",
            target_id="a",
            slot="left_arm",
            state_value="已包扎；未确认痊愈",
            persistence="bounded",
            valid_until=8,
        ),
        event(
            "tell",
            "沈砚听说门开着",
            "tell",
            5,
            "林舟说门开着，并告知沈砚。",
            actor_id="a",
            target_id="b",
            assertion_id="claim-open",
        ),
        {
            "kind": "knowledge",
            "id": "forgot",
            "holder_id": "b",
            "assertion_id": "claim-open",
            "attitude": "forgotten",
            "acquisition": "initial",
            "validity": {"start": 9},
            "origin": "creative",
        },
        {
            "kind": "rule",
            "id": "door-rule",
            "title": "门保持关闭",
            "rule_type": "require",
            "conditions": [{"subject_id": "door", "property_id": "door-open", "value": False}],
            "validity": {"start": 0, "end": 10},
            "origin": "creative",
        },
        {
            "kind": "asset_binding",
            "id": "blue-asset",
            "entity_id": "a",
            "role": "角色形象",
            "asset_uri": manifest_path.resolve().as_uri(),
            "version": "1",
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "attributes": {"外衣": "蓝色"},
            "origin": "production",
        },
        {
            "kind": "scene",
            "id": "warehouse-scene",
            "title": "仓库交接",
            "validity": {"start": 0, "end": 8},
            "entity_ids": ["a", "b", "key", "door"],
            "constraints": [{"subject_id": "door", "property_id": "door-open", "value": False}],
            "asset_requirements": [
                {"entity_id": "a", "role": "角色形象", "attributes": {"外衣": "蓝色"}}
            ],
            "binding_ids": ["blue-asset"],
            "production_decisions": ["本地清单仅作资产契约测试"],
            "origin": "production",
        },
        {
            "kind": "intervention",
            "id": "stimulus",
            "prompt": "门保持关闭，人物寻找其它出路",
            "entity_ids": ["a", "b"],
            "event_budget": 3,
            "entity_budget": 0,
            "model_call_budget": 0,
            "origin": "creative",
        },
        {
            "kind": "creative_intent",
            "id": "intent",
            "actor_id": "a",
            "goal": "找出安全出口",
            "intervention_id": "stimulus",
            "origin": "creative",
        },
    ]
    candidate = app.propose_objects(
        records, note="完整模型隔离演示；时间坐标与创作补充均为人工设定。"
    )
    app.accept_candidate(candidate, 1)
    revision = app.project_info().revision
    candidate = app.propose_objects(
        [
            {
                "kind": "artifact_observation",
                "id": "observation",
                "scene_id": "warehouse-scene",
                "scene_revision": revision,
                "artifact_uri": manifest_path.resolve().as_uri(),
                "artifact_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "attributes": [
                    {"entity_id": "a", "attribute": "外衣", "value": "蓝色", "certainty": "certain"}
                ],
                "media_note": "测试清单观察，并非真实图片识别",
                "origin": "production",
            },
            {
                "kind": "artifact_acceptance",
                "id": "acceptance",
                "scene_id": "warehouse-scene",
                "scene_revision": revision,
                "observation_ids": ["observation"],
                "decision": "accepted",
                "origin": "production",
            },
        ]
    )
    app.accept_candidate(candidate, 1)
    package = app.scene_package("warehouse-scene", draft=False)
    (destination.parent / "model-demo-scene-package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination.parent / "narrative-model-v3.json").write_text(
        json.dumps(app.model_schema(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return app


if __name__ == "__main__":
    project = build(Path("data/model-complete-demo.sqlite3"))
    print(
        json.dumps(
            {
                "revision": project.project_info().revision,
                "records": len(project.records()),
                "state": project.state_at_time(6),
            },
            ensure_ascii=False,
        )
    )
