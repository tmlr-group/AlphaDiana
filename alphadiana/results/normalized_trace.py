"""Helpers for writing a stable persisted trace schema.

The persisted task JSON remains a lightweight summary, while the richer
``normalized_trace.json`` artifact provides a harness-agnostic view of the
saved request/trajectory data.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

TRACE_SCHEMA_VERSION = "alphadiana_trace.v1"
TRACE_ARTIFACT_NAME = "normalized_trace.json"

_TOOL_USE_TYPES = {"tool", "tool_use", "toolcall", "tool_call", "tooluse"}
_TOOL_RESULT_TYPES = {"tool_result", "toolresult"}
_REASONING_TYPES = {"reasoning", "thinking"}
_LOGPROB_REF_ALIASES = ("logprobs_float", "logprobs_int16")


def normalize_request_messages(
    request_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return request messages in a stable persisted format."""
    normalized: list[dict[str, Any]] = []
    for message in request_messages or []:
        if not isinstance(message, dict):
            continue
        role = _canonical_role(str(message.get("role") or "").strip(), step_type="message")
        content = _coerce_text(message.get("content", ""))
        entry: dict[str, Any] = {
            "role": role,
            "content": content,
            "type": "system" if role == "system" else "message",
        }
        thinking = _coerce_text(message.get("thinking", ""))
        if thinking:
            entry["thinking"] = thinking
        normalized.append(entry)
    return normalized


def normalize_persisted_trajectory(
    trajectory: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize trajectory steps into a stable persisted schema."""
    normalized: list[dict[str, Any]] = []
    for raw_step in trajectory or []:
        if not isinstance(raw_step, dict):
            continue
        step = _normalize_trace_step(raw_step)
        if step is None:
            continue
        if normalized and _steps_equal(normalized[-1], step):
            continue
        normalized.append(step)
    return normalized


def normalize_reasoning_trajectory(
    reasoning_trajectory: list[dict[str, Any]] | None,
    *,
    trajectory: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return assistant reasoning-only entries for persisted task JSONs."""
    normalized: list[dict[str, Any]] = []

    for raw_step in reasoning_trajectory or []:
        step = _normalize_reasoning_step(raw_step)
        if step is None:
            continue
        if normalized and _steps_equal(normalized[-1], step):
            continue
        normalized.append(step)

    if normalized:
        return normalized

    for raw_step in trajectory or []:
        if not isinstance(raw_step, dict):
            continue
        thinking = _coerce_text(raw_step.get("thinking", ""))
        if not thinking:
            continue
        step = {
            "role": "assistant",
            "type": "reasoning",
            "content": thinking,
        }
        if normalized and _steps_equal(normalized[-1], step):
            continue
        normalized.append(step)
    return normalized


def build_normalized_trace(
    *,
    task_id: str,
    sample_index: int,
    response: Any,
    run_metadata: dict[str, Any] | None = None,
    trajectory: list[dict[str, Any]] | None = None,
    reasoning_trajectory: list[dict[str, Any]] | None = None,
    artifact_files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable, harness-agnostic trace artifact."""
    response_metadata = (
        deepcopy(response.metadata)
        if isinstance(getattr(response, "metadata", None), dict)
        else {}
    )
    response_json = (
        deepcopy(response.response_json)
        if isinstance(getattr(response, "response_json", None), dict)
        else {}
    )
    run_metadata = deepcopy(run_metadata or {})
    normalized_request_messages = normalize_request_messages(
        getattr(response, "request_messages", None),
    )
    normalized_trajectory = trajectory or normalize_persisted_trajectory(
        getattr(response, "trajectory", None),
    )
    normalized_reasoning = reasoning_trajectory or normalize_reasoning_trajectory(
        getattr(response, "reasoning_trajectory", None),
        trajectory=getattr(response, "trajectory", None),
    )
    artifact_files = artifact_files or {}
    refs = deepcopy(artifact_files)
    refs.pop("normalized_trace", None)
    for alias in _LOGPROB_REF_ALIASES:
        if alias in artifact_files:
            refs[alias] = artifact_files[alias]

    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "task_id": task_id,
        "sample_index": sample_index,
        "session_id": _first_non_empty(
            response_metadata.get("session_id"),
            response_json.get("session_id"),
            response_metadata.get("gateway_response_id"),
        ),
        "agent": {
            "name": _stringify(run_metadata.get("agent_name")),
            "version": _stringify(run_metadata.get("agent_version")),
            "transport": _stringify(response_metadata.get("transport")),
            "runner": _stringify(response_metadata.get("runner")),
            "model": _first_non_empty(
                response_metadata.get("model_name"),
                response_metadata.get("model"),
                response_json.get("model"),
            ),
        },
        "request": {
            "system_prompt": _stringify(getattr(response, "system_prompt", "")),
            "messages": normalized_request_messages,
        },
        "steps": normalized_trajectory,
        "reasoning_steps": normalized_reasoning,
        "final_output": {
            "answer": getattr(response, "answer", None),
            "raw_output": _stringify(getattr(response, "raw_output", "")),
            "finish_reason": _stringify(getattr(response, "finish_reason", "")),
        },
        "metrics": {
            "wall_time_sec": float(getattr(response, "wall_time_sec", 0.0) or 0.0),
            "token_usage": deepcopy(getattr(response, "token_usage", {}) or {}),
        },
        "refs": refs,
    }


def _normalize_trace_step(raw_step: dict[str, Any]) -> dict[str, Any] | None:
    step_type = _canonical_step_type(raw_step)
    role = _canonical_role(str(raw_step.get("role") or "").strip(), step_type=step_type)
    content = _extract_step_content(raw_step, step_type=step_type)
    thinking = _coerce_text(raw_step.get("thinking", ""))
    tool_calls = _normalize_tool_calls(raw_step)
    tool_results = _normalize_tool_results(raw_step)

    if not content and tool_calls:
        content = "\n".join(
            _summarize_tool_call(call)
            for call in tool_calls
        ).strip()
    if not content and tool_results:
        content = "\n".join(
            result.get("content", "")
            for result in tool_results
            if result.get("content")
        ).strip()

    if not content and not thinking and not tool_calls and not tool_results:
        if not raw_step:
            return None
        content = json.dumps(raw_step, ensure_ascii=False, sort_keys=True)

    step: dict[str, Any] = {
        "role": role,
        "content": content,
        "type": step_type,
    }
    if thinking:
        step["thinking"] = thinking
    if tool_calls:
        step["tool_calls"] = tool_calls
    if tool_results:
        step["tool_results"] = tool_results
    return step


def _normalize_reasoning_step(raw_step: Any) -> dict[str, Any] | None:
    if not isinstance(raw_step, dict):
        return None

    content = _coerce_text(raw_step.get("reasoning_content", ""))
    if not content:
        raw_type = str(raw_step.get("type") or "").strip().lower()
        if raw_type in _REASONING_TYPES:
            content = _coerce_text(raw_step.get("content", ""))
        if not content:
            content = _coerce_text(raw_step.get("thinking", ""))

    if not content:
        return None

    return {
        "role": "assistant",
        "type": "reasoning",
        "content": content,
    }


def _normalize_tool_calls(raw_step: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    raw_value = raw_step.get("tool_calls")
    if isinstance(raw_value, list):
        for item in raw_value:
            normalized = _normalize_tool_call(item)
            if normalized is not None:
                tool_calls.append(normalized)
    elif _canonical_step_type(raw_step) == "tool_use":
        normalized = _normalize_tool_call(raw_step)
        if normalized is not None:
            tool_calls.append(normalized)
    return tool_calls


def _normalize_tool_call(raw_value: Any) -> dict[str, Any] | None:
    if not isinstance(raw_value, dict):
        return None

    tool_name = _first_non_empty(
        raw_value.get("tool"),
        raw_value.get("name"),
        raw_value.get("tool_name"),
    )
    raw_input = raw_value.get("input", raw_value.get("arguments", {}))
    if not isinstance(raw_input, dict):
        raw_input = {"value": raw_input}
    normalized = {
        "id": _stringify(raw_value.get("id")),
        "tool": tool_name,
        "input": deepcopy(raw_input),
    }
    if not normalized["id"] and not normalized["tool"] and not normalized["input"]:
        return None
    return normalized


def _normalize_tool_results(raw_step: dict[str, Any]) -> list[dict[str, Any]]:
    tool_results: list[dict[str, Any]] = []
    raw_value = raw_step.get("tool_results")
    if isinstance(raw_value, list):
        for item in raw_value:
            normalized = _normalize_tool_result(item)
            if normalized is not None:
                tool_results.append(normalized)
    elif _canonical_step_type(raw_step) == "tool_result":
        normalized = _normalize_tool_result(raw_step)
        if normalized is not None:
            tool_results.append(normalized)
    return tool_results


def _normalize_tool_result(raw_value: Any) -> dict[str, Any] | None:
    if not isinstance(raw_value, dict):
        return None

    content = _coerce_text(raw_value.get("content", ""))
    if not content:
        content = _coerce_text(raw_value.get("observation", ""))
    normalized = {
        "tool_use_id": _stringify(raw_value.get("tool_use_id", raw_value.get("id", ""))),
        "content": content,
        "is_error": bool(raw_value.get("is_error", raw_value.get("isError", False))),
    }
    if not normalized["tool_use_id"] and not normalized["content"] and not normalized["is_error"]:
        return None
    return normalized


def _canonical_step_type(raw_step: dict[str, Any]) -> str:
    raw_type = str(raw_step.get("type") or "").strip().lower()
    raw_role = str(raw_step.get("role") or "").strip().lower()

    if raw_type in _REASONING_TYPES or raw_role == "reasoning":
        return "reasoning"
    if raw_type in _TOOL_USE_TYPES or raw_role in _TOOL_USE_TYPES:
        return "tool_use"
    if raw_type in _TOOL_RESULT_TYPES or raw_role in _TOOL_RESULT_TYPES:
        return "tool_result"
    if raw_step.get("tool_calls"):
        return "tool_use"
    if raw_step.get("tool_results"):
        return "tool_result"
    if raw_role == "system":
        return "system"
    return "message"


def _canonical_role(raw_role: str, *, step_type: str) -> str:
    if raw_role in {"system", "user", "assistant", "tool"}:
        return raw_role
    if step_type == "system":
        return "system"
    if step_type == "tool_result":
        return "tool"
    if raw_role in {"tool_result", "tool"}:
        return "tool"
    if raw_role == "user":
        return "user"
    return "assistant"


def _extract_step_content(raw_step: dict[str, Any], *, step_type: str) -> str:
    for key in ("content", "text", "message"):
        text = _coerce_text(raw_step.get(key, ""))
        if text:
            return text

    if step_type == "tool_use":
        tool_calls = _normalize_tool_calls(raw_step)
        if tool_calls:
            return "\n".join(_summarize_tool_call(call) for call in tool_calls).strip()
        tool_name = _first_non_empty(raw_step.get("tool"), raw_step.get("name"))
        if tool_name:
            return tool_name

    if step_type == "tool_result":
        tool_results = _normalize_tool_results(raw_step)
        if tool_results:
            content = "\n".join(
                result.get("content", "")
                for result in tool_results
                if result.get("content")
            ).strip()
            if content:
                return content

    return ""


def _summarize_tool_call(tool_call: dict[str, Any]) -> str:
    tool_name = str(tool_call.get("tool") or "").strip() or "tool"
    tool_input = tool_call.get("input", {})
    if isinstance(tool_input, dict) and tool_input:
        keys = ", ".join(sorted(str(key) for key in tool_input.keys()))
        return f"{tool_name}({keys})"
    return tool_name


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_coerce_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        item_type = str(value.get("type") or "").strip().lower()
        if item_type == "text":
            return _coerce_text(value.get("text", ""))
        for key in ("content", "text", "message", "thinking", "reasoning_content"):
            text = _coerce_text(value.get(key, ""))
            if text:
                return text
        if value:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return ""


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _stringify(value)
        if text:
            return text
    return ""


def _stringify(value: Any) -> str:
    if value in ("", None):
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _steps_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("role") == right.get("role")
        and left.get("type") == right.get("type")
        and left.get("content") == right.get("content")
    )
