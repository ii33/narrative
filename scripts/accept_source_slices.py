"""One bounded real-model slice request against a new, isolated project."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from narrative import tool_api
from narrative.operations import Operations
from narrative.providers import preset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ["NARRATIVE_PROVIDER_DB"] = str(out / "providers.sqlite3")
    project = out / "slices.nproj"
    ops = Operations.create_graph_project(project)
    text = "第一节 来客\n林舟推开书店的门，向店主问好。\n\n第二节 归途\n傍晚，林舟带着一本旧书回家。"
    source_file = out / "input.txt"
    source_file.write_text(text, encoding="utf-8")
    source_id = ops.import_source_file(source_file, title="最小切片验收")
    source = tool_api.call("read_source", {"source_id": source_id}, project=project)
    config = preset("ark")
    prompt = (
        "分析下列原文，按两节的语义边界批量创建两个连续切片，完整覆盖原文。"
        "只输出JSON：{\"tool\":\"create_source_slices\",\"args\":{"
        "\"source_id\":\"...\",\"slices\":[{\"title\":\"...\",\"start\":0,\"end\":1}]}}。"
        "坐标为Python Unicode字符索引，end不包含。不要输出代码围栏。\n"
        + json.dumps(source, ensure_ascii=False)
        + "\n工具schema：" + json.dumps(tool_api.TOOLS["create_source_slices"].schema(), ensure_ascii=False)
    )
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    report = {
        "prompt_version": "source-slices-acceptance/1",
        "provider": "ark", "model": config["model"],
        "config": {"base_url": config["base_url"], "max_tokens": 1500,
                   "temperature": 0, "thinking": "disabled", "timeout_seconds": 45},
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "request_count": 0, "usage": None, "finish_reason": None,
        "candidate_count": 0, "review_actions": [],
        "precheck": [], "status": "incomplete", "raw_model_output_pass": False,
    }
    conn = None
    try:
        key = os.environ.get(config["key_env"], "")
        if not key:
            raise ValueError("Missing configured model credential")
        endpoint = urlsplit(config["base_url"])
        conn = http.client.HTTPSConnection(endpoint.hostname, endpoint.port, timeout=45)
        body = {"model": config["model"], "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500, "temperature": 0, "thinking": {"type": "disabled"}}
        report["request_count"] = 1
        conn.request("POST", endpoint.path.rstrip("/") + "/chat/completions",
                     json.dumps(body).encode(),
                     {"Content-Type": "application/json", "Authorization": "Bearer " + key})
        response = conn.getresponse()
        raw = response.read()
        (out / "response.json").write_bytes(raw)
        report["http_status"] = response.status
        payload = json.loads(raw)
        report["usage"] = payload.get("usage")
        if response.status != 200:
            raise ValueError("Model HTTP request failed")
        choice = payload["choices"][0]
        report["finish_reason"] = choice.get("finish_reason")
        if report["finish_reason"] != "stop":
            raise ValueError("Model response did not finish normally")
        content = choice["message"]["content"]
        (out / "raw.txt").write_text(content, encoding="utf-8")
        call = json.loads(content)
        if call.get("tool") != "create_source_slices" or call["args"]["source_id"] != source_id:
            raise ValueError("Model did not produce the requested source-slice tool call")
        result = tool_api.call(call["tool"], call["args"], project=project)
        slices = result["slices"]
        if len(slices) != 2 or slices[0]["start"] != 0 or slices[-1]["end"] != len(text):
            raise ValueError("Model slice count or coverage failed")
        if slices[0]["end"] != slices[1]["start"]:
            raise ValueError("Model slice coverage is discontinuous")
        previews = [tool_api.call("read_source_slice", {"source_id": source_id, "slice_id": s["id"]},
                                  project=project) for s in slices]
        (out / "business-result.json").write_text(json.dumps({"result": result, "previews": previews},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
        report.update(status="passed", raw_model_output_pass=True,
                      precheck=["batch accepted without repair", "two contiguous ranges cover the source"],
                      slice_count=len(slices),
                      review_actions=["source slices are navigation metadata; no graph candidate produced"])
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        # Avoid recording exception payloads which might contain request headers.
        report["precheck"].append("Real acceptance incomplete or failed; no retry performed")
    finally:
        if conn is not None:
            conn.close()
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
