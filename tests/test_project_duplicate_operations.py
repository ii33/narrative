"""Public project-copy identity, scope inheritance and real-session entry behavior."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from agent_fixtures import project as project

from narrative.operations import Operations
from narrative.project_package import layout
from narrative.provider_store import GLOBAL_SCOPE


def test_duplicate_audits_new_identity_title_and_inherited_provider_settings(project, tmp_path):
    source = layout(project.project_info().path)
    project.import_source("原文", "副本原文不变。")
    project._provider_store.put(GLOBAL_SCOPE, "recent_projects", '["global sentinel"]')
    project._provider_store.put(project.graph_project_info()["project_id"], "recent_projects", '["not a model setting"]')
    before = project.graph_snapshot()
    destination = tmp_path / "有意义的副本.nproj"
    copy = Operations.duplicate_graph_project(source.entry, destination)
    assert copy.graph_project_info()["project_id"] != project.graph_project_info()["project_id"]
    assert copy.graph_project_info()["title"] == destination.stem
    assert copy.graph_project_info()["revision"] == before["revision"]
    assert copy.graph_snapshot()["nodes"] == before["nodes"] and copy.sources() == project.sources()
    new_id = copy.graph_project_info()["project_id"]
    assert copy._provider_store.get(new_id, "harness_settings") is None
    assert copy._provider_store.get(new_id, "harness_profiles") is None
    assert copy._provider_store.get(new_id, "recent_projects") is None
    assert project._provider_store.get(GLOBAL_SCOPE, "recent_projects") == '["global sentinel"]'
    with copy._store.connect() as db:
        action = db.execute("SELECT action,revision,summary FROM actions ORDER BY rowid DESC LIMIT 1").fetchone()
    assert action["action"] == "project_copy" and action["revision"] == before["revision"]
    summary = json.loads(action["summary"])
    assert summary["source_project_id"] == project.graph_project_info()["project_id"]
    assert summary["destination_entry"] == str(destination.resolve())
    assert Operations.inspect_graph_project(destination)["status"] == "ready"
    assert project.graph_snapshot() == before


def test_copy_suggestion_and_path_checks_do_not_overwrite_or_recurse(project, tmp_path):
    source = layout(project.project_info().path)
    suggested = Operations.next_project_copy_path(source.entry)
    assert suggested.name == source.entry.stem + "-副本.nproj"
    occupied = layout(suggested).chats
    occupied.mkdir()
    marker = occupied / "user.txt"
    marker.write_text("do not replace", encoding="utf-8")
    assert Operations.next_project_copy_path(source.entry) != suggested
    with pytest.raises(ValueError, match="已存在"):
        Operations.duplicate_graph_project(source.entry, suggested)
    with pytest.raises(ValueError, match="重叠"):
        Operations.duplicate_graph_project(source.entry, source.root / "nested.nproj")
    assert marker.read_text(encoding="utf-8") == "do not replace"
    assert not (source.root / "nested.nproj").exists()
    destination = tmp_path / "busy.nproj"
    with project._store.connect(write=True):
        with pytest.raises(ValueError, match="正被写入"):
            Operations.duplicate_graph_project(source.entry, destination)
    assert not destination.exists() and not layout(destination).root.exists()
    assert Operations.inspect_graph_project(tmp_path / "missing.nproj")["status"] == "missing"


def test_copy_resumes_copied_sdk_session_and_host_writes_only_new_project(project, tmp_path):
    from narrative import tool_api
    from narrative.assistant import tool_catalog
    from narrative.chat_store import ChatStore
    from narrative.conversation_runtime import close_project_runtime
    from narrative.conversation_session import open_conversation_session
    from narrative.harness import HarnessSettings

    source = layout(project.project_info().path)
    chat_store = ChatStore(Path(project.project_info().path))
    chat = chat_store.create("已有SDK会话")
    sdk = chat_store.sdk_directory()
    marker = sdk / "runtime-state" / (chat["session"] + ".json")
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b'{"opaque_sdk_context":"not reconstructed from chat text"}')
    session_manifest = sdk / "chats" / chat["id"] / "manifest.json"
    session_manifest.parent.mkdir(parents=True)
    session_manifest.write_text(json.dumps({"version": 2, "project_id": project.graph_project_info()["project_id"],
        "chat_id": chat["id"], "session_id": chat["session"], "turn_count": 1, "sdk_directory": str(sdk)}), encoding="utf-8")
    settings = HarnessSettings(provider="custom", model="offline-copy", base_url="http://127.0.0.1:1/v1")
    observed = []
    class Runner:
        def __init__(self, _settings, _key, directory, **_kwargs):
            self.directory = Path(directory)
            self.active_session = None
            self._closed = False
        def resume(self, session):
            file = self.directory / "runtime-state" / (session + ".json")
            observed.append((self.directory, session, file.read_bytes()))
            return True
        def close(self, **_kwargs):
            self._closed = True
    copy = None
    try:
        original_runner, original_session = open_conversation_session(project, settings, "offline", Runner,
            task_tools=tool_catalog([]), tool_handler=lambda _name, _args: {})
        original_runner.close()
        copy = Operations.duplicate_graph_project(source.entry, tmp_path / "resumable.nproj")
        copied_layout = layout(copy.project_info().path)
        runner, resumed = open_conversation_session(copy, settings, "offline", Runner, chat_id=chat["id"],
            task_tools=tool_catalog({"create_screenplay"}),
            tool_handler=lambda name, args: tool_api.call(name, args, project=copied_layout.entry))
        try:
            assert resumed == original_session == chat["session"]
            assert observed[-1][0] == copied_layout.extraction / "assistant"
            assert observed[-1][2] == marker.read_bytes()
            address = urlsplit(runner.manager.host.config["url"])
            connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
            try:
                connection.request("POST", "/call", json.dumps({"session_id": resumed, "nonce": runner.manager._binding["nonce"],
                    "name": "create_screenplay", "args": {"title": "只写副本", "reason": "复制后续用真实会话入口"}}),
                    {"Authorization": "Bearer " + runner.manager.host.token, "Content-Type": "application/json"})
                response = connection.getresponse()
                payload = json.loads(response.read())
                assert response.status == 200, payload
                assert payload["result"]["screenplay"]["title"] == "只写副本"
            finally:
                connection.close()
            assert project.list_screenplays() == []
            assert [row["title"] for row in copy.list_screenplays()] == ["只写副本"]
        finally:
            runner.close()
    finally:
        close_project_runtime(project)
        if copy is not None:
            close_project_runtime(copy)
