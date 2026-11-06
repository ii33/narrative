"""One real local image job via Operations, never a mock or LLM retry."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

from accept_storyboard import seed
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ["NARRATIVE_PROVIDER_DB"] = str(root / "providers.sqlite3")
    from narrative.operations import Operations
    from narrative.storyboard_style_provider import ComfyClient
    ops = Operations.create_graph_project(root / "图像草图验收.nproj")
    fixture = seed(ops)
    unit = fixture["unit_id"]
    board = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "站前递出铜钥匙，手与钥匙接触清晰", "source_block_ids": [fixture["block"]], "duration_ms": 2000}])
    shot = board["shots"][0]["id"]
    before = {"screenplay": ops.read_screenplay(fixture["screenplay_id"]), "graph": ops.graph_snapshot(), "assets": {c:ops.list_assets(c) for c in ("appearance", "item", "location", "material")}}
    (root / "source-context.json").write_text(json.dumps(ops.read_script_unit_context(unit), ensure_ascii=False, indent=2), encoding="utf-8")
    requests, lock = [], threading.Lock()
    original = ComfyClient.request
    original_upload = ComfyClient.upload
    uploads = []
    def audited_upload(client, image):
        destination = root / f"uploaded-reference-{len(uploads) + 1}.png"
        shutil.copyfile(image, destination)
        uploads.append(str(destination))
        return original_upload(client, image)
    ComfyClient.upload = audited_upload
    def audited(client, method, route, body=None, content_type=None):
        with lock:
            number = len(requests) + 1
            row = {"number": number, "method": method, "route": route, "content_type": content_type}
            requests.append(row)
        if body is not None:
            payload = body.encode() if isinstance(body, str) else body
            (root / f"request-{number}.bin").write_bytes(payload)
            row["request_file"] = f"request-{number}.bin"
            if method == "POST" and route == "/prompt":
                (root / "provider-workflow-request.json").write_bytes(payload)
        try:
            result = original(client, method, route, body, content_type)
            (root / f"response-{number}.bin").write_bytes(result)
            row["response_file"] = f"response-{number}.bin"
            return result
        except Exception as exc:
            row["error"] = str(exc)
            raise
    ComfyClient.request = audited
    report = {"status": "running", "prompt_version": "storyboard-descriptive-image/1", "configuration": {"service": os.environ.get("NARRATIVE_COMFY_URL", "http://127.0.0.1:8188"), "workflow": Path(os.environ.get("NARRATIVE_COMFY_WORKFLOW", "storyboard_flux_klein.json")).name}, "source_sha256": hashlib.sha256(json.dumps(before["screenplay"], ensure_ascii=False, sort_keys=True).encode()).hexdigest(), "provider": "ComfyUI", "llm_requests": 0, "image_jobs": 0, "automatic_retry": False, "usage": None, "usage_note": "Local image service reports no token or billing usage.", "external_observation_seconds": args.seconds, "fixture": fixture, "requests": requests, "uploaded_references": uploads}
    settled = threading.Event()
    started = time.monotonic()
    def execute():
        try:
            report["result"] = ops.generate_storyboard_sketch(unit, shot, 0, {"composition": "two_shot", "framing": "medium", "setting": "exterior", "light": "left", "subjects": [{"kind": "person", "x": 0.3, "y": 0.8, "pose": "reach", "facing": "right"}, {"kind": "person", "x": 0.7, "y": 0.8, "pose": "stand", "facing": "left"}]}, board["revision"])
        except Exception as exc:
            report.update(error_type=type(exc).__name__, error=str(exc), status="not_passed")
        finally:
            settled.set()
    thread = threading.Thread(target=execute, daemon=True)
    thread.start()
    settled.wait(args.seconds)
    report["elapsed_seconds"] = time.monotonic() - started
    report["image_jobs"] = sum(r["method"] == "POST" and r["route"] == "/prompt" for r in requests)
    if not settled.is_set():
        report.update(status="not_completed", stop_reason="External image QA observation budget reached; no retry or substitute image.")
    elif "result" in report:
        result = report["result"]
        frame = result["shots"][0]["keyframes"][0]
        image = Path(frame["sketch_path"])
        with Image.open(image) as picture:
            picture.load()
            report["output"] = {"path": str(image), "size": list(picture.size), "mode": picture.mode, "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
            picture.save(root / "actual-output.png")
        checks = {"one_real_image_job": report["image_jobs"] == 1, "bound_start_frame": frame["offset_ms"] == 0 and image.is_file(), "provider_output_not_internal_guide": bool(uploads) and all(image.read_bytes() != Path(upload).read_bytes() for upload in uploads), "not_approved_automatically": not result["approved"], "body_preserved": before["screenplay"] == ops.read_screenplay(fixture["screenplay_id"]), "graph_preserved": before["graph"] == ops.graph_snapshot(), "assets_preserved": before["assets"] == {c:ops.list_assets(c) for c in before["assets"]}, "static_screenplay_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"]}
        report.update(checks=checks, status="passed" if all(checks.values()) else "not_passed")
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "directory": str(root)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
