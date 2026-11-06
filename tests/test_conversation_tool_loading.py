"""Per-chat tool loading changes schema visibility, never the captured authority."""
import json

import pytest
from test_assistant_access_runtime import ResidentStub
from test_native_model_diagnostics import require_runtime, stub_model, tool_delta

from narrative import assistant, tool_api
from narrative.conversation_runtime import close_project_runtime, get_runtime
from narrative.harness import HarnessSettings
from narrative.operations import Operations


def test_runtime_loaded_tools_are_chat_local_persist_across_tasks_and_filter_mode(tmp_path, monkeypatch):
    from narrative.tool_loading import CORE_TOOL_NAMES

    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'tools.nproj'
    ops = Operations.create_graph_project(entry)
    settings = HarnessSettings(provider='custom', model='offline', base_url='http://127.0.0.1:1/v1')
    runtime = get_runtime(ops, settings, 'offline', ResidentStub)
    calls = []

    def dispatch(name, args):
        result = tool_api.call(name, args, project=entry)
        calls.append((name, args, result))
        return result

    def invoke(name, args):
        return runtime._dispatch(name, args, runtime._binding['session_id'], runtime._binding['nonce'])

    try:
        all_tools = assistant.tool_catalog(access_mode='full')
        with runtime.bind('chat-A', all_tools, dispatch, access_mode='full'):
            assert set(runtime._model_tool_allowlist()) == set(CORE_TOOL_NAMES)
            assert 'read_screenplay' in runtime._binding['allowed']
            invoke('load_tools', {'names': ['read_screenplay', 'read_host_file']})
            assert {'read_screenplay', 'read_host_file'} <= set(runtime._model_tool_allowlist())
            prefix = runtime._model_tool_allowlist()
            invoke('load_tools', {'families': ['assets']})
            assert runtime._model_tool_allowlist()[:len(prefix)] == prefix
            assert {'list_assets', 'save_asset', 'get_asset'} <= set(runtime._model_tool_allowlist())
            first_runner = runtime.runner
        with runtime.bind('chat-B', all_tools, dispatch, access_mode='full'):
            assert set(runtime._model_tool_allowlist()) == set(CORE_TOOL_NAMES)
        with runtime.bind('chat-A', all_tools, dispatch, access_mode='full'):
            assert {'read_screenplay', 'read_host_file'} <= set(runtime._model_tool_allowlist())
            assert runtime.runner is first_runner
        with runtime.bind('chat-A', all_tools, dispatch, access_mode='standard'):
            assert 'read_screenplay' in runtime._model_tool_allowlist()
            assert 'read_host_file' not in runtime._model_tool_allowlist()
            before = runtime._model_tool_allowlist()
            with pytest.raises(ValueError):
                invoke('load_tools', {'names': ['read_host_file']})
            assert runtime._model_tool_allowlist() == before
            # The load callback itself also enforces exact binding and permissions.
            with pytest.raises(ValueError):
                runtime._load_tools(runtime._binding, ['read_host_file'])
        with runtime.bind('chat-A', all_tools, dispatch, access_mode='full'):
            assert 'read_host_file' not in runtime._model_tool_allowlist()
            assert 'read_screenplay' in runtime._model_tool_allowlist()
            stale = runtime._binding
        with pytest.raises(ValueError):
            runtime._load_tools(stale, ['read_source'])
        assert [name for name, _, _ in calls] == ['load_tools', 'load_tools']
    finally:
        close_project_runtime(ops)


@pytest.mark.parametrize("has_screenplay", [True, False])
def test_pinned_native_sdk_loads_tool_for_next_request_same_session(tmp_path, monkeypatch, has_screenplay):
    from test_chat_scroll import transcript_shell

    from narrative.tool_loading import CORE_TOOL_NAMES

    require_runtime()
    monkeypatch.setenv('TOOL_LOADING_TEST_KEY', 'offline-only')
    calls = []
    pids, sessions = [], []

    def names(request):
        return {row.get('function', {}).get('name') for row in request.get('tools', [])}

    def provider(number, request):
        from narrative.conversation_runtime import get_existing_runtime
        runtime = get_existing_runtime(ops)
        pids.append(runtime.runner._harness.client._proc.pid)
        sessions.append(runtime.runner.active_session)
        if number == 1:
            assert names(request) == set(CORE_TOOL_NAMES)
            assert 'read_screenplay' not in names(request)
            assert 'list_available_tools' in str(request['messages']) and 'load_tools' in str(request['messages'])
            return tool_delta('load_tools', json.dumps({'names': ['read_screenplay']}), 'load-one')
        if number == 2:
            assert names(request) == set(CORE_TOOL_NAMES) | {'read_screenplay'}
            return tool_delta('read_screenplay', '{}', 'read-one')
        assert number == 3
        last_tool = next(row for row in reversed(request['messages']) if row.get('role') == 'tool')
        assert not last_tool.get('isError'), last_tool
        content = last_tool['content']
        if isinstance(content, list):
            content = ''.join(block.get('text', '') for block in content if block.get('type') == 'text')
        assert 'returned invalid output' not in content, content
        value = json.loads(content)
        assert isinstance(value, list), value
        if has_screenplay:
            assert len(value) == 1 and '按需读取剧本' in json.dumps(value, ensure_ascii=False)
        else:
            assert value == []
        return {'role': 'assistant', 'content': '已读取目标剧本。'}, 'stop'

    with stub_model(provider) as (url, requests, errors):
        with transcript_shell(tmp_path, monkeypatch) as ui:
            ops = Operations.open(ui.bridge._project_path)
            if has_screenplay:
                ops.create_screenplay('按需读取剧本', reason='离线工具加载fixture')
            ops.save_provider_profile({'provider':'custom', 'model':'local-stub', 'base_url':url, 'key_env':'TOOL_LOADING_TEST_KEY', 'timeout_seconds':10})
            ui.bridge.newChat()
            ui.wait(lambda: ui.panel.property('chatId') not in {ui.a['id'], ui.b['id'], ''})
            ui.results.clear()
            ui.bridge.assistantTurn('按需加载read_screenplay并读取目标剧本。', '')
            ui.wait(lambda: errors or ui.errors or any(k == 'assistant' for k, _ in ui.results))
            assert not errors and not ui.errors, (errors, ui.errors)
            assert len(requests) == 3, [(k,p) for k,p in ui.results if k in {"assistant","conversation_progress"}]
            calls = [p['step'] for k, p in ui.results if k == 'conversation_progress' and p.get('event') == 'tool_complete']
            assert [row['tool'] for row in calls] == ['load_tools', 'read_screenplay']
            assert all(row['ok'] for row in calls)
            result = next(p for k, p in ui.results if k == 'assistant')
            assert '已读取目标剧本' in result['answer']
            from narrative.conversation_runtime import get_existing_runtime
            runtime = get_existing_runtime(ops)
            assert runtime is not None and not runtime.closed
            assert len(set(pids)) == 1 and len(set(sessions)) == 1 and sessions[0]
