"""Local HTTP proxy that injects image attachments into chat completions.

Used to enable multimodal inputs for CLI-based agents (opencode, zeroclaw)
that cannot natively pass images. The agent's CLI is configured to use this
proxy as its OpenAI-compatible API base; the proxy intercepts each
``/chat/completions`` request, injects ``image_url`` content blocks into the
designated user message, and forwards to the real upstream API.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from alphadiana.utils.attachments import iter_binary_attachments, build_openai_multimodal_user_content

logger = logging.getLogger(__name__)

_INJECT_FLAG_KEY = "_alphadiana_image_injected"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _inject_into_messages(
    messages: list[dict[str, Any]],
    attachments: dict[str, Any],
    target_text: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Inject image attachments into the first matching user message.

    Returns (new_messages, did_inject).
    """
    target_norm = (target_text or "").strip()
    new_messages: list[dict[str, Any]] = []
    injected = False

    for msg in messages:
        if injected or msg.get("role") != "user":
            new_messages.append(msg)
            continue

        content = msg.get("content")
        if isinstance(content, list):
            text_parts: list[str] = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            text = "\n".join(text_parts)
        else:
            text = str(content or "")

        if target_norm and target_norm not in text:
            new_messages.append(msg)
            continue

        new_content = build_openai_multimodal_user_content(text, attachments)
        new_msg = dict(msg)
        new_msg["content"] = new_content
        new_msg[_INJECT_FLAG_KEY] = True
        new_messages.append(new_msg)
        injected = True

    return new_messages, injected


class _ProxyState:
    """Shared state for the proxy server (passed by closure to handler)."""

    def __init__(
        self,
        upstream_base: str,
        upstream_api_key: str,
        attachments: dict[str, Any],
        target_text: str,
        upstream_model: str | None = None,
    ) -> None:
        self.upstream_base = upstream_base.rstrip("/")
        self.upstream_api_key = upstream_api_key
        self.attachments = attachments
        self.target_text = target_text
        self.upstream_model = upstream_model
        self.injection_count = 0
        self.request_count = 0


def _build_handler(state: _ProxyState) -> type[BaseHTTPRequestHandler]:

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("[vision-proxy] " + format, *args)

        def _forward(self, body: bytes) -> None:
            forward_path = self.path
            if forward_path.startswith("/v1") and state.upstream_base.endswith("/v1"):
                forward_path = forward_path[len("/v1"):] or "/"
            url = f"{state.upstream_base}{forward_path}"
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in {"host", "content-length", "authorization"}
            }
            if state.upstream_api_key:
                headers["Authorization"] = f"Bearer {state.upstream_api_key}"
            req = Request(url, data=body, headers=headers, method=self.command)
            try:
                with urlopen(req, timeout=600) as resp:
                    payload = resp.read()
                    self.send_response(resp.status)
                    for hk, hv in resp.headers.items():
                        if hk.lower() in {"transfer-encoding", "connection", "content-length"}:
                            continue
                        self.send_header(hk, hv)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
            except Exception as exc:
                err = json.dumps({"error": {"message": str(exc), "type": "proxy_error"}})
                err_bytes = err.encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_bytes)))
                self.end_headers()
                self.wfile.write(err_bytes)

        def do_GET(self) -> None:  # noqa: N802
            self._forward(b"")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            state.request_count += 1

            is_chat = self.path.endswith("/chat/completions")
            has_images = any(True for _ in iter_binary_attachments(state.attachments))
            should_modify = is_chat and (has_images or state.upstream_model)

            if should_modify:
                try:
                    payload = json.loads(body.decode("utf-8"))
                    if state.upstream_model:
                        payload["model"] = state.upstream_model
                    messages = payload.get("messages")
                    if has_images and isinstance(messages, list):
                        new_messages, injected = _inject_into_messages(
                            messages, state.attachments, state.target_text
                        )
                        if injected:
                            payload["messages"] = new_messages
                            state.injection_count += 1
                            logger.info(
                                "[vision-proxy] injected %d images into request #%d",
                                sum(1 for _ in iter_binary_attachments(state.attachments)),
                                state.request_count,
                            )
                    body = json.dumps(payload).encode("utf-8")
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.warning("[vision-proxy] failed to parse request body: %s", exc)

            self._forward(body)

    return Handler


class VisionProxy:
    """Context manager that runs a local HTTP proxy injecting image attachments.

    Usage::

        with VisionProxy(upstream_base, upstream_api_key, attachments, target_text) as proxy:
            # configure CLI agent to use proxy.url as its api_base
            ...

    The proxy listens on a random local port and forwards all requests to the
    real upstream API, injecting image_url content blocks into the first user
    message that contains ``target_text``.
    """

    def __init__(
        self,
        upstream_base: str,
        upstream_api_key: str,
        attachments: dict[str, Any],
        target_text: str,
        port: int | None = None,
        upstream_model: str | None = None,
    ) -> None:
        self.upstream_base = upstream_base
        self.upstream_api_key = upstream_api_key
        self.attachments = attachments
        self.target_text = target_text
        self.upstream_model = upstream_model
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._state: _ProxyState | None = None

    def __enter__(self) -> "VisionProxy":
        self._state = _ProxyState(
            self.upstream_base,
            self.upstream_api_key,
            self.attachments,
            self.target_text,
            self.upstream_model,
        )
        port = self._port or _find_free_port()
        handler_cls = _build_handler(self._state)
        self._server = HTTPServer(("127.0.0.1", port), handler_cls)
        self._port = port
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"vision-proxy-{port}",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[vision-proxy] listening on %s, upstream=%s",
            self.url, self.upstream_base,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._state is not None:
            logger.info(
                "[vision-proxy] shutdown: %d requests, %d injections",
                self._state.request_count, self._state.injection_count,
            )

    @property
    def url(self) -> str:
        if self._port is None:
            raise RuntimeError("VisionProxy not started")
        return f"http://127.0.0.1:{self._port}/v1"

    @property
    def injection_count(self) -> int:
        return self._state.injection_count if self._state else 0
