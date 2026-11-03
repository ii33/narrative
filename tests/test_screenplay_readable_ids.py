"""Readable identities are automatic, stable, and transactional."""
from __future__ import annotations

import pytest
from test_screenplay_writing import body, table
from test_screenplay_writing import project as project


def test_business_ids_are_readable_and_references_remain_stable(project):
    ops, screenplay_id, segment_id = project
    assert screenplay_id == "screenplay-1"
    assert segment_id == "segment-1"
    document = ops.read_screenplay(screenplay_id)
    assert document["screenplay"]["timeline_id"] == "script-timeline-1"
    assert document["segments"][0]["span_id"] == "span-1"
    scene_table = table(ops, segment_id)
    first, second = scene_table["units"]
    assert [first["id"], second["id"]] == ["unit-1", "unit-2"]
    written = body(ops, first["id"])
    assert written["blocks"][0]["id"] == "block-1"
    assert written["body_basis_id"].startswith("basis-")
    block_id = written["blocks"][0]["id"]
    ops.apply_script_block_changes(first["id"], [{"op": "delete", "block_id": block_id}],
        [block_id], written["body_revision"], reason="删除本场块")
    replacement = body(ops, first["id"])
    assert replacement["blocks"][0]["id"] == "block-2"
    assert replacement["id"] == first["id"]
    assert ops.check_screenplay(screenplay_id)["ok"]


def test_failed_creation_rolls_back_number_and_reset_does_not_reuse_identity(project):
    ops, _screenplay_id, _segment_id = project
    with pytest.raises(ValueError):
        ops.create_screenplay("", reason="无效名称不消耗编号")
    new = ops.create_screenplay("第二份", format="series", reason="有效创建")
    assert new["screenplay"]["id"] == "screenplay-2"
    assert new["timeline"]["id"] == "script-timeline-2"
    assert new["episodes"][0]["id"] == "episode-1"
    assert new["structure_entries"][0]["id"].startswith("structure-")
    inspection = ops.inspect_screenplay_storage()
    ops.reset_screenplay_storage("清空隔离剧本", "screenplay", inspection["expected_storage_fingerprint"], True)
    after = ops.create_screenplay("重建后", reason="不复用已成功分配身份")
    assert after["screenplay"]["id"] == "screenplay-3"
    assert after["timeline"]["id"] == "script-timeline-3"
