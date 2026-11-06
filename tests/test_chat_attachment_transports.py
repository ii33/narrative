"""Explicit user import crosses the file boundary; model catalogs do not."""
import json

from narrative import assistant, mcp_server, tool_api, tool_cli
from narrative.chat_store import ChatStore
from narrative.operations import Operations


def test_explicit_cli_import_preserves_original_and_standard_mcp_cannot_import(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'attachments.nproj'
    Operations.create_graph_project(entry)
    chat = ChatStore(entry).create('附件')
    source = tmp_path / '用户原件.txt'
    source.write_text('参考资料，不覆盖原件', encoding='utf-8')
    assert tool_cli.main(['chat-attachment-import', '--project', str(entry), '--chat-id', chat['id'],
                          '--paths', json.dumps([str(source)]), '--json']) == 0
    imported = json.loads(capsys.readouterr().out)['attachments'][0]
    assert imported['id'] == 'attachment-1' and source.read_text(encoding='utf-8') == '参考资料，不覆盖原件'
    assert imported['path'] != str(source)
    assert tool_cli.main(['chat-attachment-read', '--project', str(entry), '--chat-id', chat['id'],
                          '--attachment-id', imported['id'], '--json']) == 0
    assert json.loads(capsys.readouterr().out)['content'] == '参考资料，不覆盖原件'
    read = mcp_server.handle({'id': 2, 'method': 'tools/call', 'params': {
        'name': 'read_chat_attachment', 'arguments': {'chat_id': chat['id'], 'attachment_id': imported['id']}}},
        default_project=str(entry))
    assert not read['result']['isError']
    for mode in ('standard', 'full'):
        assert 'import_chat_attachments' not in {row['name'] for row in assistant.tool_catalog(access_mode=mode)}
    denied = mcp_server.handle({'id': 1, 'method': 'tools/call', 'params': {
        'name': 'import_chat_attachments', 'arguments': {'chat_id': chat['id'], 'paths': [str(source)]}}},
        default_project=str(entry))
    assert denied['result']['isError']
    assert tool_api.TOOLS['import_chat_attachments'].scope == 'configuration'
