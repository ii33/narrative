"""Pinned real local SDK: steering reaches next step without aborting its tool."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
from PIL import Image

from narrative import assistant, tool_api
from narrative.conversation_runtime import close_project_runtime
from narrative.conversation_session import open_conversation_session
from narrative.conversation_steering import pack_instruction
from narrative.harness import HarnessRunner, HarnessSettings
from narrative.operations import Operations


def test_native_sdk_steering_text_and_image_during_tool_without_interrupt(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'steering.nproj'
    ops = Operations.create_graph_project(entry)
    source = ops.import_source('测试', '先读取这一小段原文。')
    entered, release = threading.Event(), threading.Event()
    requests, errors, receipts = [], [], []
    class LocalModel(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            if len(requests) == 1:
                delta = {'tool_calls': [{'index': 0, 'id': 'blocked-read', 'function': {'name': 'read_source', 'arguments': json.dumps({'source_id': source})}}]}
                finish = 'tool_calls'
            else:
                serialized = json.dumps(request, ensure_ascii=False)
                if 'STEER-CODE' not in serialized:
                    errors.append({'reason': 'Next model request lost native steering text or image', 'last_messages': request.get('messages', [])[-3:], 'receipts': list(receipts)})
                delta, finish = {'content': 'received STEER-CODE'}, 'stop'
            frames = [{'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}, {'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}]
            body = (''.join('data: ' + json.dumps(frame, ensure_ascii=False) + '\n\n' for frame in frames) + 'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), LocalModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings = HarnessSettings(provider='custom', model='offline', base_url=f'http://127.0.0.1:{server.server_port}/v1')
    wrapper, session = None, None
    result = {}
    def handler(name, args):
        entered.set()
        assert release.wait(8)
        return tool_api.call(name, args, project=entry)
    try:
        wrapper, session = open_conversation_session(ops, settings, 'local-only', HarnessRunner, task_id='steering-task', task_tools=assistant.tool_catalog(['read_source']), tool_handler=handler)
        runtime = wrapper.manager
        subscription = wrapper.runner._harness.client.subscribe_session_notifications(session)
        subscription.__enter__()
        pid = wrapper.runner._harness.client._proc.pid
        def work():
            try:
                result['reply'] = wrapper.agent_turn('Read the source, then reply.', session)
            except Exception as exc:
                result['error'] = exc
        thread = threading.Thread(target=work)
        thread.start()
        try:
            assert entered.wait(8)
            stream = BytesIO()
            Image.new('RGB', (16, 16), 'blue').save(stream, format='PNG')
            blocks = pack_instruction([{'type': 'text', 'text': '新增要求：STEER-CODE'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()}}])
            ack = runtime.steer(wrapper.chat_id, 'steering-task', wrapper.binding_nonce, blocks, message_id='steer-local-proof', on_status=receipts.append)
            assert ack['status'] == 'queued' and len(requests) == 1
            assert not runtime._binding['cancelled']
        finally:
            release.set()
        thread.join(10)
        assert not thread.is_alive() and 'error' not in result, result
        assert not errors
        events = []
        subscription.drain(lambda n: events.append(n.payload.get('event', {})))
        subscription.close()
        user = next(event['data'] for event in events if event.get('type') == 'user/message' and event.get('data', {}).get('id') == 'steer-local-proof')
        assert any(block.get('type') == 'image' and block.get('attachment') for block in user['content'])
        assert 'image omitted because this model accepts text only' in json.dumps(requests[-1])
        assert len(requests) == 2
        assert result['reply'].raw == 'received STEER-CODE'
        assert [row['status'] for row in receipts] == ['queued', 'delivered']
        assert wrapper.runner._harness.client._proc.pid == pid
        with pytest.raises(ValueError, match='已结束'):
            runtime.steer(wrapper.chat_id, 'steering-task', wrapper.binding_nonce, 'late')
    finally:
        release.set()
        if wrapper is not None:
            wrapper.close()
        close_project_runtime(ops)
        server.shutdown()
        server.server_close()
