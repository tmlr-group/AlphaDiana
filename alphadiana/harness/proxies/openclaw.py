"""OpenClaw gateway wire compatibility: rename reasoning and strip captured SSE logprobs."""

from __future__ import annotations

import http.server
import json
import logging
import socketserver
import threading
from typing import Any

from alphadiana.harness.proxies.logprob_proxy import (
    resolve_logprob_proxy_advertise_host,
)

_logger = logging.getLogger(__name__)


def _rename_reasoning_field_in_delta(choice: dict) -> None:
    """Rename ``delta.reasoning`` -> ``delta.reasoning_content`` in-place.

    vLLM's qwen3 reasoning parser names the thinking-mode field ``reasoning``;
    the OpenClaw gateway parser expects ``reasoning_content``. Normalising here
    lets the gateway forward thinking chunks to AlphaDiana without dropping
    them (root cause of finding #11).
    """
    delta = choice.get("delta")
    if (
        isinstance(delta, dict)
        and "reasoning" in delta
        and "reasoning_content" not in delta
    ):
        delta["reasoning_content"] = delta.pop("reasoning")
    message = choice.get("message")
    if (
        isinstance(message, dict)
        and "reasoning" in message
        and "reasoning_content" not in message
    ):
        message["reasoning_content"] = message.pop("reasoning")


def _rename_reasoning_field_in_message(payload: dict) -> None:
    """Walk an OpenAI-style response payload and apply the rename to every choice."""
    if not isinstance(payload, dict):
        return
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, dict):
                _rename_reasoning_field_in_delta(choice)


class _OpenClawLogprobProxyHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that forwards to vLLM and captures per-token logprob dicts."""

    server: "_OpenClawLogprobServer"

    def log_message(self, fmt: str, *args: Any) -> None:  # silence access log
        pass

    def do_POST(self) -> None:  # noqa: N802
        import httpx

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        # Inject logprob request parameters
        payload["logprobs"] = True
        payload["top_logprobs"] = self.server.top_logprobs
        # vLLM with --reasoning-parser qwen3 emits thinking content under
        # delta.reasoning / message.reasoning, but the OpenClaw gateway expects
        # delta.reasoning_content / message.reasoning_content. We rewrite the
        # SSE chunks below in _strip_and_capture_event so the gateway sees
        # reasoning_content, which lets us honour enable_thinking=true without
        # the gateway dropping reasoning chunks on the floor (#11 root cause).

        upstream_url = self.server.upstream.rstrip("/") + self.path
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
        }
        is_stream = payload.get("stream", False)

        try:
            if is_stream:
                self._handle_streaming(upstream_url, headers, payload, httpx)
            else:
                self._handle_non_streaming(upstream_url, headers, payload, httpx)
        except Exception as exc:
            _logger.debug("OpenClaw logprob proxy error: %s", exc)
            self.send_error(502, f"Proxy error: {exc}")

    def _handle_non_streaming(
        self, url: str, headers: dict, payload: dict, httpx: Any
    ) -> None:
        resp = self.server.client.post(url, headers=headers, json=payload)
        resp_bytes = resp.content

        # Capture logprobs and rename reasoning->reasoning_content for gateway.
        try:
            resp_json = resp.json()
            _rename_reasoning_field_in_message(resp_json)
            resp_bytes = json.dumps(resp_json, separators=(",", ":")).encode()
            with self.server.lock:
                self.server.raw_responses.append(resp_json)
        except Exception:
            pass

        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() not in (
                "transfer-encoding",
                "content-encoding",
                "content-length",
            ):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _strip_and_capture_event(self, event_bytes: bytes) -> bytes:
        """Capture logprobs from an SSE event and return it with logprobs stripped.

        Also renames vLLM's ``reasoning`` field (qwen3 reasoning parser) to the
        ``reasoning_content`` name the OpenClaw gateway expects so thinking
        chunks aren't dropped (finding #11).

        The gateway's SSE parser fails on large data: lines produced when
        top_logprobs=20 injects thousands of bytes per token. Strip logprobs
        from the forwarded event so the gateway sees compact normal-sized chunks,
        while still capturing the logprob data in streaming_logprobs.
        """
        out_lines: list[bytes] = []
        for line_bytes in event_bytes.split(b"\n"):
            line = line_bytes.decode("utf-8", errors="replace")
            if line.startswith("data:") and line.strip() != "data: [DONE]":
                data_str = line[5:].strip()
                try:
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if choices:
                        logprob_data = choices[0].get("logprobs")
                        if isinstance(logprob_data, dict):
                            content = logprob_data.get("content") or []
                            if content:
                                with self.server.lock:
                                    self.server.streaming_logprobs.extend(content)
                        choices[0]["logprobs"] = None
                        _rename_reasoning_field_in_delta(choices[0])
                    out_lines.append(
                        b"data: " + json.dumps(chunk, separators=(",", ":")).encode()
                    )
                except Exception:
                    out_lines.append(line_bytes)
            else:
                out_lines.append(line_bytes)
        return b"\n".join(out_lines)

    def _handle_streaming(
        self, url: str, headers: dict, payload: dict, httpx: Any
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        with self.server.client.stream(
            "POST", url, headers=headers, json=payload
        ) as resp:
            # Buffer raw bytes and emit complete SSE events with logprobs stripped.
            # Stripping is required: top_logprobs=20 makes each data: line thousands
            # of bytes long; the OpenClaw gateway SSE parser fails on such large lines.
            buf = b""
            for raw_chunk in resp.iter_raw():
                buf += raw_chunk
                while b"\n\n" in buf:
                    event_bytes, buf = buf.split(b"\n\n", 1)
                    stripped = self._strip_and_capture_event(event_bytes)
                    try:
                        self.wfile.write(stripped + b"\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
            if buf:
                try:
                    self.wfile.write(buf)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass


class _OpenClawLogprobServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, upstream: str, top_logprobs: int) -> None:
        super().__init__(("0.0.0.0", 0), _OpenClawLogprobProxyHandler)
        self.upstream = upstream
        self.top_logprobs = top_logprobs
        self.raw_responses: list[dict] = []
        self.streaming_logprobs: list[dict] = []
        self.lock = threading.Lock()
        import httpx as _httpx

        self.client = _httpx.Client(timeout=120.0)


class OpenClawLogprobProxy:
    """Host-side MITM proxy accessible from Docker containers via host bridge IP.

    ``start()`` is idempotent — repeat calls within an agent's lifetime are
    no-ops once the server is up. This avoids the ``AssertionError: self._server
    is not None`` race under ``max_concurrent>=2`` where a second concurrent
    task would otherwise tear down a still-in-use proxy mid-stream (finding #8).
    """

    def __init__(self, upstream: str, top_logprobs: int) -> None:
        self._upstream = upstream
        self._top_logprobs = top_logprobs
        self._server: _OpenClawLogprobServer | None = None
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._server is not None:
                return
            self._server = _OpenClawLogprobServer(self._upstream, self._top_logprobs)
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            server = self._server
            if server is None:
                return
            self._server = None
            self._thread = None
        try:
            server.shutdown()
            server.client.close()
        except Exception:
            pass

    @property
    def local_port(self) -> int:
        with self._lifecycle_lock:
            if self._server is None:
                raise RuntimeError(
                    "OpenClaw logprob proxy is not running; call start() first"
                )
            return self._server.server_address[1]

    def proxy_url_for_docker(self, docker_host_ip: str = "") -> str:
        """Return the URL that Docker containers can use to reach this proxy."""
        host = docker_host_ip or resolve_logprob_proxy_advertise_host(self._upstream)
        return f"http://{host}:{self.local_port}"

    def captured_records(self) -> list[dict]:
        """Return all captured per-token logprob dicts (SSE preferred over buffered)."""
        if self._server is None:
            return []
        from alphadiana.harness.proxies.logprob_capture import (
            extract_openai_logprob_records,
            raw_token_logprob_dict_to_record,
        )

        records: list[dict] = []
        token_index = 0
        with self._server.lock:
            sse_tokens = list(self._server.streaming_logprobs)
            payloads = list(self._server.raw_responses)
        if sse_tokens:
            for item in sse_tokens:
                records.append(raw_token_logprob_dict_to_record(item, token_index))
                token_index += 1
        else:
            for payload in payloads:
                new_records, token_index = extract_openai_logprob_records(
                    payload, start_index=token_index
                )
                records.extend(new_records)
        return records
