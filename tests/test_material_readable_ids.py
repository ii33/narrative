"""Readable original-node material IDs are reserved under an OS writer lock."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event, Thread

import pytest
from agent_fixtures import TEXT, draft_result

from narrative import tool_api
from narrative.node_material import MaterialStore
from narrative.operations import Operations
from narrative.project_package import layout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "materials.nproj")
    sid = ops.import_source("合成", TEXT)
    assert ops.submit_extraction(sid, draft_result(ended=False))["status"] == "accepted"
    person = next(node["id"] for node in ops.graph_snapshot()["nodes"] if node["node_type"] == "person")
    return ops, person


def test_operations_and_tool_ids_survive_deletion_without_reusing_numbers(project, tmp_path):
    ops, person = project
    entry = layout(ops.path).entry
    before = ops.graph_snapshot(), ops.property_records()
    first = tool_api.call("add_material_card", {"node_id": person, "title": "造型"}, project=entry)["card"]
    assert first["id"] == f"{person}-card-1"
    source = tmp_path / "Library 原件.png"
    source.write_bytes(b"keep original")
    image = tool_api.call("add_material_image", {"node_id": person, "card_id": first["id"], "path": str(source)}, project=entry)["image"]
    assert image["id"] == f"{person}-card-1-image-1"
    tool_api.call("delete_material_image", {"node_id": person, "card_id": first["id"], "image_id": image["id"]}, project=entry)
    second_image = ops.material_add_image(person, first["id"], source)
    assert second_image["id"] == f"{person}-card-1-image-2"
    tool_api.call("delete_material_card", {"node_id": person, "card_id": first["id"]}, project=entry)
    second = ops.material_add_card(person, "新造型")
    assert second["id"] == f"{person}-card-2"
    third_image = ops.material_add_image(person, second["id"], source)
    assert third_image["id"] == f"{person}-card-2-image-3"
    assert source.read_bytes() == b"keep original"
    assert (ops.graph_snapshot(), ops.property_records()) == before
    store = MaterialStore(ops.path)
    assert store.load(person)["next_card_number"] == 3 and store.load(person)["next_image_number"] == 4
    assert (store.root / ".write.lock").is_file() and (store.root / ".sequences" / f"{person}.json").is_file()


def test_existing_ids_are_never_rewritten_and_read_projection_never_sweeps_files(project):
    ops, person = project
    store = MaterialStore(ops.path)
    store.root.mkdir(parents=True)
    old = {"node_id": person, "cards": [{"id": "card_0123abcd", "title": "旧资料", "detail": "", "valid_from": "", "valid_to": "", "images": [], "created_at": ""}], "updated_at": ""}
    store.path_for(person).write_text(json.dumps(old), encoding="utf-8")
    orphan = store.root / person / "unreferenced.png"
    orphan.parent.mkdir()
    orphan.write_bytes(b"do not delete on read")
    before = store.path_for(person).read_bytes()
    assert ops.material_view(person)["cards"][0]["id"] == "card_0123abcd"
    assert store.path_for(person).read_bytes() == before and orphan.is_file()
    new = ops.material_add_card(person, "新资料")
    assert new["id"] == f"{person}-card-1"
    assert ops.material_view(person)["cards"][0]["id"] == "card_0123abcd"


def test_failed_publish_keeps_previous_json_and_media_but_burns_reserved_id(project, tmp_path, monkeypatch):
    import narrative.node_material as module

    ops, person = project
    card = ops.material_add_card(person, "现有资料")
    source = tmp_path / "Library.png"
    source.write_bytes(b"original")
    image = ops.material_add_image(person, card["id"], source)
    store = MaterialStore(ops.path)
    target, before = store.path_for(person), store.path_for(person).read_bytes()
    replace = module.os.replace

    def fail_primary(incoming, outgoing):
        if Path(outgoing) == target:
            raise OSError("injected publish failure")
        return replace(incoming, outgoing)

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "replace", fail_primary)
        with pytest.raises(OSError, match="publish"):
            ops.material_update_card(person, card["id"], title="不得覆盖旧资料", images=[{"path": str(source)}])
    assert target.read_bytes() == before and Path(image["path"]).read_bytes() == b"original"
    another_format = tmp_path / "Library.jpg"
    another_format.write_bytes(b"second original")
    third = ops.material_add_image(person, card["id"], another_format)
    assert third["id"] == f"{card['id']}-image-3"
    assert source.read_bytes() == b"original" and another_format.read_bytes() == b"second original"
    assert ops.material_view(person)["cards"][0]["title"] == "现有资料"


def test_failed_card_publish_burns_card_number(project, monkeypatch):
    ops, person = project
    save = MaterialStore._save

    def fail(*_args, **_kwargs):
        raise OSError("injected card save failure")

    with monkeypatch.context() as patch:
        patch.setattr(MaterialStore, "_save", fail)
        with pytest.raises(OSError):
            ops.material_add_card(person, "未发布")
    assert ops.material_view(person)["cards"] == []
    assert ops.material_add_card(person, "已发布")["id"] == f"{person}-card-2"
    assert MaterialStore._save is save


def test_concurrent_writer_is_rejected_without_lost_update_or_counter_reuse(project, monkeypatch):
    ops, person = project
    entered, release = Event(), Event()
    save = MaterialStore._save
    completed, failures = [], []

    def pause(store, node_id, data):
        entered.set()
        assert release.wait(3)
        return save(store, node_id, data)

    def worker():
        try:
            completed.append(ops.material_add_card(person, "第一写者"))
        except Exception as exc:
            failures.append(exc)

    with monkeypatch.context() as patch:
        patch.setattr(MaterialStore, "_save", pause)
        thread = Thread(target=worker)
        thread.start()
        try:
            assert entered.wait(3)
            with pytest.raises(ValueError, match="其他操作写入"):
                ops.material_add_card(person, "并发不得覆盖")
        finally:
            release.set()
            thread.join(3)
    assert not failures and len(completed) == 1
    second = ops.material_add_card(person, "明确稍后另一次写入")
    assert second["id"] == f"{person}-card-2"
    assert [card["title"] for card in ops.material_view(person)["cards"]] == ["第一写者", "明确稍后另一次写入"]


def test_corrupt_material_is_never_silently_replaced(project):
    ops, person = project
    store = MaterialStore(ops.path)
    store.root.mkdir(parents=True)
    store.path_for(person).write_text("{broken", encoding="utf-8")
    before = store.path_for(person).read_bytes()
    with pytest.raises(ValueError, match="损坏"):
        ops.material_add_card(person, "不得清空后重建")
    assert store.path_for(person).read_bytes() == before
