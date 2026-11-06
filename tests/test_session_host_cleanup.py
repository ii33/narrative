"""Host task cleanup receipts survive wrapper close and durable headless audit."""
import json
import subprocess
import sys

import pytest
from test_assistant_access_runtime import ResidentStub

from narrative import tool_api
from narrative.conversation_runtime import close_project_runtime
from narrative.conversation_session import open_conversation_session
from narrative.harness import HarnessSettings
from narrative.operations import Operations


def test_completed_model_with_running_foreground_reports_incomplete_after_close(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'cleanup.nproj'
    ops = Operations.create_graph_project(entry)
    wrapper = None
    try:
        wrapper, session = open_conversation_session(ops, HarnessSettings(provider='custom', model='offline', base_url='http://127.0.0.1:1/v1'),
            'offline', ResidentStub, access_mode='full', tool_handler=lambda name,args: tool_api.call(name,args,project=entry))
        command = subprocess.list2cmdline([sys.executable, '-c', 'import time; time.sleep(20)'])
        started = wrapper.manager._dispatch('run_host_command', {'command': command, 'cwd': str(tmp_path)}, session, wrapper.binding_nonce)
        reply = wrapper.agent_turn('offline natural model finish', session)
        assert reply.metadata['finish_reason'] == 'completed'
        cleanup = wrapper.close()
        assert cleanup == wrapper.host_cleanup == wrapper.close()
        assert len(cleanup['cleanup_receipts']) == 1
        assert cleanup['cleanup_receipts'][0]['command_id'] == started['command_id']
        assert reply.metadata['status'] == 'incomplete' and reply.metadata['finish_reason'] == 'completed'
        assert reply.commit_summary['status'] == 'incomplete'
        assert reply.commit_summary['host_commands_incomplete'] == 1
        assert '部分命令未完成' in reply.commit_summary_text
        turn = next((wrapper.directory / 'turns').glob('*/metadata.json'))
        durable = json.loads(turn.read_text(encoding='utf-8'))
        assert durable['host_cleanup'] == cleanup
        assert durable['status'] == 'incomplete' and durable['finish_reason'] == 'completed'
        assert durable['output_contract_version'] and durable['prompt_sha256']
        assert ops.read_task_write_summary(durable['task_id'], wrapper.chat_id) == reply.commit_summary
    finally:
        if wrapper is not None:
            wrapper.close()
        close_project_runtime(ops)


@pytest.mark.parametrize('cancelled', [False, True])
def test_non_success_cleanup_preserves_failed_or_cancelled_status(tmp_path, monkeypatch, cancelled):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'non-success.nproj'
    ops = Operations.create_graph_project(entry)
    wrapper = None
    try:
        wrapper, session = open_conversation_session(ops, HarnessSettings(provider='custom', model='offline', base_url='http://127.0.0.1:1/v1'),
            'offline', ResidentStub, access_mode='full', tool_handler=lambda name,args: tool_api.call(name,args,project=entry))
        command = subprocess.list2cmdline([sys.executable, '-c', 'import time; time.sleep(20)'])
        wrapper.manager._dispatch('run_host_command', {'command': command, 'cwd': str(tmp_path)}, session, wrapper.binding_nonce)
        reply = wrapper.agent_turn('offline model response before terminal host status', session)
        if cancelled:
            wrapper.manager.signal_stop(wrapper.binding_nonce)
        cleanup = wrapper.close(completed=False)
        expected = 'cancelled' if cancelled else 'failed'
        assert reply.commit_summary['status'] == expected
        assert reply.metadata['finish_reason'] == 'completed'
        assert reply.commit_summary['host_commands_incomplete'] == 0
        assert not any(row.get('code') == 'host_command_stopped_at_task_end' for row in cleanup['diagnostics'])
        assert wrapper.close() == cleanup
    finally:
        if wrapper is not None:
            wrapper.close()
        close_project_runtime(ops)
