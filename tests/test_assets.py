"""Assets are independent visual cards with validated adaptation time windows."""
import pytest

from narrative.asset_operations import AssetOperationsMixin
from narrative.operations import Operations


@pytest.fixture
def ops(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    project = Operations.create_graph_project(tmp_path / "assets.nproj")
    snapshot = {"nodes": [
        {"id": "P1", "node_type": "person", "name": "角色", "sort_id": 1},
        {"id": "I1", "node_type": "item", "name": "物品", "sort_id": 2},
        {"id": "L1", "node_type": "location", "name": "地点", "sort_id": 3},
        {"id": "E9", "node_type": "event", "name": "先", "sort_id": 10},
        {"id": "E1", "node_type": "event", "name": "后", "sort_id": 20},
    ]}
    # Exercise the public mixin boundary even before composition is installed.
    class TestOperations(AssetOperationsMixin):
        _store = project._store
        def adaptation_snapshot(self):
            return snapshot
        def graph_snapshot(self):
            return project.graph_snapshot()
    return TestOperations(), project


def test_appearance_multiple_cards_and_half_open_window(ops):
    assets, project = ops
    before = project.graph_snapshot()
    first = assets.save_asset({"category": "appearance", "node_id": "P1", "title": "少年",
                               "valid_from": "E9", "valid_to": "E1", "prompt": "水墨"})
    assets.save_asset({"category": "appearance", "node_id": "P1", "title": "成年", "valid_from": "E1"})
    assert [a["title"] for a in assets.list_assets("appearance", "E9", "P1")["assets"]] == ["少年"]
    assert [a["title"] for a in assets.list_assets("appearance", "E1", "P1")["assets"]] == ["成年"]
    assert assets.get_asset(first["id"])["prompt"] == "水墨"
    assert project.graph_snapshot() == before
    assert [node["id"] for node in assets.asset_moments()] == ["E9", "E1"]


def test_validation_and_concurrent_revision_leave_existing_assets(ops):
    assets, _ = ops
    card = assets.save_asset({"category": "material", "title": "铜"})
    with pytest.raises(ValueError, match="已改变"):
        assets.delete_asset(card["id"], expected_revision=0)
    with pytest.raises(ValueError, match="材质"):
        assets.save_asset({"category": "material", "title": "铜", "valid_from": "E9"})
    with pytest.raises(ValueError, match="对应类别"):
        assets.save_asset({"category": "location", "node_id": "P1", "title": "错误"})
    with pytest.raises(ValueError, match="之后"):
        assets.save_asset({"category": "appearance", "node_id": "P1", "title": "错误", "valid_from": "E1", "valid_to": "E9"})
    assert assets.get_asset(card["id"])["revision"] == 1
    assert assets.delete_asset(card["id"], expected_revision=1)["revision"] == 2
    assert assets.list_assets("material")["assets"] == []


def test_reference_images_are_copied_and_originals_survive_deletion(ops, tmp_path):
    assets, _ = ops
    original = tmp_path / "ref.png"
    original.write_bytes(b"test image")
    card = assets.save_asset({"category": "item", "node_id": "I1", "title": "剑",
                              "reference_images": [{"path": str(original), "caption": "参考"}]})
    from pathlib import Path
    imported = Path(card["reference_images"][0]["path"])
    assert imported.read_bytes() == original.read_bytes()
    original.write_bytes(b"changed")
    assert imported.read_bytes() == b"test image"
    card = assets.save_asset({"id": card["id"], "prompt": "青铜长剑"}, expected_revision=1)
    assert len(card["reference_images"]) == 1
    assets.delete_asset(card["id"])
    assert original.read_bytes() == b"changed"


def test_public_operations_asset_crud_keeps_graph_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "public.nproj")
    card = ops.save_asset({"category": "material", "title": "木纹", "prompt": "细密木纹"})
    assert ops.get_asset(card["id"])["title"] == "木纹"
    assert ops.list_assets("material")["assets"][0]["prompt"] == "细密木纹"
    assert ops.graph_project_info()["revision"] == 0
    ops.delete_asset(card["id"])
    assert ops.list_assets("material")["assets"] == []


def test_moved_moment_reports_inverted_asset_window(ops):
    assets, _ = ops
    card = assets.save_asset({"category": "appearance", "node_id": "P1", "title": "少年",
                              "valid_from": "E9", "valid_to": "E1"})
    snapshot = assets.adaptation_snapshot()
    next(node for node in snapshot["nodes"] if node["id"] == "E1")["sort_id"] = 5
    result = assets.list_assets("appearance", "E9")
    assert not result["assets"]
    assert result["diagnostics"] == [{"asset_id": card["id"], "message": "资产时间区间倒置，请调整生效或失效时间"}]
