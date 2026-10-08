"""Constructed CLI fixture exercises the real proxy against an authenticated provider."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.opencode.agent import OpenCodeAgent


@pytest.mark.parametrize("stream", [False, True])
def test_cli_capture_authenticates_to_provider(monkeypatch, stream):
    observed = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(self.headers.get("Authorization"))
            authorized = observed[-1] == "Bearer fixture-provider-key"
            token = {"token": "42", "logprob": -0.1, "top_logprobs": []}
            response = {"choices": [{"index": 0, "message": {"content": "42"},
                         "delta": {"content": "42"}, "logprobs": {"content": [token]}}]}
            body = json.dumps(response)
            if payload.get("stream"):
                body = "data: " + body + "\n\ndata: [DONE]\n\n"
            body = body.encode()
            self.send_response(200 if authorized else 401)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=provider.serve_forever, daemon=True)
    worker.start()
    agent = OpenCodeAgent()
    agent.setup({"controller_mode": "podman", "model": "custom/fixture",
                 "api_base": f"http://127.0.0.1:{provider.server_port}/v1",
                 "api_key": "fixture-provider-key", "capture_logprobs": True,
                 "top_logprobs": 1, "streaming": stream})

    def cli_request(command, workdir, env):
        with httpx.Client(trust_env=False) as client:
            reply = client.post(env["OPENAI_BASE_URL"] + "/chat/completions",
                                headers={"Authorization": "Bearer " + env["OPENAI_API_KEY"]},
                                json={"model": "fixture", "stream": stream,
                                      "messages": [{"role": "user", "content": "question"}]})
        # Return normally so the agent closes its real proxy even on a regression.
        return json.dumps({"type": "text", "part": {"text": "42"}}), "", 0

    monkeypatch.setattr(agent, "_run_in_podman", cli_request)
    try:
        result = agent.solve(BenchmarkTask("fixture", "question", "42"))
        assert observed == ["Bearer fixture-provider-key"]
        assert result.metadata["logprobs_capture_status"] == "captured"
    finally:
        agent.teardown()
        provider.shutdown()
        provider.server_close()
        worker.join()
