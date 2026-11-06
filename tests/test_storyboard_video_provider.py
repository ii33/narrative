"""H3 transport and intent contracts against a local HTTP server, never H3."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from PIL import Image

from narrative.storyboard_video_provider import build_request, query, submit


def test_reference_and_endpoint_payloads_follow_published_roles(tmp_path):
    image = tmp_path / 'style.png'
    Image.new('RGB', (320, 320), 'gray').save(image)
    motion = tmp_path / 'reference.mp4'
    motion.write_bytes(b'local protocol fixture; Operations probes real media separately')
    options = {'model': 'MiniMax-H3', 'resolution': '768P', 'duration_ms': 4000, 'prompt': '窗缓慢下降'}
    reference = build_request(**options, mode='reference_motion', images=[image], motion_path=motion)
    assert [item.get('role') for item in reference['content'][1:]] == ['reference_video', 'reference_image']
    assert reference['content'][1]['video_url']['url'].startswith('data:video/mp4;base64,')
    endpoints = build_request(**options, mode='endpoints', images=[image, image])
    assert [item.get('role') for item in endpoints['content'][1:]] == ['first_frame', 'last_frame']
    assert endpoints['ratio'] == 'adaptive' and reference['duration'] == 4
    for override in ({'model': 'MiniMax-H3-Max'}, {'duration_ms': 3500}, {'model': 'MiniMax-H3-Lite', 'resolution': '2K'}):
        with pytest.raises(ValueError):
            build_request(**(options | override), mode='endpoints', images=[image])
    with pytest.raises(ValueError, match='不与运动参考混用'):
        build_request(**options, mode='endpoints', images=[image], motion_path=motion)


@pytest.mark.parametrize('status', [200, 302, 500])
def test_http_submission_is_single_and_credentials_never_follow_redirects(status):
    seen = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append((self.path, payload, self.headers.get('Authorization')))
            body = json.dumps({'task_id': 'service-task-1'} if status == 200 else {'message': 'test-video-secret'}).encode()
            self.send_response(status)
            if status == 302:
                self.send_header('Location', '/do-not-follow')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen.append((self.path, None, self.headers.get('Authorization')))
            body = json.dumps({'task': {'id': 'service-task-1', 'status': 'running'}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Endpoint) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = 'http://127.0.0.1:' + str(server.server_port)
            if status == 200:
                assert submit(address, 'test-video-secret', {'content': []})['task_id'] == 'service-task-1'
                assert query(address, 'test-video-secret', 'service-task-1')['task']['status'] == 'running'
            else:
                with pytest.raises(ValueError) as error:
                    submit(address, 'test-video-secret', {'content': []})
                assert 'test-video-secret' not in str(error.value)
        finally:
            server.shutdown()
            thread.join()
    assert sum(path == '/v2/video_generation' for path, _, _ in seen) == 1
    assert not any('do-not-follow' in path for path, _, _ in seen)
    assert seen[0][2] == 'Bearer test-video-secret'
