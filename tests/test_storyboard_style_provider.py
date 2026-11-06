"""Protocol and late-result checks for approved sketch-to-style rendering."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from PIL import Image
from test_screenplay_writing import body, table
from test_screenplay_writing import project as project

from narrative.storyboard_style_provider import render_sketch_image, render_style_image


@pytest.mark.parametrize('with_references', [False, True])
def test_comfy_http_contract_and_expected_output(tmp_path, monkeypatch, with_references):
    sketch = tmp_path / "line.png"
    Image.new("RGB", (32, 32), "gray").save(sketch)
    seen = []
    body_bytes = sketch.read_bytes()

    class Endpoints(BaseHTTPRequestHandler):
        def answer(self, value):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, payload))
            if self.path == "/upload/image":
                self.answer({"name": "input.png", "subfolder": "narrative", "type": "input"})
            elif self.path == "/prompt":
                self.answer({"prompt_id": "jobabc", "node_errors": {}})
            else:
                self.send_error(404)

        def do_GET(self):
            if self.path == "/history/jobabc":
                self.answer({"jobabc": {"status": {"status_str": "success", "completed": True},
                                        "outputs": {"12": {"images": [{"filename": "image.png", "type": "output"}]}}}})
            elif self.path.startswith("/view?"):
                self.answer(body_bytes)
            else:
                self.send_error(404)

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Endpoints) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        monkeypatch.setenv("NARRATIVE_COMFY_URL", "http://127.0.0.1:" + str(server.server_port))
        monkeypatch.delenv("NARRATIVE_COMFY_WORKFLOW", raising=False)
        monkeypatch.delenv("NARRATIVE_COMFY_SKETCH_WORKFLOW", raising=False)
        try:
            target = tmp_path / "styled.png"
            if with_references:
                reference = tmp_path / 'appearance.png'
                Image.new('RGB', (16, 32), 'blue').save(reference)
                receipt = render_sketch_image(sketch, 'descriptive pencil sketch', target, [reference])
            else:
                receipt = render_style_image(sketch, "warm film light", target)
        finally:
            server.shutdown()
            worker.join()
    assert [call[0] for call in seen] == ["/upload/image"] * (2 if with_references else 1) + ["/prompt"]
    assert body_bytes in seen[0][1]
    graph = json.loads(seen[-1][1])["prompt"]
    assert graph["1"]["inputs"]["image"] == "narrative/input.png"
    assert graph["5"]["inputs"]["text"] == ('descriptive pencil sketch' if with_references else "warm film light")
    assert type(graph['10']['inputs']['seed']) is int
    if with_references:
        assert reference.read_bytes() in seen[1][1]
        ref_node = graph['7']['inputs']['images.image1'][0]
        assert graph[ref_node]['class_type'] == 'LoadImage'
        assert receipt['input_image_count'] == 2
    assert receipt['raw_request']['prompt'] == graph and receipt['raw_response']['status']['completed']
    assert receipt['usage'] is None
    assert receipt["job_id"] == "jobabc" and target.is_file()


def test_project_style_change_during_render_rejects_late_image(project, tmp_path, monkeypatch):
    from narrative import storyboard_style_provider
    ops, _, segment = project
    unit = table(ops, segment)["units"][0]["id"]
    block = body(ops, unit)["blocks"][0]["id"]
    board = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "等候", "source_block_ids": [block]}])
    shot = board["shots"][0]["id"]
    Image.new("RGB", (32, 32), "gray").save(tmp_path / "line.png")
    board = ops.apply_storyboard_changes(unit, 1, [{"op": "set_keyframe", "shot_id": shot, "offset_ms": 0, "sketch_path": str(tmp_path / "line.png")},
                                                   {"op": "set_approved", "approved": True}])
    frame = board["shots"][0]["keyframes"][0]
    ops.save_project_style("水彩", 0)

    def change_style(sketch, prompt, output):
        assert sketch == Path(frame["sketch_path"]) and "水彩" in prompt
        ops.save_project_style("木刻", 1)
        Image.new("RGB", (32, 32), "red").save(output)
        return {"provider": "protocol test"}

    monkeypatch.setattr(storyboard_style_provider, "render_style_image", change_style)
    with pytest.raises(ValueError, match="风格已变化"):
        ops.render_storyboard_style(unit, shot, frame["id"], 2)
    assert ops.read_storyboard(unit) == board
