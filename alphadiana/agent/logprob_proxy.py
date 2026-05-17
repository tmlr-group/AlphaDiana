"""OpenAI-compatible proxy that injects and captures chat-completion logprobs."""

from __future__ import annotations

import http.server
import json
import logging
import os
import socketserver
import threading
from typing import Any
from urllib.parse import urlsplit

import httpx

from alphadiana.agent.logprob_capture import (
    extract_openai_logprob_records,
    raw_token_logprob_dict_to_record,
)

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def normalize_openai_proxy_upstream(api_base: str) -> str:
    """Return an upstream base URL suitable for forwarding full OpenAI paths."""
    upstream = str(api_base or "").strip().rstrip("/")
    if upstream.endswith("/v1"):
        upstream = upstream[:-3]
    return upstream.rstrip("/")


def resolve_logprob_proxy_advertise_host(
    upstream: str,
    configured_host: str = "",
) -> str:
    """Resolve the host clients should use to reach a locally bound proxy."""
    configured = str(configured_host or "").strip()
    if configured:
        return configured
    env_host = os.environ.get("ALPHADIANA_LOGPROB_PROXY_HOST", "").strip()
    if env_host:
        return env_host
    try:
        upstream_host = (urlsplit(upstream).hostname or "").strip()
    except Exception:
        upstream_host = ""
    if upstream_host and upstream_host.lower() not in _LOOPBACK_HOSTS:
        return upstream_host
    return "host.docker.internal"


def _system_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def normalize_chat_system_messages(messages: Any) -> tuple[Any, bool]:
    """Move all system messages to a single leading message for strict templates."""
    if not isinstance(messages, list):
        return messages, False

    system_parts: list[str] = []
    non_system_messages: list[Any] = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            system_parts.append(_system_content_to_text(message.get("content", "")))
        else:
            non_system_messages.append(message)

    if not system_parts:
        return messages, False

    merged_system = "\n\n".join(system_parts)
    normalized_messages = [{"role": "system", "content": merged_system}, *non_system_messages]
    return normalized_messages, normalized_messages != messages


class _LogprobProxyHandler(http.server.BaseHTTPRequestHandler):
    server: "_LogprobCaptureServer"

    def _is_authorized(self) -> bool:
        expected = self.server.proxy_api_key
        if not expected:
            return True
        auth_header = self.headers.get("Authorization", "").strip()
        api_key_header = self.headers.get("api-key", "").strip()
        if auth_header == f"Bearer {expected}" or api_key_header == expected:
            return True
        self.send_error(401, "Unauthorized")
        return False

    def _forward_headers(self, resp: httpx.Response, *, skip_length: bool = False) -> None:
        self.send_response(resp.status_code)
        for key, value in resp.headers.items():
            normalized = key.lower()
            if normalized in ("transfer-encoding", "content-encoding"):
                continue
            if skip_length and normalized == "content-length":
                continue
            self.send_header(key, value)
        self.end_headers()

    def _extract_sse_logprobs(self, line: str) -> None:
        if not line.startswith("data:") or line.strip() == "data: [DONE]":
            return
        raw_json = line[len("data:"):].strip()
        if not raw_json or raw_json == "[DONE]":
            return
        try:
            data = json.loads(raw_json)
            choices = data.get("choices", [])
            if choices:
                lp_content = (choices[0].get("logprobs") or {}).get("content") or []
                if lp_content:
                    with self.server.lock:
                        self.server.streaming_logprobs.extend(lp_content)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def do_POST(self) -> None:
        if not self._is_authorized():
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        path = self.path
        is_completions = "/chat/completions" in path

        if is_completions:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {}
            normalized_messages, normalized = normalize_chat_system_messages(payload.get("messages"))
            if normalized:
                payload["messages"] = normalized_messages
            payload["logprobs"] = True
            payload["top_logprobs"] = self.server.top_logprobs
            payload.update(self.server.request_overrides)
            body = json.dumps(payload).encode()

        fwd_url = self.server.upstream.rstrip("/") + path
        fwd_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in (
                "host",
                "transfer-encoding",
                "content-length",
                "authorization",
                "api-key",
            )
        }
        fwd_headers["Content-Length"] = str(len(body))
        if self.server.upstream_api_key:
            fwd_headers["Authorization"] = f"Bearer {self.server.upstream_api_key}"

        response_started = False
        sse_response = False
        try:
            with self.server.client.stream(
                "POST",
                fwd_url,
                content=body,
                headers=fwd_headers,
            ) as resp:
                is_sse = "text/event-stream" in resp.headers.get("content-type", "")

                if resp.status_code >= 400:
                    err_body = resp.read()
                    logger.warning(
                        "LogprobProxy upstream %s returned %d overrides=%s sent=%s err=%s",
                        fwd_url,
                        resp.status_code,
                        self.server.request_overrides,
                        body[:300],
                        err_body[:300],
                    )
                    response_started = True
                    self.send_response(resp.status_code)
                    for key, value in resp.headers.items():
                        if key.lower() in ("transfer-encoding", "content-encoding", "content-length"):
                            continue
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(err_body)))
                    self.end_headers()
                    self.wfile.write(err_body)
                    return

                if is_sse and is_completions:
                    sse_response = True
                    response_started = True
                    self._forward_headers(resp, skip_length=True)
                    sse_buf = ""
                    for raw in resp.iter_bytes(chunk_size=512):
                        self.wfile.write(raw)
                        self.wfile.flush()
                        sse_buf += raw.decode("utf-8", errors="replace")
                        while "\n" in sse_buf:
                            line, sse_buf = sse_buf.split("\n", 1)
                            self._extract_sse_logprobs(line.rstrip("\r"))
                else:
                    resp_body = resp.read()
                    if is_completions:
                        try:
                            with self.server.lock:
                                self.server.raw_responses.append(json.loads(resp_body))
                        except (json.JSONDecodeError, ValueError):
                            pass
                    response_started = True
                    self.send_response(resp.status_code)
                    for key, value in resp.headers.items():
                        if key.lower() in ("transfer-encoding", "content-encoding", "content-length"):
                            continue
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(resp_body)))
                    self.end_headers()
                    self.wfile.write(resp_body)
        except Exception as exc:
            if response_started or sse_response:
                logger.warning(
                    "LogprobProxy upstream stream failed after response started url=%s err=%s",
                    fwd_url,
                    exc,
                )
                return
            try:
                self.send_error(502, str(exc))
            except Exception:
                pass

    def do_GET(self) -> None:
        if not self._is_authorized():
            return
        fwd_url = self.server.upstream.rstrip("/") + self.path
        fwd_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "authorization", "api-key")
        }
        if self.server.upstream_api_key:
            fwd_headers["Authorization"] = f"Bearer {self.server.upstream_api_key}"
        try:
            resp = self.server.client.get(fwd_url, headers=fwd_headers)
        except Exception as exc:
            self.send_error(502, str(exc))
            return
        resp_body = resp.content
        self.send_response(resp.status_code)
        for key, value in resp.headers.items():
            if key.lower() in ("transfer-encoding", "content-encoding", "content-length"):
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def log_message(self, *args: object) -> None:
        pass


class _LogprobCaptureServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        upstream: str,
        top_logprobs: int,
        *,
        bind_host: str,
        client_timeout: float,
        upstream_api_key: str = "",
        proxy_api_key: str = "",
        request_overrides: dict[str, Any] | None = None,
    ) -> None:
        super().__init__((bind_host, 0), _LogprobProxyHandler)
        self.upstream = upstream
        self.top_logprobs = top_logprobs
        self.upstream_api_key = upstream_api_key
        self.proxy_api_key = proxy_api_key
        self.request_overrides = dict(request_overrides or {})
        self.raw_responses: list[dict] = []
        self.streaming_logprobs: list[dict] = []
        self.lock = threading.Lock()
        self.client = httpx.Client(timeout=client_timeout, trust_env=False)


class LogprobCaptureProxy:
    """Small HTTP proxy that requests and stores OpenAI chat logprobs."""

    def __init__(
        self,
        upstream: str,
        top_logprobs: int,
        *,
        bind_host: str = "127.0.0.1",
        advertise_host: str = "",
        client_timeout: float = 120.0,
        upstream_api_key: str = "",
        proxy_api_key: str = "",
        request_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._upstream = normalize_openai_proxy_upstream(upstream)
        self._top_logprobs = int(top_logprobs)
        self._bind_host = str(bind_host or "127.0.0.1").strip()
        self._advertise_host = str(advertise_host or "").strip()
        self._client_timeout = float(client_timeout)
        self._upstream_api_key = str(upstream_api_key or "").strip()
        self._proxy_api_key = str(proxy_api_key or "").strip()
        self._request_overrides = dict(request_overrides or {})
        self._server: _LogprobCaptureServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = _LogprobCaptureServer(
            self._upstream,
            self._top_logprobs,
            bind_host=self._bind_host,
            client_timeout=self._client_timeout,
            upstream_api_key=self._upstream_api_key,
            proxy_api_key=self._proxy_api_key,
            request_overrides=self._request_overrides,
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        server.shutdown()
        server.server_close()
        server.client.close()

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass

    @property
    def proxy_url(self) -> str:
        assert self._server is not None
        port = self._server.server_address[1]
        host = self._advertise_host or self._server.server_address[0]
        if host in {"", "0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    @property
    def local_url(self) -> str:
        assert self._server is not None
        port = self._server.server_address[1]
        host = self._server.server_address[0]
        if host in {"", "0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    @property
    def upstream(self) -> str:
        return self._upstream

    def captured_records(self) -> list[dict]:
        assert self._server is not None
        with self._server.lock:
            sse_tokens = list(self._server.streaming_logprobs)
            payloads = list(self._server.raw_responses)
        return self._records_from_buffers(sse_tokens, payloads)

    def drain_records(self) -> list[dict]:
        assert self._server is not None
        with self._server.lock:
            sse_tokens = list(self._server.streaming_logprobs)
            payloads = list(self._server.raw_responses)
            self._server.streaming_logprobs.clear()
            self._server.raw_responses.clear()
        return self._records_from_buffers(sse_tokens, payloads)

    @staticmethod
    def _records_from_buffers(sse_tokens: list[dict], payloads: list[dict]) -> list[dict]:
        records: list[dict] = []
        token_index = 0
        if sse_tokens:
            for item in sse_tokens:
                records.append(raw_token_logprob_dict_to_record(item, token_index))
                token_index += 1
        else:
            for payload in payloads:
                new_records, token_index = extract_openai_logprob_records(
                    payload,
                    start_index=token_index,
                )
                records.extend(new_records)
        return records
