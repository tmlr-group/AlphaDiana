"""Helpers for normalizing preserved runtime artifacts into AgentResponse fields."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any


def parse_jsonl_records(raw_text: str) -> list[dict[str, Any]]:
    """Parse best-effort JSONL text into a list of object records."""
    records: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def normalize_request_trajectory(request_messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return request messages as trajectory-safe entries."""
    trajectory: list[dict[str, Any]] = []
    for message in request_messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip() or "user"
        entry: dict[str, Any] = {"role": role, "content": message.get("content", "")}
        thinking = message.get("thinking")
        if isinstance(thinking, str) and thinking.strip():
            entry["thinking"] = thinking
        trajectory.append(entry)
    return trajectory


def add_artifact_file_refs(
    artifact_manifest: dict[str, Any] | None,
    **file_refs: str | None,
) -> dict[str, Any]:
    """Merge stable file aliases into ``artifact_manifest['files']``."""
    manifest = deepcopy(artifact_manifest) if isinstance(artifact_manifest, dict) else {}
    files = manifest.setdefault("files", {})
    for key, value in file_refs.items():
        if isinstance(value, str) and value.strip():
            files[key] = value
    return manifest


def build_event_trajectories(
    request_messages: list[dict[str, Any]] | None,
    records: list[dict[str, Any]] | None,
    *,
    final_output: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize runtime records into trajectory and reasoning_trajectory lists."""
    trajectory = normalize_request_trajectory(request_messages)
    reasoning_trajectory: list[dict[str, Any]] = []

    for record in records or []:
        step, reasoning = _runtime_record_to_steps(record)
        if step is not None and not _step_already_present(trajectory, step):
            trajectory.append(step)
        if reasoning is not None:
            reasoning_trajectory.append(reasoning)

    final_output = final_output.strip()
    if final_output:
        final_step = {"role": "assistant", "content": final_output}
        if not _step_already_present(trajectory, final_step):
            trajectory.append(final_step)
    return trajectory, reasoning_trajectory


def build_runtime_trace_summary(
    *,
    output_text: str = "",
    stderr_text: str = "",
    records: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable response_json envelope for CLI-style runtimes."""
    summary = dict(extra or {})
    if output_text:
        summary.setdefault("output_text", output_text)
    if stderr_text:
        summary.setdefault("stderr_text", stderr_text)
    if records:
        summary.setdefault("records", records)
    return summary


def build_text_step_trajectories(
    request_messages: list[dict[str, Any]] | None,
    output_text: str,
    *,
    max_segments: int = 16,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split preserved assistant text into step-like trajectory entries."""
    trajectory = normalize_request_trajectory(request_messages)
    segments = _split_text_segments(output_text, max_segments=max_segments)
    if not segments:
        return trajectory, []

    reasoning_trajectory: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        step = {"role": "assistant", "content": segment}
        if not _step_already_present(trajectory, step):
            trajectory.append(step)
        if len(segments) > 1 and index < len(segments) - 1:
            reasoning_trajectory.append(
                {
                    "role": "assistant",
                    "type": "reasoning",
                    "content": segment,
                }
            )
    return trajectory, reasoning_trajectory


def _runtime_record_to_steps(
    record: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    step_type = str(record.get("type") or "").strip()
    role = "assistant"
    content = ""
    thinking = ""

    message = record.get("message")
    if isinstance(message, dict):
        role = str(message.get("role") or role).strip() or role
        content = _content_to_text(message.get("content", ""))
        thinking = _content_to_text(message.get("thinking", ""))
    elif isinstance(message, str) and message.strip():
        content = message.strip()

    if not content:
        content = _content_to_text(record.get("content", ""))
    if not content:
        content = _content_to_text(record.get("text", ""))

    part = record.get("part")
    if not content and isinstance(part, dict):
        part_type = str(part.get("type") or "").strip()
        if part_type == "assistant":
            role = "assistant"
            content = _content_to_text(part.get("message", {}))
        else:
            content = _content_to_text(part)
            if not step_type and part_type:
                step_type = part_type

    choices = record.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message_obj = choice.get("message", {})
        if not content and isinstance(message_obj, dict):
            role = str(message_obj.get("role") or role).strip() or role
            content = _content_to_text(message_obj.get("content", ""))
            if not thinking:
                thinking = _content_to_text(message_obj.get("reasoning_content", ""))
        delta = choice.get("delta", {})
        if isinstance(delta, dict):
            if not content:
                content = _content_to_text(delta.get("content", ""))
            if not thinking:
                thinking = _content_to_text(delta.get("reasoning_content", ""))

    if not content and step_type in {"tool", "tool_use", "tool_call", "toolCall", "tool_result", "toolResult"}:
        tool_name = str(record.get("name") or "").strip()
        if isinstance(part, dict):
            tool_name = str(part.get("name") or tool_name).strip()
        content = tool_name or step_type

    if not content and not thinking:
        if step_type in {"assistant", "message", "text", ""}:
            return None, None
        content = json.dumps(record, ensure_ascii=False, sort_keys=True)

    step: dict[str, Any] | None = None
    if content:
        step = {"role": role, "content": content}
        if step_type:
            step["type"] = step_type

    reasoning: dict[str, Any] | None = None
    if thinking:
        reasoning = {
            "role": "assistant",
            "type": "reasoning",
            "content": thinking,
        }
        if step is not None:
            step["thinking"] = thinking
    elif step is not None and step_type and step_type not in {"assistant", "message", "text"}:
        reasoning = {
            "role": step["role"],
            "type": step_type,
            "content": step["content"],
        }
    return step, reasoning


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = _content_to_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        item_type = str(content.get("type") or "").strip()
        if item_type == "text":
            return str(content.get("text") or "").strip()
        if item_type in {"toolCall", "tool_call", "toolUse", "tool_use", "tool", "toolResult", "tool_result"}:
            tool_name = str(content.get("name") or content.get("tool_name") or item_type).strip()
            tool_text = str(content.get("text") or content.get("content") or "").strip()
            if tool_name and tool_text:
                return f"{tool_name}: {tool_text}"
            return tool_name or tool_text
        for key in ("content", "text", "message"):
            if key in content:
                text = _content_to_text(content.get(key))
                if text:
                    return text
    return ""


def _split_text_segments(text: str, *, max_segments: int) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    raw_segments = [segment.strip() for segment in re.split(r"\n\s*\n+", stripped) if segment.strip()]
    segments: list[str] = []
    for segment in raw_segments:
        if segments and segments[-1] == segment:
            continue
        segments.append(segment)
    if len(segments) <= 1:
        return [stripped]
    if len(segments) <= max_segments:
        return segments
    head = max_segments - 1
    merged_tail = "\n\n".join(segments[head:]).strip()
    return [*segments[:head], merged_tail] if merged_tail else segments[:max_segments]


def _step_already_present(
    trajectory: list[dict[str, Any]],
    candidate: dict[str, Any],
) -> bool:
    if not trajectory:
        return False
    last = trajectory[-1]
    return (
        last.get("role") == candidate.get("role")
        and last.get("content") == candidate.get("content")
        and last.get("type") == candidate.get("type")
    )
