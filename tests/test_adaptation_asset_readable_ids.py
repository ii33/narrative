"""Readable identities are automatic, stable, transactional and unrelated to order."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from agent_fixtures import project as project
from test_screenplay_core import seed

from narrative.readable_ids import allocate_id


@pytest.mark.parametrize("affinity", ["TEXT", "INTEGER"])
def test_allocator_bootstraps_existing_numbers_retains_high_water_and_rolls_back(affinity):
    db = sqlite3.connect(":memory:")
    db.execute(f"CREATE TABLE meta(key TEXT PRIMARY KEY,value {affinity} NOT NULL)")
    with pytest.raises(ValueError, match="写事务"):
        allocate_id(db, "test", "event-")
    db.execute("BEGIN IMMEDIATE")
    assert allocate_id(db, "test", "event-", ["old_hash_identity", "event-9"]) == "event-10"
    assert allocate_id(db, "test", "event-") == "event-11"
    db.rollback()
    db.execute("BEGIN IMMEDIATE")
    assert allocate_id(db, "test", "event-", ["event-9"]) == "event-10"
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    assert allocate_id(db, "test", "event-") == "event-11"  # prior object can be deleted
    db.commit()
    db.close()


def test_adaptation_ids_preserve_type_identity_references_and_original_ids(project):
    ids = seed(project)
    source = project.graph_snapshot()
    with pytest.raises(ValueError):
        project.edit_adaptation_node(changes={"node_type": "event", "name": "非法归属"}, parent_id="missing")
    state = project.edit_adaptation_node(changes={"node_type": "scene", "name": "新场景"})
    scene = next(n for n in state["nodes"] if n["name"] == "新场景")
    assert scene["id"] == "adapt-scene-1"
    state = project.edit_adaptation_node(changes={"node_type": "event", "name": "新事件", "participants": [ids["甲"]]}, parent_id=scene["id"])
    event = next(n for n in state["nodes"] if n["name"] == "新事件")
    assert event["id"] == "adapt-event-1"  # failed transaction did not consume it
    state = project.set_adaptation_property(event["id"], "说明", event["id"], "set", value="第一段")
    value = next(row for row in state["properties"] if row.get("property_id") == "说明")
    assert value["id"] == "adapt-change-1"
    state = project.copy_adaptation_node(scene["id"])
    cloned_scene = next(n for n in state["nodes"] if n["id"] == "adapt-scene-2")
    cloned_event = next(n for n in state["nodes"] if n["id"] == "adapt-event-2")
    assert cloned_scene["events"] == [cloned_event["id"]]
    assert cloned_event["participants"] == [ids["甲"]]
    copied_value = next(row for row in state["properties"] if row.get("node_id") == cloned_event["id"])
    assert copied_value["id"] == "adapt-change-2" and copied_value["event_id"] == cloned_event["id"]
    project.move_adaptation_node(cloned_event["id"], after_id=ids["甲坐下"])
    assert any(n["id"] == cloned_event["id"] for n in project.adaptation_snapshot()["nodes"])
    project.delete_adaptation_node(event["id"], project.adaptation_snapshot()["revision"], "显式删除")
    state = project.edit_adaptation_node(changes={"node_type": "event", "name": "后续事件"}, parent_id=scene["id"])
    assert next(n for n in state["nodes"] if n["name"] == "后续事件")["id"] == "adapt-event-3"
    state = project.set_adaptation_property(cloned_event["id"], "新增说明", cloned_event["id"], "set", value="第二段")
    assert next(row for row in state["properties"] if row.get("property_id") == "新增说明")["id"] == "adapt-change-3"
    assert project.check_adaptation()["ok"]
    assert project.graph_snapshot() == source
    assert ids["甲"] == "P1" and ids["甲敲门"] in {n["id"] for n in state["nodes"]}


def test_asset_category_and_image_ids_are_readable_stable_and_never_reused(project, tmp_path):
    ids = seed(project)
    state = project.edit_adaptation_node(changes={"node_type": "item", "name": "道具", "initial_holder": ids["甲"],"point_id":ids["甲敲门"]})
    item = next(n for n in state["nodes"] if n["name"] == "道具")
    state = project.edit_adaptation_node(changes={"node_type": "location", "name": "新地点"})
    location = next(n for n in state["nodes"] if n["name"] == "新地点")
    source = project.graph_snapshot()
    image = tmp_path / "original.png"
    image.write_bytes(b"original reference image")
    appearance = project.save_asset({"category": "appearance", "node_id": ids["甲"], "title": "角色形象", "reference_images": [{"path": str(image)}]})
    item_card = project.save_asset({"category": "item", "node_id": item["id"], "title": "道具视觉", "images": [{"path": str(image)}]})
    location_card = project.save_asset({"category": "location", "node_id": location["id"], "title": "地点视觉"})
    with pytest.raises(ValueError, match="标题不能为空"):
        project.save_asset({"category": "material", "title": ""})
    material = project.save_asset({"category": "material", "title": "铜材"})
    assert [appearance["id"], item_card["id"], location_card["id"], material["id"]] == ["appearance-1", "item-design-1", "location-design-1", "material-1"]
    assert appearance["reference_images"][0]["id"] == "asset-image-1"
    assert item_card["images"][0]["id"] == "asset-image-2"
    edited = project.save_asset({"id": appearance["id"], "prompt": "追加视觉描述", "reference_images": [{"id": "asset-image-1", "caption": "原参考"}]})
    assert edited["id"] == appearance["id"]
    assert edited["reference_images"][0]["id"] == "asset-image-1"
    imported_path = Path(edited["reference_images"][0]["path"])
    project.save_asset({"id": appearance["id"], "reference_images": []})
    edited = project.save_asset({"id": appearance["id"], "reference_images": [{"path": str(image)}]})
    assert edited["reference_images"][0]["id"] == "asset-image-3"
    project.delete_asset(material["id"])
    assert project.save_asset({"category": "material", "title": "新铜材"})["id"] == "material-2"
    assert imported_path.read_bytes() == image.read_bytes()
    assert project.graph_snapshot() == source
    assert all(re.fullmatch(r"[a-z]+(?:-[a-z]+)*-[0-9]+", card["id"]) for card in (appearance, item_card, location_card, material))

def test_existing_asset_identity_is_preserved_when_editing_old_card(project):
    card = project.save_asset({"category": "material", "title": "旧材质"})
    legacy_id = "asset_0123456789abcdef0123456789abcdef"
    # Isolated historical-data fixture; production never rewrites old IDs.
    import json
    with project._asset_db(write=True) as db:
        old = json.loads(db.execute("SELECT data FROM assets WHERE id=?", (card["id"],)).fetchone()[0])
        old["id"] = legacy_id
        db.execute("UPDATE assets SET id=?,data=? WHERE id=?", (legacy_id, json.dumps(old, ensure_ascii=False), card["id"]))
    edited = project.save_asset({"id": legacy_id, "prompt": "保留原身份"})
    assert edited["id"] == legacy_id and project.get_asset(legacy_id)["prompt"] == "保留原身份"
    assert project.save_asset({"category": "material", "title": "新材质"})["id"] == "material-2"
    assert legacy_id in {asset["id"] for asset in project.list_assets("material")["assets"]}
