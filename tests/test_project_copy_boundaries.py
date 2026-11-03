"""Full project copies own their media, profile, context and artifact references."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from agent_fixtures import TEXT, draft_result

from narrative import tool_api
from narrative.chat_store import ChatStore
from narrative.operations import Operations
from narrative.project_package import layout, read_entry
from narrative.screenplay_store import read_context


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "provider-config.sqlite3"))
    entry = tmp_path / "source" / "原工程.nproj"
    ops = Operations.create_graph_project(entry)
    source_id = ops.import_source("复制合成原文", TEXT)
    assert ops.submit_extraction(source_id, draft_result(ended=False))["status"] == "accepted"
    adaptation = ops.sync_adaptation()
    person = next(node["id"] for node in adaptation["nodes"] if node["node_type"] == "person")
    events = [node["id"] for node in adaptation["nodes"] if node["node_type"] == "event"]
    screenplay = ops.create_screenplay("复制剧本", reason="隔离复制fixture")["screenplay"]
    local = ops.import_screenplay_timeline(screenplay["id"], events, 0, adaptation["revision"], "fixture独立时间线")
    span = ops.create_adaptation_span(screenplay["id"], "复制范围", selection_ids=events,
        expected_timeline_revision=local["revision"], reason="fixture范围")["span"]
    segment = ops.create_script_segment(screenplay["id"], span["id"], "复制段落", screenplay["structure_revision"], "fixture段落")
    context = ops.read_scene_table_context(segment["id"])
    unit = ops.submit_scene_table(segment["id"], [{"plan": {"summary": "甲等候乙并一起离开。"}}],
        context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"], reason="fixture场表")["units"][0]
    found = layout(entry)
    owned_library = found.root / "Library" / "原始附件.txt"
    owned_library.parent.mkdir(parents=True)
    owned_library.write_bytes(b"project-owned original attachment")
    task_file = found.extraction / "assistant" / "tasks" / "fixture-task" / "writing-task.json"
    task_file.parent.mkdir(parents=True)
    raw_task = json.dumps({"prompt": "用户原话中的源路径必须保留：" + str(entry), "source_project_id": ops.graph_project_info()["project_id"]}, ensure_ascii=False).encode()
    task_file.write_bytes(raw_task)
    context = ops.read_script_unit_context(unit["id"])
    ops.submit_script_unit(unit["id"], [{"type": "action", "payload": {"text": "不可改写的源工程正文。"}}],
        context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"], context["context_fingerprint"], context["plan_fingerprint"],
        reason="fixture正文", task_id="fixture-task", manifest_ref=str(task_file))
    library = tmp_path / "Library" / "原始参考.png"
    library.parent.mkdir()
    library.write_bytes(b"original Library file")
    card = ops.material_add_card(person, "原作资料卡")
    material_image = ops.material_add_image(person, card["id"], library)
    asset = ops.save_asset({"category": "material", "title": "树皮材质", "reference_images": [{"path": str(library), "caption": "原参考"}]})
    settings = ops.harness_settings()
    ops._provider_store.put("__global__", "harness_settings", json.dumps(settings | {"model": "unrelated-global-model"}))
    ops.save_harness_settings(settings | {"model": "project-copy-fixture-model"})
    chat_store = ChatStore(ops.path)
    chat = chat_store.create("保留真实上下文")
    chat_store.append(chat["id"], "user", "用户文字不应被替换：" + str(entry))
    chat_store.append(chat["id"], "assistant", "已保存历史。", sdk_turn=True)
    sdk = chat_store.sdk_directory()
    manifest = sdk / "chats" / chat["id"] / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": 2, "project_id": ops.graph_project_info()["project_id"], "chat_id": chat["id"],
        "session_id": chat["session"], "turn_count": 1, "business_actions_applied": 0, "sdk_directory": str(sdk)}, ensure_ascii=False), encoding="utf-8")
    history = sdk / "home" / "sessions" / chat["session"] / "session.v4.jsonl"
    history.parent.mkdir(parents=True)
    raw_history = ("SDK_HISTORY_MUST_NOT_BE_RECONSTRUCTED " + str(entry)).encode()
    history.write_bytes(raw_history)
    public = tool_api.call("read_script_unit_context", {"unit_id": unit["id"]}, project=entry)
    return {"ops": ops, "entry": entry, "person": person, "screenplay": screenplay["id"], "segment": segment["id"], "unit": unit["id"],
        "library": library, "card": card, "material_image": material_image, "asset": asset, "chat": chat,
        "manifest": manifest, "history": history, "raw_history": raw_history, "task_file": task_file, "raw_task": raw_task, "public": public,
        "owned_library": owned_library}


def destination(tmp_path):
    path = tmp_path / "target" / "独立副本.nproj"
    path.parent.mkdir(exist_ok=True)
    return path


def test_media_relative_paths_and_original_library_are_independent(source, tmp_path):
    original = source["ops"]
    target = destination(tmp_path)
    copied = Operations.duplicate_graph_project(source["entry"], target)
    source_info, target_info = original.graph_project_info(), copied.graph_project_info()
    assert source_info["project_id"] != target_info["project_id"]
    assert read_entry(target)["project"]["id"] == target_info["project_id"]
    assert copied.graph_snapshot() == original.graph_snapshot()
    assert copied.adaptation_snapshot() == original.adaptation_snapshot()
    target_layout, source_layout = layout(target), layout(source["entry"])
    copied_card = copied.material_view(source["person"])["cards"][0]
    image = copied_card["images"][0]
    assert image["file"] == source["material_image"]["file"]
    assert Path(image["path"]).is_relative_to(target_layout.material)
    assert Path(image["path"]).read_bytes() == b"original Library file"
    copied_asset = copied.get_asset(source["asset"]["id"])
    reference = copied_asset["reference_images"][0]
    assert reference["file"] == source["asset"]["reference_images"][0]["file"]
    assert Path(reference["path"]).is_relative_to(target_layout.material)
    copied.material_remove_card(source["person"], source["card"]["id"])
    assert Path(source["material_image"]["path"]).is_file()
    original.save_asset({"id": source["asset"]["id"], "title": "源工程后来修改"})
    assert copied.get_asset(source["asset"]["id"])["title"] == "树皮材质"
    assert source["library"].read_bytes() == b"original Library file"
    copied_original = target_layout.root / source["owned_library"].relative_to(source_layout.root)
    assert copied_original.read_bytes() == b"project-owned original attachment"
    copied_original.write_bytes(b"changed only in copy")
    assert source["owned_library"].read_bytes() == b"project-owned original attachment"
    assert target_layout.material != source_layout.material


def test_profiles_sdk_manifests_and_business_basis_point_to_copy_without_rewriting_evidence(source, tmp_path):
    original = source["ops"]
    global_before = original._provider_store.get("__global__", "harness_settings")
    target = destination(tmp_path)
    copied = Operations.duplicate_graph_project(source["entry"], target)
    assert copied.harness_settings() == original.harness_settings()
    assert copied.harness_settings()["model"] == "project-copy-fixture-model"
    copied.save_harness_settings(copied.harness_settings() | {"model": "copy-only-model"})
    assert original.harness_settings()["model"] == "project-copy-fixture-model"
    assert original._provider_store.get("__global__", "harness_settings") == global_before
    source_layout, target_layout = layout(source["entry"]), layout(target)
    copied_chat_store = ChatStore(copied.path)
    assert copied_chat_store.load(source["chat"]["id"])["messages"] == ChatStore(original.path).load(source["chat"]["id"])["messages"]
    new_manifest = target_layout.extraction / source["manifest"].relative_to(source_layout.extraction)
    manifest = json.loads(new_manifest.read_text(encoding="utf-8"))
    assert manifest["project_id"] == copied.graph_project_info()["project_id"]
    assert Path(manifest["sdk_directory"]) == copied_chat_store.sdk_directory()
    history = target_layout.extraction / source["history"].relative_to(source_layout.extraction)
    assert history.read_bytes() == source["raw_history"]
    state, _adaptation = read_context(copied)
    basis = next(row for row in state["script_writing_bases"].values() if row["target_kind"] == "unit" and row["target_id"] == source["unit"])
    copied_task = target_layout.extraction / source["task_file"].relative_to(source_layout.extraction)
    assert Path(basis["manifest_ref"]) == copied_task
    assert copied_task.read_bytes() == source["raw_task"]
    assert source["task_file"].read_bytes() == source["raw_task"]
    assert copied.read_script_unit_context(source["unit"])["unit"]["basis_state"] == "current"


def test_fingerprint_handles_are_project_bound_and_new_copy_can_issue_fresh_handles(source, tmp_path):
    from narrative.tool_fingerprint_refs import decode_tool_parameters

    target = destination(tmp_path)
    copied = Operations.duplicate_graph_project(source["entry"], target)
    old_handle = source["public"]["context_fingerprint"]
    with pytest.raises(ValueError, match="依据|工程|缓存"):
        decode_tool_parameters(copied, {"context_fingerprint": old_handle})
    new = tool_api.call("read_script_unit_context", {"unit_id": source["unit"]}, project=target)
    assert new["context_fingerprint"] != old_handle
    assert decode_tool_parameters(copied, {"context_fingerprint": new["context_fingerprint"]})["context_fingerprint"] == copied.read_script_unit_context(source["unit"])["context_fingerprint"]


def test_business_writes_and_material_counters_fork_without_touching_the_other_project(source, tmp_path):
    original = source["ops"]
    copied = Operations.duplicate_graph_project(source["entry"], destination(tmp_path))
    node = next(node for node in copied.graph_snapshot()["nodes"] if node["id"] == source["person"])
    old_name = node["name"]
    result = copied.apply_changes([node | {"name": "仅副本更正人物"}], [], copied.graph_project_info()["revision"], "副本独立原作更正")
    assert result["status"] == "accepted"
    assert next(node for node in original.graph_snapshot()["nodes"] if node["id"] == source["person"])["name"] == old_name
    original.edit_adaptation_node(source["person"], {"name": "仅源工程改编名称"})
    assert next(node for node in copied.adaptation_snapshot()["nodes"] if node["id"] == source["person"])["name"] == old_name
    source_unit = original.read_script_unit_context(source["unit"])["unit"]
    unit = copied.read_script_unit_context(source["unit"])["unit"]
    block = unit["blocks"][0]
    copied.apply_script_block_changes(unit["id"], [{"op": "update", "block_id": block["id"],
        "block": {"type": "action", "payload": {"text": "只在副本修改的正文。"}}}], [block["id"]], unit["body_revision"], reason="副本独立正文编辑")
    assert original.read_script_unit_context(source["unit"])["unit"]["blocks"] == source_unit["blocks"]
    source_card = original.material_add_card(source["person"], "源分支新卡")
    copy_card = copied.material_add_card(source["person"], "副本分支新卡")
    assert source_card["id"] == copy_card["id"]  # Stable per-project scopes fork independently.
    assert original.material_view(source["person"])["cards"][-1]["title"] == "源分支新卡"
    assert copied.material_view(source["person"])["cards"][-1]["title"] == "副本分支新卡"


def test_committed_wal_data_in_graph_and_asset_database_are_included(source, tmp_path):
    graph = sqlite3.connect(source["ops"].path)
    assets = sqlite3.connect(layout(source["entry"]).material / "assets" / "assets.sqlite3")
    try:
        for db in (graph, assets):
            assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
            db.execute("PRAGMA wal_autocheckpoint=0")
        graph.execute("INSERT INTO meta(key,value) VALUES('copy_wal_sentinel','committed only via WAL')")
        graph.commit()
        row = assets.execute("SELECT data FROM assets WHERE id=?", (source["asset"]["id"],)).fetchone()
        data = json.loads(row[0]) | {"title": "WAL中已提交的资产"}
        assets.execute("UPDATE assets SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), source["asset"]["id"]))
        assets.commit()
        assert Path(str(source["ops"].path) + "-wal").is_file()
        copied = Operations.duplicate_graph_project(source["entry"], destination(tmp_path))
        assert copied._store.meta("copy_wal_sentinel") == "committed only via WAL"
        assert copied.get_asset(source["asset"]["id"])["title"] == "WAL中已提交的资产"
    finally:
        assets.close()
        graph.close()


@pytest.mark.parametrize("which", ["entry", "root", "extraction", "chats"])
def test_existing_destination_component_is_never_overwritten_or_removed(source, tmp_path, which):
    target = destination(tmp_path)
    found = layout(target)
    collision = {"entry": found.entry, "root": found.root, "extraction": found.extraction, "chats": found.chats}[which]
    if which == "entry":
        collision.write_bytes(b"preexisting user entry must survive")
        sentinel = collision
    else:
        collision.mkdir(parents=True)
        sentinel = collision / "preexisting-user-file.txt"
        sentinel.write_bytes(b"preexisting user directory must survive")
    before = sentinel.read_bytes()
    graph_before = source["ops"].graph_snapshot()
    files_before = {path.relative_to(target.parent): path.read_bytes() for path in target.parent.rglob("*") if path.is_file()}
    with pytest.raises((ValueError, FileExistsError), match="存在|覆盖|exist"):
        Operations.duplicate_graph_project(source["entry"], target)
    assert sentinel.read_bytes() == before
    assert {path.relative_to(target.parent): path.read_bytes() for path in target.parent.rglob("*") if path.is_file()} == files_before
    assert source["ops"].graph_snapshot() == graph_before


@pytest.mark.parametrize("which", ["task", "material"])
def test_copy_refuses_an_active_source_writer_instead_of_taking_inconsistent_sidecars(source, tmp_path, which):
    from narrative.leases import ProjectLease

    found = layout(source["entry"])
    path = found.extraction / "assistant" / "task.lock" if which == "task" else found.material / ".write.lock"
    lease = ProjectLease(path)
    target = destination(tmp_path)
    try:
        with pytest.raises(ValueError, match="正在|占用|写入|使用"):
            Operations.duplicate_graph_project(source["entry"], target)
        assert not target.exists() and not layout(target).root.exists()
    finally:
        lease.release()


def _provider_rows(ops):
    from contextlib import closing
    with closing(sqlite3.connect(ops._provider_store.path)) as db:
        return list(db.execute("SELECT project_id,key,value FROM provider_settings ORDER BY project_id,key"))


def test_copying_failure_removes_staging_without_changing_source_or_profiles(source, tmp_path, monkeypatch):
    from narrative import project_copy

    target = destination(tmp_path)
    provider_before = _provider_rows(source["ops"])
    graph_before = source["ops"].graph_snapshot()
    adaptation_before = source["ops"].adaptation_snapshot()
    copied_file = project_copy._copy_file
    observed = []

    def fail_mid_copy(incoming, outgoing):
        observed.append(str(incoming))
        copied_file(incoming, outgoing)
        raise OSError("injected copy failure after file was staged")

    monkeypatch.setattr(project_copy, "_copy_file", fail_mid_copy)
    with pytest.raises(OSError, match="injected copy"):
        Operations.duplicate_graph_project(source["entry"], target)
    assert observed and list(target.parent.iterdir()) == []
    assert _provider_rows(source["ops"]) == provider_before
    assert source["ops"].graph_snapshot() == graph_before
    assert source["ops"].adaptation_snapshot() == adaptation_before
    assert source["library"].read_bytes() == b"original Library file"


def test_partial_descriptor_publication_failure_removes_claimed_entry_and_rolls_back_profile_scope(source, tmp_path, monkeypatch):
    from narrative import project_copy

    target = destination(tmp_path)
    provider_before = _provider_rows(source["ops"])
    graph_before = source["ops"].graph_snapshot()
    observed = []

    def fail_after_claiming_entry(path, **_kwargs):
        with Path(path).open("x", encoding="utf-8") as stream:
            stream.write('{"partial_descriptor":')
        observed.append(Path(path))
        raise OSError("injected descriptor write failure")

    monkeypatch.setattr(project_copy, "write_project", fail_after_claiming_entry)
    with pytest.raises(OSError, match="descriptor write failure"):
        Operations.duplicate_graph_project(source["entry"], target)
    assert observed and not target.exists()
    assert list(target.parent.iterdir()) == []
    assert _provider_rows(source["ops"]) == provider_before
    assert source["ops"].graph_snapshot() == graph_before


def test_another_actor_claiming_entry_during_publication_is_preserved(source, tmp_path, monkeypatch):
    from narrative import project_copy

    target = destination(tmp_path)
    provider_before = _provider_rows(source["ops"])
    observed = []

    def another_actor_wins(_staged, published):
        assert Path(published) == target
        with Path(published).open("xb") as stream:
            stream.write(b"another actor owns this entry")
        observed.append(True)
        raise FileExistsError("injected concurrent destination claim")

    monkeypatch.setattr(project_copy.os, "link", another_actor_wins)
    with pytest.raises(FileExistsError, match="concurrent"):
        Operations.duplicate_graph_project(source["entry"], target)
    assert observed and target.read_bytes() == b"another actor owns this entry"
    assert list(target.parent.iterdir()) == [target]
    assert _provider_rows(source["ops"]) == provider_before
