"""Provider requests expose loaded schemas without rebuilding the SDK registry."""
import http.client
import json

from test_gateway_turn_cancel import COMPLETE, _upstream

from narrative.harness import HarnessSettings
from narrative.model_gateway import ModelGateway


def test_dynamic_visibility_keeps_resident_prefix_and_same_session():
    with _upstream(COMPLETE) as (port, _, release, requests):
        release.set()
        settings = HarnessSettings(provider="custom", base_url=f"http://127.0.0.1:{port}/v1", model="offline")
        gateway = ModelGateway(settings, "offline-key")
        visible = ["project_info", "load_tools"]
        gateway.tool_allowlist_provider = lambda: tuple(visible)
        # Registration order deliberately differs from visibility/load order.
        registry = [{"type": "function", "function": {"name": name, "description": name,
                    "parameters": {"type": "object", "properties": {}}}}
                    for name in ["read_screenplay", "project_info", "apply_storyboard_changes", "load_tools"]]
        try:
            with gateway.session("same-chat"):
                for load in [False, True]:
                    if load:
                        visible.append("read_screenplay")
                    conn = http.client.HTTPConnection("127.0.0.1", gateway._server.server_port, timeout=4)
                    try:
                        conn.request("POST", "/v1/chat/completions", json.dumps({"model": "offline",
                            "messages": [{"role": "user", "content": "list scripts"}], "stream": True,
                            "tools": registry}), {"Authorization": f"Bearer {gateway.token}", "Content-Type": "application/json"})
                        response = conn.getresponse()
                        assert response.status == 200
                        response.read()
                    finally:
                        conn.close()
            names = [[tool["function"]["name"] for tool in request["tools"]] for request in requests]
            assert names == [["project_info", "load_tools"], ["project_info", "load_tools", "read_screenplay"]]
            assert requests[0]["tools"] == requests[1]["tools"][:2]
            assert len(registry) == 4
        finally:
            gateway.close()
