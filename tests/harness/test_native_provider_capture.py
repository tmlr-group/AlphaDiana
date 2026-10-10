"""Observe upstream runtimes without rewriting their messages or control loop."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from alphadiana.harness.terminal_bench2.terminus import (
    attribute_terminal_feedback,
)
from alphadiana.harness.base import AgentResponse
from alphadiana.harness.proxies.native_capture import (
    NativeProviderCapture,
    complete_provider_usage,
)
from alphadiana.harness.zeroclaw.agent import ZeroClawAgent


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("capture_logprobs", [False, True])
def test_native_capture_preserves_request_and_closes_on_failure(
    tmp_path, failure, capture_logprobs
):
    observed = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            observed.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            body = json.dumps(
                {
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 3,
                        "total_tokens": 14,
                    },
                    "choices": [
                        {
                            "message": {"content": "answer"},
                            "logprobs": {
                                "content": [
                                    {
                                        "token": "answer",
                                        "logprob": -0.1,
                                        "top_logprobs": [
                                            {"token": "answer", "logprob": -0.1}
                                        ],
                                    }
                                ]
                            } if capture_logprobs else None,
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    capture = NativeProviderCapture(
        {
            "capture_logprobs": capture_logprobs,
            "top_logprobs": 1,
            "api_base": f"http://127.0.0.1:{provider.server_port}/v1",
        },
        tmp_path,
        measure_usage=True,
    )
    payload = {
        "model": "fixture",
        "temperature": 0.2,
        "max_tokens": 128,
        "stream": False,
        "messages": [
            {"role": "user", "content": "instruction"},
            {"role": "system", "content": "preserve position"},
            {"role": "tool", "content": "terminal output"},
        ],
    }
    try:
        try:
            with capture:
                with httpx.Client(trust_env=False) as client:
                    client.post(
                        capture.api_base + "/chat/completions", json=payload
                    ).raise_for_status()
                if failure:
                    raise RuntimeError("native failure")
        except RuntimeError as exc:
            assert failure and str(exc) == "native failure"
        probability_fields = (
            {"logprobs": True, "top_logprobs": 1} if capture_logprobs else {}
        )
        assert observed == [{**payload, **probability_fields}]
        assert capture.proxy._server is None
        if capture_logprobs:
            assert len(capture.records) == 1
            assert capture.records[0]["follows_tool_result"]
            assert capture.requests[0]["call_id"] == capture.records[0]["call_id"]
        else:
            assert capture.records == []
        assert (
            capture.requests[0]["last_message_content_sha256"]
            == hashlib.sha256(b"terminal output").hexdigest()
        )
        probability_file = tmp_path / "provider_logprobs.jsonl"
        if capture_logprobs:
            assert json.loads(probability_file.read_text())["token"] == "answer"
        else:
            assert probability_file.read_text() == ""
        response = capture.apply(AgentResponse(answer="answer"))
        assert response.metadata["logprobs_capture_status"] == (
            "captured" if capture_logprobs else "not_requested"
        )
        assert "provider_requests.jsonl" in response.workspace_file_contents
        assert "provider_responses.jsonl" in response.workspace_file_contents
        assert response.token_usage == {
            "prompt_tokens": 11,
            "completion_tokens": 3,
            "total_tokens": 14,
        }
        assert response.metadata["token_usage_provenance"]["complete"]
        assert capture.responses[0]["call_id"] == capture.requests[0]["call_id"]
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join()


def test_capture_disabled_keeps_direct_native_url(tmp_path):
    with NativeProviderCapture({"api_base": "http://provider/v1"}, tmp_path) as capture:
        assert capture.api_base == "http://provider/v1" and capture.proxy is None
    assert not list(tmp_path.iterdir())
    assert (
        capture.apply(AgentResponse(answer="x")).metadata["logprobs_capture_status"]
        == "not_requested"
    )


@pytest.mark.parametrize(
    "case",
    ["command", "first", "parser", "completion", "ambiguous", "intervening", "missing"],
)
def test_terminal_feedback_requires_native_command_observation(case):
    content = "observed terminal output"
    step = {
        "step_id": 2,
        "source": "agent",
        "tool_calls": [{"function_name": "bash_command"}],
        "observation": {"results": [{"content": content}]},
    }
    if case == "parser":
        step["tool_calls"] = []
    if case == "completion":
        step["tool_calls"] = [{"function_name": "mark_task_complete"}]
    steps = [step]
    if case == "ambiguous":
        steps.append({"source": "user", "message": content})
    requests = [
        {
            "call_id": "call",
            "call_index": 0 if case == "first" else 1,
            "message_roles_before": ["assistant", "user"]
            if case != "intervening"
            else ["user", "user"],
            "last_message_content_sha256": hashlib.sha256(content.encode()).hexdigest()
            if case != "missing"
            else None,
        }
    ]
    records = [{"call_id": "call", "follows_tool_result": False}]
    attribute_terminal_feedback(records, requests, steps)
    assert records[0]["follows_tool_result"] == (case == "command")
    if case == "command":
        assert records[0]["terminal_feedback_attribution"]["step_ids"] == [2]


def test_explicit_trace_path_preserves_default_and_other_config(tmp_path):
    agent = ZeroClawAgent()
    agent.setup(
        {
            "model": "fixture",
            "api_base": "http://localhost:1/v1",
            "logs_base_dir": str(tmp_path),
        }
    )
    default = tomllib.loads(agent._build_config_toml())
    explicit = tomllib.loads(
        agent._build_config_toml(runtime_trace_path="/owned/task/state/trace.jsonl")
    )
    assert default["observability"]["runtime_trace_path"] == "state/runtime-trace.jsonl"
    assert (
        explicit["observability"]["runtime_trace_path"]
        == "/owned/task/state/trace.jsonl"
    )
    explicit["observability"]["runtime_trace_path"] = "state/runtime-trace.jsonl"
    assert explicit == default


@pytest.mark.parametrize(
    "case",
    [
        "complete",
        "missing",
        "duplicate",
        "failed",
        "absent_usage",
        "invalid_total",
        "zero",
        "boolean",
    ],
)
def test_provider_totals_require_complete_request_coverage(case):
    requests = [{"call_id": "a"}, {"call_id": "b"}]
    responses = [
        {
            "call_id": identity,
            "status_code": 200,
            "prompt_tokens": 4,
            "completion_tokens": 2,
            "total_tokens": 6,
        }
        for identity in ["b", "a"]
    ]
    if case == "missing":
        responses.pop()
    if case == "duplicate":
        responses[0]["call_id"] = "a"
    if case == "failed":
        responses[0]["status_code"] = 400
    if case == "absent_usage":
        responses[0].pop("completion_tokens")
    if case == "invalid_total":
        responses[0]["total_tokens"] = 10
    if case == "boolean":
        responses[0]["completion_tokens"] = True
    if case == "zero":
        responses[0].update(completion_tokens=0, total_tokens=4)
    usage, provenance = complete_provider_usage(requests, responses)
    if case in {"complete", "zero"}:
        assert usage == {
            "prompt_tokens": 8,
            "completion_tokens": 4 if case == "complete" else 2,
            "total_tokens": 12 if case == "complete" else 10,
        }
        assert provenance["complete"]
    else:
        assert usage == {} and not provenance["complete"]


def test_usage_capture_preserves_environment_proxy_egress(tmp_path, monkeypatch):
    observed = []
    class EgressProxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            observed.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            body = b'{"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), EgressProxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    # Windows environment names are case-insensitive; clear aliases before setting them.
    for name in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('HTTP_PROXY', f'http://127.0.0.1:{proxy.server_port}')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    try:
        with NativeProviderCapture({'api_base': 'http://native-provider.invalid/v1'}, tmp_path, measure_usage=True) as capture:
            with httpx.Client(trust_env=False) as client:
                response = client.post(capture.api_base + '/chat/completions', json={'model': 'fixture', 'messages': []})
                assert response.status_code == 200
        assert observed == ['http://native-provider.invalid/v1/chat/completions']
        assert capture.responses[0]['prompt_tokens'] == 1
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join()
