"""Run one local ComfyUI image-edit workflow for one approved keyframe."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import mimetypes
import os
import secrets
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

from PIL import Image

_DEFAULT = Path(__file__).with_name("workflows") / "storyboard_flux_klein.json"
_SOURCE = "{{SKETCH}}"
_PROMPT = "{{STYLE_PROMPT}}"


def _workflow(path, image_name, prompt, size):
    try:
        raw = path.read_bytes()
        nodes = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取 ComfyUI API 工作流文件：{path}") from exc
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError("请使用 ComfyUI 导出的 API 工作流")
    used = set()
    outputs = []
    replacements = {_SOURCE: image_name, _PROMPT: prompt, "{{WIDTH}}": size[0], "{{HEIGHT}}": size[1], "{{SEED}}": secrets.randbits(63)}
    for key, node in nodes.items():
        if not isinstance(node, dict) or not node.get("class_type") or not isinstance(node.get("inputs"), dict):
            raise ValueError("请使用 ComfyUI 导出的 API 工作流")
        for field, value in node["inputs"].items():
            if isinstance(value, str) and value in replacements:
                used.add(value)
                node["inputs"][field] = replacements[value]
        if node.get("_meta", {}).get("title") == "Storyboard Output":
            outputs.append(key)
    if not {_SOURCE, _PROMPT} <= used or len(outputs) != 1:
        raise ValueError("工作流须含 {{SKETCH}}、{{STYLE_PROMPT}} 和唯一的 Storyboard Output 输出节点")
    return nodes, outputs[0], hashlib.sha256(raw).hexdigest()


class ComfyClient:
    def __init__(self, base):
        addr = urlsplit(base)
        host = addr.hostname or ""
        try:
            local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local or addr.scheme != "http" or addr.username or addr.password or addr.query or addr.fragment:
            raise ValueError("ComfyUI 地址须为本机 HTTP 服务")
        self.host, self.port, self.prefix = host, addr.port or 80, addr.path.rstrip("/")

    def request(self, method, route, body=None, content_type=None) -> bytes:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
        try:
            headers = {"Content-Type": content_type} if content_type else {}
            conn.request(method, self.prefix + route, body=body, headers=headers)
            reply = conn.getresponse()
            if reply.status >= 300:
                diagnostic = reply.read(2000).decode("utf-8", errors="replace")
                raise ValueError(f"ComfyUI {route.split('?')[0]} 返回 {reply.status}：{diagnostic}")
            return reply.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ValueError(f"无法连接本地 ComfyUI：{exc}") from exc
        finally:
            conn.close()

    def json_request(self, method, route, body=None, content_type=None) -> dict:
        value = json.loads(self.request(method, route, body, content_type))
        if not isinstance(value, dict):
            raise ValueError("ComfyUI 返回了无效的状态数据")
        return value

    def upload(self, sketch):
        boundary = uuid4().hex
        name = f"narrative-{uuid4().hex}{sketch.suffix.lower()}"
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        part = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                f"Content-Type: {content_type}\r\n\r\n").encode()
        payload = part + sketch.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        result = self.json_request("POST", "/upload/image", payload, "multipart/form-data; boundary=" + boundary)
        return "/".join(filter(None, [result.get("subfolder"), result["name"]]))

    def complete(self, job):
        path = "/history/" + quote(job, safe="")
        while True:
            record = self.json_request("GET", path).get(job)
            if record:
                return record
            queue = self.json_request("GET", "/queue")
            entries = queue.get("queue_running", []) + queue.get("queue_pending", [])
            if not any(row[1] == job for row in entries):
                # Completion may race the first history read; check the same job once.
                record = self.json_request("GET", path).get(job)
                if record:
                    return record
                raise ValueError(f"ComfyUI 任务 {job} 已离开队列且没有结果")
            time.sleep(1)


def _render_image(sketch, prompt, copies_to, *, workflow_path, reference_images=()):
    path = Path(workflow_path)
    client = ComfyClient(os.environ.get("NARRATIVE_COMFY_URL") or "http://127.0.0.1:8188")
    with Image.open(sketch) as source:
        source.load()
        size = source.size
    # The default diffusion latent dimensions use a 16-pixel grid.
    grid_size = tuple(max(16, round(n / 16) * 16) for n in size)
    nodes, output, fingerprint = _workflow(path, _SOURCE, prompt, grid_size)
    references = [Path(image) for image in reference_images]
    reference_targets = [node for node in nodes.values() if node["class_type"] == "ReferenceMultiLatent"
                         and "images.image0" in node["inputs"] and "vae" in node["inputs"]]
    if references and (len(reference_targets) != 1 or len(references) > 99):
        raise ValueError("额外参考图需要唯一的 ReferenceMultiLatent 多图节点及 VAE；该节点最多接收99张额外参考图")
    for image in references:
        with Image.open(image) as actual:
            actual.verify()
    uploaded = client.upload(sketch)
    for node in nodes.values():
        for key, value in node["inputs"].items():
            if value == _SOURCE:
                node["inputs"][key] = uploaded
    for index, image in enumerate(references, 1):
        node_id = f"narrative_reference_{index}"
        while node_id in nodes:
            node_id += "_input"
        nodes[node_id] = {"class_type": "LoadImage", "inputs": {"image": client.upload(image)}}
        target_inputs = reference_targets[0]["inputs"]
        slot = 1
        while f"images.image{slot}" in target_inputs:
            slot += 1
        if slot >= 100:
            raise ValueError("工作流参考图输入已达到100张，请减少参考图")
        target_inputs[f"images.image{slot}"] = [node_id, 0]
    request = {"prompt": nodes}
    queued = client.json_request("POST", "/prompt", json.dumps(request).encode(), "application/json")
    job = queued.get("prompt_id")
    if not job or queued.get("node_errors"):
        raise ValueError("ComfyUI 拒绝了工作流，请核对已安装模型与节点")
    record = client.complete(job)
    status = record.get("status", {})
    if status.get("status_str") != "success" or not status.get("completed"):
        errors = [info.get("exception_message", name) for name, info in status.get("messages", [])
                  if name in {"execution_error", "execution_interrupted"}]
        raise ValueError(f"ComfyUI 生成失败（{job}）：{'；'.join(errors) or status.get('status_str', '状态未知')}")
    images = record.get("outputs", {}).get(output, {}).get("images", [])
    if len(images) != 1 or images[0].get("type") != "output":
        raise ValueError("工作流最终输出须为一张已保存的图像")
    selector = {k: images[0].get(k, "") for k in ("filename", "subfolder", "type")}
    data = client.request("GET", "/view?" + urlencode(selector))
    with Image.open(BytesIO(data)) as actual:
        actual.load()
        if abs(actual.width - size[0] * actual.height / size[1]) > 16:
            raise ValueError("生成结果与草图画幅不一致，请检查工作流")
        actual.save(copies_to, format="PNG")
    return {"provider": "ComfyUI", "job_id": job, "workflow_sha256": fingerprint,
            "workflow_file": path.name, "output_sha256": hashlib.sha256(data).hexdigest(),
            "output_file": selector["filename"],
            "input_image_count": 1 + len(references), "usage": None,
            "raw_request": request, "raw_submit_response": queued, "raw_response": record}


def render_style_image(sketch, prompt, copies_to, reference_images=()):
    return _render_image(sketch, prompt, copies_to, reference_images=reference_images,
                         workflow_path=os.environ.get("NARRATIVE_COMFY_WORKFLOW") or _DEFAULT)


def render_sketch_image(guide, prompt, copies_to, reference_images=()):
    """Generate a real descriptive sketch, using a layout guide and asset images."""
    return _render_image(guide, prompt, copies_to, reference_images=reference_images,
                         workflow_path=os.environ.get("NARRATIVE_COMFY_SKETCH_WORKFLOW")
                         or os.environ.get("NARRATIVE_COMFY_WORKFLOW") or _DEFAULT)
