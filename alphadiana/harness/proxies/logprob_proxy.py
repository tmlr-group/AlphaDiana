"""OpenAI-compatible proxy that injects and captures chat-completion logprobs."""

from __future__ import annotations

import http.server
import json
import logging
import os
import socketserver
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from alphadiana.harness.proxies.logprob_capture import (
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


def ensure_chat_has_user_message(messages: Any) -> tuple[Any, bool]:
    """Insert a minimal user turn when pruning leaves a tool-only chat history."""
    if not isinstance(messages, list) or not messages:
        return messages, False
    if any(isinstance(message, dict) and message.get("role") == "user" for message in messages):
        return messages, False

    insertion_index = 1 if isinstance(messages[0], dict) and messages[0].get("role") == "system" else 0
    normalized_messages = [
        *messages[:insertion_index],
        {"role": "user", "content": "Continue with the task."},
        *messages[insertion_index:],
    ]
    return normalized_messages, True


def _message_roles(messages: Any) -> list[str]:
    if not isinstance(messages, list):
        return []
    roles: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            roles.append(str(message.get("role", "")))
        else:
            roles.append("")
    return roles


def _message_content_lengths(messages: Any) -> list[int]:
    if not isinstance(messages, list):
        return []
    lengths: list[int] = []
    for message in messages:
        if not isinstance(message, dict):
            lengths.append(0)
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            lengths.append(len(content))
        else:
            try:
                lengths.append(len(json.dumps(content, ensure_ascii=False)))
            except (TypeError, ValueError):
                lengths.append(len(str(content)))
    return lengths


def _content_length(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(value))


def summarize_chat_completion_request(
    *,
    path: str,
    payload: dict[str, Any],
    original_messages: Any,
    normalized_system_messages: bool,
    inserted_user_message: bool,
) -> dict[str, Any]:
    """Return a content-free summary of an OpenAI chat-completion request."""
    outbound_messages = payload.get("messages")
    original_roles = _message_roles(original_messages)
    outbound_roles = _message_roles(outbound_messages)
    return {
        "path": path,
        "model": payload.get("model", ""),
        "stream": bool(payload.get("stream", False)),
        "message_count": len(outbound_roles),
        "message_roles_before": original_roles,
        "message_roles_after": outbound_roles,
        "system_indices_before": [
            index for index, role in enumerate(original_roles) if role == "system"
        ],
        "system_indices_after": [
            index for index, role in enumerate(outbound_roles) if role == "system"
        ],
        "normalized_system_messages": normalized_system_messages,
        "inserted_user_message": inserted_user_message,
        "message_content_lengths_after": _message_content_lengths(outbound_messages),
        "max_tokens": payload.get("max_tokens", None),
        "temperature": payload.get("temperature", None),
        "top_p": payload.get("top_p", None),
        "presence_penalty": payload.get("presence_penalty", None),
        "frequency_penalty": payload.get("frequency_penalty", None),
        "logprobs": payload.get("logprobs", None),
        "top_logprobs": payload.get("top_logprobs", None),
        "chat_template_kwargs": payload.get("chat_template_kwargs", None),
        "reasoning": payload.get("reasoning", None),
        "stream_options": payload.get("stream_options", None),
        "has_tools": bool(payload.get("tools")),
        "tool_count": len(payload.get("tools") or []) if isinstance(payload.get("tools"), list) else 0,
    }


def summarize_chat_completion_response(
    *,
    path: str,
    payload: dict[str, Any],
    status_code: int,
) -> dict[str, Any]:
    """Return a content-free summary of an OpenAI chat-completion response."""
    choices = payload.get("choices")
    if not isinstance(choices, list):
        choices = []

    content_lengths: list[int] = []
    reasoning_lengths: list[int] = []
    finish_reasons: list[str] = []
    tool_call_counts: list[int] = []
    for choice in choices:
        choice_dict = choice if isinstance(choice, dict) else {}
        message = choice_dict.get("message")
        if not isinstance(message, dict):
            message = choice_dict.get("delta") if isinstance(choice_dict.get("delta"), dict) else {}
        content_lengths.append(_content_length(message.get("content")))
        reasoning_value = (
            message.get("reasoning")
            if "reasoning" in message
            else message.get("reasoning_content")
        )
        reasoning_lengths.append(_content_length(reasoning_value))
        finish_reasons.append(str(choice_dict.get("finish_reason") or ""))
        tool_calls = message.get("tool_calls")
        tool_call_counts.append(len(tool_calls) if isinstance(tool_calls, list) else 0)

    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    empty_content_and_reasoning = [
        index
        for index, (content_len, reasoning_len) in enumerate(zip(content_lengths, reasoning_lengths))
        if content_len == 0 and reasoning_len == 0
    ]
    empty_content_reasoning_and_tool = [
        index
        for index, (content_len, reasoning_len, tool_count) in enumerate(
            zip(content_lengths, reasoning_lengths, tool_call_counts)
        )
        if content_len == 0 and reasoning_len == 0 and tool_count == 0
    ]
    return {
        "path": path,
        "status_code": status_code,
        "model": payload.get("model", ""),
        "choice_count": len(choices),
        "finish_reasons": finish_reasons,
        "content_lengths": content_lengths,
        "reasoning_lengths": reasoning_lengths,
        "tool_call_counts": tool_call_counts,
        "empty_content_choice_count": sum(1 for length in content_lengths if length == 0),
        "empty_reasoning_choice_count": sum(1 for length in reasoning_lengths if length == 0),
        "empty_content_and_reasoning_choice_count": len(empty_content_and_reasoning),
        "empty_content_and_reasoning_choice_indices": empty_content_and_reasoning,
        "empty_content_reasoning_and_tool_choice_count": len(
            empty_content_reasoning_and_tool
        ),
        "empty_content_reasoning_and_tool_choice_indices": empty_content_reasoning_and_tool,
        "prompt_tokens": usage.get("prompt_tokens", None),
        "completion_tokens": usage.get("completion_tokens", None),
        "total_tokens": usage.get("total_tokens", None),
    }


def _empty_stream_summary(path: str, model: str, status_code: int) -> dict[str, Any]:
    return {
        "path": path,
        "status_code": status_code,
        "model": model,
        "stream": True,
        "choice_count": 0,
        "finish_reasons": [],
        "content_lengths": [],
        "reasoning_lengths": [],
        "tool_call_counts": [],
        "empty_content_choice_count": 0,
        "empty_reasoning_choice_count": 0,
        "empty_content_and_reasoning_choice_count": 0,
        "empty_content_and_reasoning_choice_indices": [],
        "empty_content_reasoning_and_tool_choice_count": 0,
        "empty_content_reasoning_and_tool_choice_indices": [],
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


def _content_from_stream_delta(delta: dict[str, Any], key: str) -> int:
    value = delta.get(key)
    return _content_length(value)


def update_chat_completion_stream_summary(
    summary: dict[str, Any],
    line: str,
) -> None:
    """Update a content-free chat-completion summary from one SSE line."""
    text = str(line or "").strip()
    if not text.startswith("data:"):
        return
    data = text.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    if payload.get("model") and not summary.get("model"):
        summary["model"] = payload.get("model")

    usage = payload.get("usage")
    if isinstance(usage, dict):
        summary["prompt_tokens"] = usage.get("prompt_tokens")
        summary["completion_tokens"] = usage.get("completion_tokens")
        summary["total_tokens"] = usage.get("total_tokens")

    choices = payload.get("choices")
    if not isinstance(choices, list):
        return

    content_lengths = summary.setdefault("content_lengths", [])
    reasoning_lengths = summary.setdefault("reasoning_lengths", [])
    tool_call_sets = summary.setdefault("_tool_call_sets", [])
    finish_reasons = summary.setdefault("finish_reasons", [])
    for fallback_index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        index = choice.get("index")
        if not isinstance(index, int) or index < 0:
            index = fallback_index
        while len(content_lengths) <= index:
            content_lengths.append(0)
        while len(reasoning_lengths) <= index:
            reasoning_lengths.append(0)
        while len(tool_call_sets) <= index:
            tool_call_sets.append(set())

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content_lengths[index] += _content_from_stream_delta(delta, "content")
        if "reasoning" in delta:
            reasoning_lengths[index] += _content_from_stream_delta(delta, "reasoning")
        else:
            reasoning_lengths[index] += _content_from_stream_delta(delta, "reasoning_content")

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for call_fallback_index, call in enumerate(tool_calls):
                if not isinstance(call, dict):
                    continue
                call_key = call.get("id")
                if call_key in (None, ""):
                    call_key = call.get("index")
                if call_key in (None, ""):
                    call_key = call_fallback_index
                tool_call_sets[index].add(str(call_key))

        finish_reason = choice.get("finish_reason")
        if finish_reason:
            finish_reasons.append(str(finish_reason))


def finalize_chat_completion_stream_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Finalize a content-free SSE chat-completion summary."""
    content_lengths = list(summary.get("content_lengths") or [])
    reasoning_lengths = list(summary.get("reasoning_lengths") or [])
    tool_call_sets = list(summary.pop("_tool_call_sets", []) or [])
    choice_count = max(len(content_lengths), len(reasoning_lengths), len(tool_call_sets))
    while len(content_lengths) < choice_count:
        content_lengths.append(0)
    while len(reasoning_lengths) < choice_count:
        reasoning_lengths.append(0)
    tool_call_counts: list[int] = []
    for index in range(choice_count):
        calls = tool_call_sets[index] if index < len(tool_call_sets) else set()
        tool_call_counts.append(len(calls))

    empty_content_and_reasoning = [
        index
        for index, (content_len, reasoning_len) in enumerate(zip(content_lengths, reasoning_lengths))
        if content_len == 0 and reasoning_len == 0
    ]
    empty_content_reasoning_and_tool = [
        index
        for index, (content_len, reasoning_len, tool_count) in enumerate(
            zip(content_lengths, reasoning_lengths, tool_call_counts)
        )
        if content_len == 0 and reasoning_len == 0 and tool_count == 0
    ]
    summary["choice_count"] = choice_count
    summary["content_lengths"] = content_lengths
    summary["reasoning_lengths"] = reasoning_lengths
    summary["tool_call_counts"] = tool_call_counts
    summary["empty_content_choice_count"] = sum(1 for value in content_lengths if value == 0)
    summary["empty_reasoning_choice_count"] = sum(1 for value in reasoning_lengths if value == 0)
    summary["empty_content_and_reasoning_choice_count"] = len(empty_content_and_reasoning)
    summary["empty_content_and_reasoning_choice_indices"] = empty_content_and_reasoning
    summary["empty_content_reasoning_and_tool_choice_count"] = len(
        empty_content_reasoning_and_tool
    )
    summary["empty_content_reasoning_and_tool_choice_indices"] = (
        empty_content_reasoning_and_tool
    )
    return summary


def _empty_stream_aggregate(model: str) -> dict[str, Any]:
    return {
        "id": "",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [],
        "usage": None,
        "_choice_states": [],
    }


def _stream_choice_state(aggregate: dict[str, Any], index: int) -> dict[str, Any]:
    states = aggregate.setdefault("_choice_states", [])
    while len(states) <= index:
        states.append({
            "index": len(states),
            "role": None,
            "content_parts": [],
            "reasoning_parts": [],
            "reasoning_content_parts": [],
            "tool_calls": {},
            "finish_reason": None,
            "logprob_content": [],
        })
    return states[index]


def _append_text_part(parts: list[str], value: Any) -> None:
    if value in (None, ""):
        return
    if isinstance(value, str):
        parts.append(value)
        return
    try:
        parts.append(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        parts.append(str(value))


def _merge_stream_tool_call(
    state: dict[str, Any],
    call: dict[str, Any],
    fallback_index: int,
) -> None:
    raw_index = call.get("index")
    index = raw_index if isinstance(raw_index, int) and raw_index >= 0 else fallback_index
    tool_calls = state.setdefault("tool_calls", {})
    current = tool_calls.setdefault(index, {"index": index})
    if call.get("id"):
        current["id"] = call.get("id")
    if call.get("type"):
        current["type"] = call.get("type")

    function = call.get("function")
    if isinstance(function, dict):
        current_function = current.setdefault("function", {})
        if function.get("name"):
            current_function["name"] = (
                str(current_function.get("name") or "") + str(function.get("name"))
            )
        if function.get("arguments"):
            current_function["arguments"] = (
                str(current_function.get("arguments") or "")
                + str(function.get("arguments"))
            )


def update_chat_completion_stream_aggregate(
    aggregate: dict[str, Any],
    line: str,
) -> None:
    """Accumulate an upstream SSE chat-completion response into a JSON payload."""
    text = str(line or "").strip()
    if not text.startswith("data:"):
        return
    data = text.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(payload, dict):
        return

    for key in ("id", "object", "created", "model"):
        if payload.get(key) not in (None, ""):
            aggregate[key] = payload.get(key)
    usage = payload.get("usage")
    if isinstance(usage, dict):
        aggregate["usage"] = usage

    choices = payload.get("choices")
    if not isinstance(choices, list):
        return
    for fallback_index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        index = choice.get("index")
        if not isinstance(index, int) or index < 0:
            index = fallback_index
        state = _stream_choice_state(aggregate, index)
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message") if isinstance(choice.get("message"), dict) else {}

        if delta.get("role"):
            state["role"] = str(delta.get("role"))
        _append_text_part(state["content_parts"], delta.get("content"))
        _append_text_part(state["reasoning_parts"], delta.get("reasoning"))
        _append_text_part(
            state["reasoning_content_parts"],
            delta.get("reasoning_content"),
        )

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for call_fallback_index, call in enumerate(tool_calls):
                if isinstance(call, dict):
                    _merge_stream_tool_call(state, call, call_fallback_index)

        logprob_content = (choice.get("logprobs") or {}).get("content")
        if isinstance(logprob_content, list):
            state["logprob_content"].extend(logprob_content)

        finish_reason = choice.get("finish_reason")
        if finish_reason:
            state["finish_reason"] = finish_reason


def finalize_chat_completion_stream_aggregate(
    aggregate: dict[str, Any],
) -> dict[str, Any]:
    """Finalize an accumulated upstream SSE response as non-streaming JSON."""
    aggregate["object"] = "chat.completion"
    states = list(aggregate.pop("_choice_states", []) or [])
    choices: list[dict[str, Any]] = []
    for fallback_index, state in enumerate(states):
        index = state.get("index")
        if not isinstance(index, int) or index < 0:
            index = fallback_index
        content = "".join(state.get("content_parts") or [])
        reasoning = "".join(state.get("reasoning_parts") or [])
        reasoning_content = "".join(state.get("reasoning_content_parts") or [])
        message: dict[str, Any] = {
            "role": state.get("role") or "assistant",
            "content": content,
        }
        if reasoning:
            message["reasoning"] = reasoning
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
        tool_calls = []
        for call_index in sorted((state.get("tool_calls") or {}).keys()):
            call = dict(state["tool_calls"][call_index])
            call.pop("index", None)
            tool_calls.append(call)
        if tool_calls:
            message["tool_calls"] = tool_calls

        choice = {
            "index": index,
            "message": message,
            "finish_reason": state.get("finish_reason"),
        }
        if state.get("logprob_content"):
            choice["logprobs"] = {"content": list(state["logprob_content"])}
        choices.append(choice)

    aggregate["choices"] = choices
    return aggregate


def summarize_chat_completion_error_response(
    *,
    path: str,
    model: Any,
    status_code: int,
    response_body_length: int,
) -> dict[str, Any]:
    """Return a content-free summary of an upstream chat-completion error."""
    return {
        "path": path,
        "status_code": status_code,
        "model": model or "",
        "choice_count": 0,
        "finish_reasons": [],
        "content_lengths": [],
        "reasoning_lengths": [],
        "tool_call_counts": [],
        "empty_content_choice_count": 0,
        "empty_reasoning_choice_count": 0,
        "empty_content_and_reasoning_choice_count": 0,
        "empty_content_and_reasoning_choice_indices": [],
        "empty_content_reasoning_and_tool_choice_count": 0,
        "empty_content_reasoning_and_tool_choice_indices": [],
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "error_response": True,
        "response_body_length": response_body_length,
    }


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

        downstream_stream = False
        if is_completions:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {}
            downstream_stream = bool(payload.get("stream", False))
            original_messages = payload.get("messages")
            normalized = False
            inserted_user_message = False
            if self.server.normalize_system_messages:
                normalized_messages, normalized = normalize_chat_system_messages(original_messages)
                if normalized:
                    payload["messages"] = normalized_messages
                user_normalized_messages, inserted_user_message = ensure_chat_has_user_message(
                    payload.get("messages")
                )
                if inserted_user_message:
                    payload["messages"] = user_normalized_messages
            if self.server.inject_logprobs:
                payload["logprobs"] = True
                payload["top_logprobs"] = self.server.top_logprobs
            payload.update(self.server.request_overrides)
            if self.server.upstream_stream:
                payload["stream"] = True
                stream_options = payload.get("stream_options")
                if not isinstance(stream_options, dict):
                    stream_options = {}
                stream_options.setdefault("include_usage", True)
                payload["stream_options"] = stream_options
            if self.server.capture_request_summary:
                summary = summarize_chat_completion_request(
                    path=path,
                    payload=payload,
                    original_messages=original_messages,
                    normalized_system_messages=normalized,
                    inserted_user_message=inserted_user_message,
                )
                if downstream_stream != bool(payload.get("stream", False)):
                    summary["downstream_stream"] = downstream_stream
                    summary["upstream_stream_forced"] = True
                with self.server.lock:
                    self.server.request_summaries.append(summary)
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
                    if is_completions and self.server.capture_request_summary:
                        with self.server.lock:
                            self.server.response_summaries.append(
                                summarize_chat_completion_error_response(
                                    path=path,
                                    model=payload.get("model", "") if isinstance(payload, dict) else "",
                                    status_code=resp.status_code,
                                    response_body_length=len(err_body),
                                )
                            )
                    logger.warning(
                        "LogprobProxy upstream %s returned %d overrides=%s request_bytes=%d response_bytes=%d",
                        fwd_url,
                        resp.status_code,
                        self.server.request_overrides,
                        len(body),
                        len(err_body),
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
                    aggregate_downstream = (
                        not downstream_stream
                        and bool(payload.get("stream", False))
                        if isinstance(payload, dict)
                        else False
                    )
                    stream_summary = (
                        _empty_stream_summary(
                            path,
                            str(payload.get("model") or "") if isinstance(payload, dict) else "",
                            resp.status_code,
                        )
                        if self.server.capture_request_summary
                        else None
                    )
                    stream_aggregate = _empty_stream_aggregate(
                        str(payload.get("model") or "") if isinstance(payload, dict) else ""
                    )
                    if not aggregate_downstream:
                        sse_response = True
                        response_started = True
                        self._forward_headers(resp, skip_length=True)
                    sse_buf = ""
                    for raw in resp.iter_bytes(chunk_size=512):
                        if not aggregate_downstream:
                            self.wfile.write(raw)
                            self.wfile.flush()
                        sse_buf += raw.decode("utf-8", errors="replace")
                        while "\n" in sse_buf:
                            line, sse_buf = sse_buf.split("\n", 1)
                            parsed_line = line.rstrip("\r")
                            self._extract_sse_logprobs(parsed_line)
                            if stream_summary is not None:
                                update_chat_completion_stream_summary(
                                    stream_summary,
                                    parsed_line,
                                )
                            if aggregate_downstream:
                                update_chat_completion_stream_aggregate(
                                    stream_aggregate,
                                    parsed_line,
                                )
                    if sse_buf.strip():
                        parsed_line = sse_buf.rstrip("\r")
                        self._extract_sse_logprobs(parsed_line)
                        if stream_summary is not None:
                            update_chat_completion_stream_summary(
                                stream_summary,
                                parsed_line,
                            )
                        if aggregate_downstream:
                            update_chat_completion_stream_aggregate(
                                stream_aggregate,
                                parsed_line,
                            )
                    if stream_summary is not None:
                        with self.server.lock:
                            self.server.response_summaries.append(
                                finalize_chat_completion_stream_summary(stream_summary)
                            )
                    if aggregate_downstream:
                        response_payload = finalize_chat_completion_stream_aggregate(
                            stream_aggregate
                        )
                        resp_body = json.dumps(response_payload).encode("utf-8")
                        with self.server.lock:
                            self.server.raw_responses.append(response_payload)
                        response_started = True
                        self.send_response(resp.status_code)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(resp_body)))
                        self.end_headers()
                        self.wfile.write(resp_body)
                else:
                    resp_body = resp.read()
                    if is_completions:
                        try:
                            response_payload = json.loads(resp_body)
                        except (json.JSONDecodeError, ValueError):
                            response_payload = None
                        if isinstance(response_payload, dict):
                            with self.server.lock:
                                self.server.raw_responses.append(response_payload)
                                if self.server.capture_request_summary:
                                    self.server.response_summaries.append(
                                        summarize_chat_completion_response(
                                            path=path,
                                            payload=response_payload,
                                            status_code=resp.status_code,
                                        )
                                    )
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
        inject_logprobs: bool = True,
        normalize_system_messages: bool = True,
        capture_request_summary: bool = False,
        upstream_stream: bool = False,
    ) -> None:
        super().__init__((bind_host, 0), _LogprobProxyHandler)
        self.upstream = upstream
        self.top_logprobs = top_logprobs
        self.upstream_api_key = upstream_api_key
        self.proxy_api_key = proxy_api_key
        self.request_overrides = dict(request_overrides or {})
        self.inject_logprobs = bool(inject_logprobs)
        self.normalize_system_messages = bool(normalize_system_messages)
        self.capture_request_summary = bool(capture_request_summary)
        self.upstream_stream = bool(upstream_stream)
        self.request_summaries: list[dict] = []
        self.response_summaries: list[dict] = []
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
        inject_logprobs: bool = True,
        normalize_system_messages: bool = True,
        capture_request_summary: bool = False,
        upstream_stream: bool = False,
    ) -> None:
        self._upstream = normalize_openai_proxy_upstream(upstream)
        self._top_logprobs = int(top_logprobs)
        self._bind_host = str(bind_host or "127.0.0.1").strip()
        self._advertise_host = str(advertise_host or "").strip()
        self._client_timeout = float(client_timeout)
        self._upstream_api_key = str(upstream_api_key or "").strip()
        self._proxy_api_key = str(proxy_api_key or "").strip()
        self._request_overrides = dict(request_overrides or {})
        self._inject_logprobs = bool(inject_logprobs)
        self._normalize_system_messages = bool(normalize_system_messages)
        self._capture_request_summary = bool(capture_request_summary)
        self._upstream_stream = bool(upstream_stream)
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
            inject_logprobs=self._inject_logprobs,
            normalize_system_messages=self._normalize_system_messages,
            capture_request_summary=self._capture_request_summary,
            upstream_stream=self._upstream_stream,
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

    def request_summaries(self) -> list[dict]:
        assert self._server is not None
        with self._server.lock:
            return list(self._server.request_summaries)

    def drain_request_summaries(self) -> list[dict]:
        assert self._server is not None
        with self._server.lock:
            summaries = list(self._server.request_summaries)
            self._server.request_summaries.clear()
        return summaries

    def response_summaries(self) -> list[dict]:
        assert self._server is not None
        with self._server.lock:
            return list(self._server.response_summaries)

    def drain_response_summaries(self) -> list[dict]:
        assert self._server is not None
        with self._server.lock:
            summaries = list(self._server.response_summaries)
            self._server.response_summaries.clear()
        return summaries

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
