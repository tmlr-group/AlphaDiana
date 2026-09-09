"""Deterministic OpenAI-compatible TEST server, never a model benchmark.

The real harness must execute a shell tool. Its output (candidate-created marker,
plus a task-specific nonce) determines the next assistant response.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def model_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            messages = request["messages"]
            tool_results = [m for m in messages if m.get("role") == "tool"]
            message = {"role": "assistant"}
            if tool_results:
                content = str(tool_results[-1].get("content", ""))
                message["content"] = "1" if "CANDIDATE_GOOD" in content else "0"
                finish = "stop"
            else:
                tools = request.get("tools", [])
                names = [item["function"]["name"] for item in tools]
                selected = next(
                    (
                        item["function"]
                        for item in tools
                        if item["function"]["name"] in {"bash", "persistent_bash"}
                    ),
                    None,
                )
                if selected is None:
                    raise ValueError(f"Fixture needs a shell tool, got {names}")
                properties = selected["parameters"].get("properties", {})
                field = "command" if "command" in properties else "script"
                arguments = {
                    field: (
                        "cat marker.txt 2>/dev/null || printf BASELINE; "
                        "printf '\\n'; printf TOOL_RAN > tool-ran.txt; cat tool-ran.txt"
                    )
                }
                if any(
                    "CANDIDATE_GOOD" in str(m.get("content", ""))
                    for m in messages
                    if m.get("role") == "system"
                ):
                    arguments[field] = "printf CANDIDATE_GOOD > marker.txt; " + arguments[field]
                message["content"] = None
                message["tool_calls"] = [
                    {
                        "id": "fixture_call",
                        "type": "function",
                        "function": {"name": selected["name"], "arguments": json.dumps(arguments)},
                    }
                ]
                finish = "tool_calls"
            usage = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
            if request.get("stream"):
                chunks = []
                delta = dict(message)
                if "tool_calls" in delta:
                    delta["tool_calls"][0]["index"] = 0
                for change, reason in ((delta, None), ({}, finish)):
                    chunks.append(
                        {
                            "id": "fixture",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": request["model"],
                            "choices": [{"index": 0, "delta": change, "finish_reason": reason}],
                            "usage": usage if reason else None,
                        }
                    )
                body = (
                    "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n"
                )
                content_type = "text/event-stream"
            else:
                body = json.dumps(
                    {
                        "id": "fixture",
                        "object": "chat.completion",
                        "created": 1,
                        "model": request["model"],
                        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                        "usage": usage,
                    }
                )
                content_type = "application/json"
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://host.containers.internal:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
