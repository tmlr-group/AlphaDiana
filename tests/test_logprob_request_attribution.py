"""Capture request boundaries without changing model or client payloads."""

import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from alphadiana.analysis.io.logprob_artifacts import raw_record_to_int16_record
from alphadiana.harness.proxies.logprob_proxy import (
    LogprobCaptureProxy,
    follows_tool_result,
)

TOKEN = {
    "token": "x",
    "logprob": -0.1,
    "top_logprobs": [{"token": "x", "logprob": -0.1}],
}


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert "_alphadiana_capture" not in payload
        chunk = {
            "id": "fixture",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "x"},
                    "logprobs": {"content": [TOKEN]},
                    "finish_reason": None,
                }
            ],
        }
        if payload.get("stream"):
            body = ("data: " + json.dumps(chunk) + "\n\n") * 2 + "data: [DONE]\n\n"
            content_type = "text/event-stream"
        else:
            chunk["choices"][0]["logprobs"]["content"] = [TOKEN, TOKEN]
            chunk["choices"][0]["message"] = {"role": "assistant", "content": "xx"}
            body = json.dumps(chunk)
            content_type = "application/json"
        body = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.mark.parametrize("aggregate", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_stream_and_json_calls_keep_distinct_attribution(aggregate, reverse):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = LogprobCaptureProxy(
        upstream=f"http://127.0.0.1:{upstream.server_port}",
        top_logprobs=1,
        upstream_stream=aggregate,
        capture_request_summary=True,
    )
    proxy.start()
    try:
        with httpx.Client(trust_env=False) as client:
            sequence = [(True, "tool"), (False, "user")]
            for stream, role in list(reversed(sequence)) if reverse else sequence:
                response = client.post(
                    proxy.local_url + "/v1/chat/completions",
                    json={
                        "model": "fixture",
                        "stream": stream,
                        "messages": [
                            {"role": "user", "content": "question"},
                            {"role": role, "content": "feedback"},
                        ],
                    },
                )
                response.raise_for_status()
                assert "_alphadiana_capture" not in response.text
        records = proxy.drain_records()
        assert len(records) == 4
        assert [r["call_index"] for r in records] == [0, 0, 1, 1]
        calls = {}
        for record in records:
            calls.setdefault(record["call_id"], []).append(record)
            compact = raw_record_to_int16_record(record)
            assert compact["call_id"] == record["call_id"]
            assert compact["call_token_index"] == record["call_token_index"]
        assert len(calls) == 2
        assert {r["call_id"] for r in proxy.response_summaries()} == set(calls)
        assert {r["call_id"] for r in proxy.request_summaries()} == set(calls)
        assert {group[0]["follows_tool_result"] for group in calls.values()} == {
            True,
            False,
        }
        assert all(
            [r["call_token_index"] for r in group] == [0, 1] for group in calls.values()
        )
        assert proxy.drain_records() == []
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        thread.join()


def test_tool_feedback_detection_does_not_search_past_intervening_user():
    assert follows_tool_result([{"role": "tool", "content": "done"}])
    assert follows_tool_result([{"role": "user", "content": [{"type": "tool_result"}]}])
    assert not follows_tool_result(
        [{"role": "tool"}, {"role": "user", "content": "next"}]
    )
    assert not follows_tool_result(None)


@pytest.mark.parametrize("drop_store", [False, True])
@pytest.mark.parametrize("stream", [False, True])
def test_explicit_field_drop_preserves_native_request(drop_store, stream):
    observed = []

    class StrictProvider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(payload)
            rejected = "store" in payload
            body = json.dumps({"error": "property store should not exist"} if rejected
                              else {"choices": [{"message": {"content": "ready"}}]}).encode()
            self.send_response(400 if rejected else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), StrictProvider)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = None
    payload = {"model": "native", "store": False, "stream": stream,
               "temperature": None, "max_completion_tokens": 8192,
               "messages": [{"role": "user", "content": "instruction"},
                            {"role": "tool", "tool_call_id": "call-1", "content": "feedback"}],
               "tools": [{"type": "function", "function": {"name": "terminal"}}]}
    try:
        options = {"drop_request_fields": ("store",)} if drop_store else {}
        proxy = LogprobCaptureProxy(
            f"http://127.0.0.1:{upstream.server_port}", 1,
            inject_logprobs=False, normalize_system_messages=False,
            capture_request_summary=True, **options,
        )
        proxy.start()
        with httpx.Client(trust_env=False) as client:
            response = client.post(proxy.local_url + "/v1/chat/completions", json=payload)
        assert response.status_code == (200 if drop_store else 400)
        expected = {k: v for k, v in payload.items() if not (drop_store and k == "store")}
        assert observed == [expected]
        assert payload["store"] is False
        summary = proxy.request_summaries()[0]
        assert summary.get("dropped_request_fields", []) == (["store"] if drop_store else [])
    finally:
        if proxy is not None:
            proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        thread.join()


@pytest.mark.parametrize("normalize", [False, True])
def test_native_empty_tool_content_is_null_only_when_declared(normalize):
    observed = []

    class StrictProvider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(payload)
            rejected = payload["messages"][1]["content"] == ""
            body = json.dumps({"error": "invalid messages.1.content"} if rejected else {"choices": [{"message": {"content": "ready"}}]}).encode()
            self.send_response(400 if rejected else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # Actual assistant/tool messages captured from pinned OpenCode 1.3.2
    # using a local provider fixture; no benchmark solver was called.
    turn = json.loads((Path(__file__).parent / "fixtures/opencode-1.3.2-tool-turn.json").read_text())
    untouched = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": None, "tool_calls": turn[0]["tool_calls"]},
        {"role": "assistant", "content": "explanation", "tool_calls": turn[0]["tool_calls"]},
        {"role": "assistant", "content": [], "tool_calls": turn[0]["tool_calls"]},
    ]
    payload = {"model": "fixture", "messages": [{"role": "user", "content": "Run"}, *turn, *untouched]}
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), StrictProvider)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = None
    try:
        proxy = LogprobCaptureProxy(f"http://127.0.0.1:{upstream.server_port}", 1, inject_logprobs=False, normalize_system_messages=False, normalize_empty_tool_content=normalize, capture_request_summary=True)
        proxy.start()
        with httpx.Client(trust_env=False) as client:
            response = client.post(proxy.local_url + "/v1/chat/completions", json=payload)
        assert response.status_code == (200 if normalize else 400)
        expected = json.loads(json.dumps(payload))
        if normalize:
            expected["messages"][1]["content"] = None
        assert observed == [expected]
        assert payload["messages"][1]["content"] == ""
        assert proxy.request_summaries()[0].get("normalized_empty_tool_content_indices", []) == ([1] if normalize else [])
    finally:
        if proxy is not None:
            proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        thread.join()
