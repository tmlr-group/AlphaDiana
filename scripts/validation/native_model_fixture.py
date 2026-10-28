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
def model_server(
    port: int = 0,
    *,
    api_key: str = "keyless",
    final_finish_reason: str = "stop",
    reasoning_text: str | None = None,
    native_capture: bool = False,
    policy_publication: str = "",
    weights_digest: str = "a" * 64,
    model_revision: str = "fixture-revision-1",
    tokenizer_digest: str = "a" * 64,
    chat_template_digest: str = "a" * 64,
    shell_command: str | None = None,
    prompt_tokens: int = 10,
):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.headers.get("Authorization") != "Bearer " + api_key:
                self.send_error(401, "Invalid fixture credential")
                return
            requests.append(request)
            messages = request["messages"]
            tool_results = [m for m in messages if m.get("role") == "tool"]
            message = {"role": "assistant"}
            if not request.get("tools") and not tool_results:
                message["content"] = (
                    "The user requested a shell task. Continue from the recent tool result."
                )
                finish = "stop"
            elif tool_results:
                content = str(tool_results[-1].get("content", ""))
                message["content"] = "1" if "CANDIDATE_GOOD" in content else "0"
                if reasoning_text is not None:
                    message["reasoning_content"] = reasoning_text
                finish = final_finish_reason
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
                if shell_command is not None:
                    arguments[field] = shell_command
                if any(
                    "CANDIDATE_GOOD" in str(m.get("content", ""))
                    for m in messages
                    if m.get("role") == "system"
                ):
                    arguments[field] = (
                        "printf CANDIDATE_GOOD > marker.txt; " + arguments[field]
                    )
                message["content"] = None
                message["tool_calls"] = [
                    {
                        "id": "fixture_call",
                        "type": "function",
                        "function": {
                            "name": selected["name"],
                            "arguments": json.dumps(arguments),
                        },
                    }
                ]
                finish = "tool_calls"
            native_prompt = list(range(100, 100 + len(messages)))
            native_tokens = list(range(200, 204))
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 4,
                "total_tokens": prompt_tokens + 4,
            }
            if native_capture and request.get("return_token_ids"):
                # These IDs are emitted by the fixture server itself, before
                # Pi sees the text projection; tests can therefore verify the
                # no-retokenization boundary without loading a tokenizer.
                native_tokens = list(
                    range(200, 200 + (2 if finish == "tool_calls" else 3))
                )
                native_prompt = list(range(100, 100 + len(messages) + 1))
                usage = {
                    "prompt_tokens": len(native_prompt),
                    "completion_tokens": len(native_tokens),
                    "total_tokens": len(native_prompt) + len(native_tokens),
                }
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
                            "choices": [
                                {"index": 0, "delta": change, "finish_reason": reason}
                            ],
                            "usage": usage if reason else None,
                        }
                    )
                body = (
                    "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n"
                )
                content_type = "text/event-stream"
            else:
                response = {
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": request["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": finish,
                            **(
                                {
                                    "token_ids": native_tokens,
                                    "native_log_probs": [-0.1] * len(native_tokens),
                                }
                                if native_capture and request.get("return_token_ids")
                                else {}
                            ),
                        }
                    ],
                    **(
                        {
                            "prompt_token_ids": native_prompt,
                            "policy_publication": policy_publication,
                            "policy_identity": {
                                "policy_publication": policy_publication,
                                "weights_digest": weights_digest,
                            },
                            "model_identity": {
                                "model_name": request["model"],
                                "model_revision": model_revision,
                                "tokenizer_digest": tokenizer_digest,
                                "chat_template_digest": chat_template_digest,
                            },
                        }
                        if native_capture and request.get("return_token_ids")
                        else {}
                    ),
                    "usage": usage,
                }
                body = json.dumps(response)
                content_type = "application/json"
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://host.containers.internal:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    with model_server(parser.parse_args().port) as (endpoint, _requests):
        print(endpoint, flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
