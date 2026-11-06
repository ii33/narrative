"""The program reports real commits without a model accounting round-trip."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from narrative import assistant, tool_api
from narrative.conversation_runtime import close_project_runtime
from narrative.conversation_session import IncompleteConversation, open_conversation_session
from narrative.harness import HarnessRunner, HarnessSettings
from narrative.operations import Operations
from narrative.prompt_defaults import DEFAULT_PROMPTS


@pytest.mark.parametrize("answer", ["", "细微纸纤维可保留，避免喧宾夺主。"])
def test_run_turn_returns_truthful_summary_without_reasking_or_losing_useful_answer(tmp_path, monkeypatch, answer):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "summary.nproj"
    Operations.create_graph_project(entry)
    steps, asks = [], []

    def ask(instruction):
        asks.append(instruction)
        steps.append(assistant._call(entry, "save_asset", {"payload": {"category": "material", "title": "纸张纹理"}}))
        identifier = steps[-1]["result"]["id"]
        steps.append(assistant._call(entry, "save_asset", {"payload": {"id": identifier, "category": "material", "title": "纸张纹理修订"}}))
        return answer

    result = assistant.run_turn(ask, "创建纹理后调整名称", project=entry, steps=steps, task_id="summary-task")
    assert len(asks) == 1  # no host-created final-summary call, even for empty text
    assert result["answer"] == answer
    summary = result["commit_summary"]
    assert summary["source"] == "program" and summary["task_id"] == "summary-task"
    assert summary["committed_operations"] == 2
    assert summary["totals"]["created"] == summary["totals"]["updated"] == 1
    assert result["commit_summary_text"] == summary["text"]


def test_nonrecoverable_failure_keeps_committed_summary_without_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "partial.nproj"
    Operations.create_graph_project(entry)
    steps, asks = [], []

    def ask(instruction):
        asks.append(instruction)
        steps.append(assistant._call(entry, "save_asset", {"payload": {"category": "material", "title": "已提交纹理"}}))
        raise IncompleteConversation({"finish_reason": "error", "service_error": "HTTP 503"}, tmp_path / "diagnostic.json")

    with pytest.raises(IncompleteConversation) as failure:
        assistant.run_turn(ask, "创建纹理", project=entry, steps=steps)
    assert len(asks) == 1
    assert failure.value.metadata["commit_summary"]["status"] == "failed"
    assert failure.value.metadata["commit_summary"]["totals"]["created"] == 1


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("explicit_task", [True, False])
@pytest.mark.parametrize("presentation", ["answer", "thought"])
def test_headless_real_sdk_keeps_multi_step_work_and_raw_reply_separate_from_summary(tmp_path, monkeypatch, failed, explicit_task, presentation):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "headless-summary.nproj"
    ops = Operations.create_graph_project(entry)
    requests = []
    semantic_reply = "纸张纤维可保留细节。"

    class LocalModel(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            number = len(requests)
            if number == 3 and failed:
                body = b'{"error":{"message":"isolated local failure"}}'
                self.send_response(503)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if number <= 2:
                payload = {"category": "material", "title": "纸张纹理"}
                if number == 2:
                    payload.update(id="material-1", title="纸张纹理修订")
                delta = {"tool_calls": [{"index": 0, "id": f"write-{number}", "function": {"name": "save_asset", "arguments": json.dumps({"payload": payload}, ensure_ascii=False)}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"content": semantic_reply}, "stop"
            frames = [{"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]},
                      {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}]
            body = ("".join("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n" for frame in frames) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalModel)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = HarnessSettings(provider="custom", model="local-summary", max_tokens=512,
        base_url=f"http://127.0.0.1:{server.server_port}/v1")
    wrapper = None
    try:
        wrapper, session = open_conversation_session(ops, settings, "local-only", HarnessRunner,
            task_tools=assistant.tool_catalog(["save_asset"]), task_id="headless-summary" if explicit_task else None,
            tool_handler=lambda name, args: tool_api.call(name, args, project=entry), output_presentation=presentation)
        if failed:
            with pytest.raises(IncompleteConversation) as error:
                wrapper.agent_turn("Create then update the material.", session)
            metadata = error.value.metadata
        else:
            reply = wrapper.agent_turn("Create then update the material; explain its visual value.", session)
            metadata = reply.metadata
            assert reply.raw == semantic_reply
            assert reply.commit_summary == metadata["commit_summary"]
            assert reply.commit_summary_text == metadata["commit_summary_text"]
        assert len(requests) == 3  # two independent writes, then natural SDK end
        assert metadata["commit_summary"]["committed_operations"] == 2
        assert metadata["commit_summary"]["status"] == ("failed" if failed else "completed")
        assert metadata["commit_summary"]["totals"]["created"] == metadata["commit_summary"]["totals"]["updated"] == 1
        assert ops.get_asset("material-1")["title"] == "纸张纹理修订"
        saved = json.loads(next((wrapper.directory / "turns").glob("*/metadata.json")).read_text(encoding="utf-8"))
        assert saved["commit_summary"] == metadata["commit_summary"]
        from narrative.output_presentation import OUTPUT_CONTRACT_VERSION
        assert saved["output_contract_version"] == OUTPUT_CONTRACT_VERSION
        assert saved["output_presentation"] == presentation
        prompt = next((wrapper.directory / "turns").glob("*/prompt.txt")).read_text(encoding="utf-8")
        assert "不要再复述" in prompt
        if not failed:
            assert metadata["model_messages"][-1]["display_kind"] == "answer"
            assert all(row["display_kind"] == "thought" for row in metadata["model_messages"][:-1])
        raw = next((wrapper.directory / "turns").glob("*/raw.txt")).read_text(encoding="utf-8")
        if not failed:
            assert raw == semantic_reply
            following = wrapper.agent_turn("Only explain the texture, no more writes.", session)
            if explicit_task:
                assert following.commit_summary["task_id"] == reply.commit_summary["task_id"]
                assert following.commit_summary["committed_operations"] == 2
                durable = ops.read_task_write_summary("headless-summary", wrapper.chat_id)
                assert durable["committed_operations"] == 2
            else:
                assert following.commit_summary["task_id"] != reply.commit_summary["task_id"]
                assert following.commit_summary["committed_operations"] == 0
                assert following.commit_summary["no_changes"] is True
    finally:
        if wrapper is not None:
            wrapper.close()
        close_project_runtime(ops)
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_default_guidance_assigns_accounting_to_program_without_suppressing_semantics():
    system = DEFAULT_PROMPTS["system"]["text"]
    assert "入库账目由程序" in system and "模型无需复述" in system
    assert "完成全部必要业务步骤后自然结束" in system
    assert "创作内容、重要语义判断、存疑理由与必要解释" in system
    assert "只回复已完成" not in system
