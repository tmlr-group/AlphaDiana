"""Parse saved OpenClaw sessions and recover response, reasoning, and error evidence."""

from __future__ import annotations

import json
from typing import Any


def extract_system_prompt(trajectory: list[dict]) -> str:
    """Extract system prompt from a trajectory list."""
    for entry in trajectory:
        if entry.get("role") == "system":
            return entry.get("content", "")
    # Fallback: check first assistant message for agent-like instructions
    for entry in trajectory:
        if entry.get("role") == "assistant":
            content = entry.get("content", "")
            if "You are" in content:
                return content
    return ""


def coerce_text_content(content: Any) -> str:
    """Coerce various content formats to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            if item.get("type") == "output_text":
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts).strip()
    return ""


def extract_response_text(payload: Any) -> str:
    """Extract assistant text from various gateway response formats."""
    if not isinstance(payload, dict):
        return ""

    # Standard OpenAI format
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        if isinstance(message, dict):
            content = coerce_text_content(message.get("content"))
            if content:
                return content
        delta = choices[0].get("delta", {})
        if isinstance(delta, dict):
            content = coerce_text_content(delta.get("content"))
            if content:
                return content
        text = choices[0].get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

    # Gateway-specific variants
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = coerce_text_content(item.get("content"))
            if content:
                return content

    message = payload.get("message")
    if isinstance(message, dict):
        content = coerce_text_content(message.get("content"))
        if content:
            return content

    return ""


def extract_reasoning_trajectory(payload: Any) -> list[dict]:
    """Best-effort extraction of a structured reasoning/tool trajectory."""
    if not isinstance(payload, dict):
        return []

    for key in (
        "trajectory",
        "reasoning_trajectory",
        "reasoning_trace",
        "trace",
        "steps",
        "events",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    output = payload.get("output")
    if isinstance(output, list):
        extracted = [item for item in output if isinstance(item, dict)]
        if extracted:
            return extracted

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message")
        if isinstance(message, dict):
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                return [item for item in tool_calls if isinstance(item, dict)]

    return []


def _is_empty_tool_call_payload(tool_name: Any, tool_input: Any) -> bool:
    name = str(tool_name or "").strip()
    if name:
        return False
    if tool_input is None:
        return True
    if isinstance(tool_input, dict):
        return not tool_input
    if isinstance(tool_input, str):
        return tool_input.strip() in {"", "{}"}
    return False


def recover_partial_output(trajectory: list[dict]) -> tuple[str, str]:
    """Recover partial assistant output and reasoning from session trajectory."""
    if not isinstance(trajectory, list):
        return "", ""

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    for entry in trajectory:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != "assistant":
            continue
        content = entry.get("content", "")
        thinking = entry.get("thinking", "")
        content_text = coerce_text_content(content)
        thinking_text = coerce_text_content(thinking)
        if content_text:
            text_parts.append(content_text)
        if thinking_text:
            reasoning_parts.append(thinking_text)

    return "\n".join(text_parts).strip(), "\n".join(reasoning_parts).strip()


def extract_trajectory_error(trajectory: list[dict]) -> str:
    """Return the latest explicit assistant error recorded in a session trajectory."""
    if not isinstance(trajectory, list):
        return ""

    for entry in reversed(trajectory):
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != "assistant":
            continue
        error = coerce_text_content(entry.get("error", "")).strip()
        if error:
            return error
    return ""


def _normalize_request_content(content: Any) -> str:
    """Normalize request text for transcript matching."""
    return coerce_text_content(content).replace("\r\n", "\n").strip()


def trajectory_matches_request(
    trajectory: list[dict],
    expected_user_content: str,
) -> bool:
    """Return whether a transcript contains the current request message."""
    expected = _normalize_request_content(expected_user_content)
    if not expected:
        return False

    for entry in trajectory:
        if not isinstance(entry, dict) or entry.get("role") != "user":
            continue
        candidate = _normalize_request_content(
            coerce_text_content(entry.get("content", ""))
        )
        if candidate == expected:
            return True
    return False


def slice_trajectory_to_request(
    trajectory: list[dict],
    expected_user_content: str,
) -> list[dict]:
    """Return the OpenClaw session segment for the current benchmark request."""
    expected = _normalize_request_content(expected_user_content)
    if not expected or not isinstance(trajectory, list):
        return trajectory

    start: int | None = None
    for index, entry in enumerate(trajectory):
        if not isinstance(entry, dict) or entry.get("role") != "user":
            continue
        candidate = _normalize_request_content(
            coerce_text_content(entry.get("content", ""))
        )
        if candidate == expected:
            start = index
            break
    if start is None:
        return trajectory

    end = len(trajectory)
    for index in range(start + 1, len(trajectory)):
        entry = trajectory[index]
        if not isinstance(entry, dict) or entry.get("role") != "user":
            continue
        candidate = _normalize_request_content(
            coerce_text_content(entry.get("content", ""))
        )
        if candidate != expected:
            end = index
            break

    return trajectory[start:end]


def parse_session(session_jsonl: str) -> list[dict]:
    """Parse an OpenClaw session JSONL file into a trajectory list.

    OpenClaw stores each event as a JSONL line with a "type" field:
      - "session": session metadata (id, cwd, timestamp)
      - "model_change": model provider switch
      - "message": user/assistant message with content blocks:
          - "text": plain text output
          - "thinking": model's internal reasoning (chain-of-thought)
          - "toolCall": tool invocation (name + arguments)
          - "toolResult": tool execution result
          - "tool_use" / "tool_result": alternative names for the above
      - "custom": custom events (model-snapshot, etc.)
      - "thinking_level_change": thinking mode changes
    """
    trajectory = []
    for line in session_jsonl.strip().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "message":
            msg = event.get("message", {})
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                thinking_parts = []
                tool_calls = []
                tool_results = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "thinking":
                        thinking_parts.append(block.get("thinking", ""))
                    elif block_type in ("toolCall", "tool_use"):
                        tool_name = block.get("name", "")
                        tool_input = block.get("arguments", block.get("input", {}))
                        if not _is_empty_tool_call_payload(tool_name, tool_input):
                            tool_calls.append(
                                {
                                    "id": block.get("id", ""),
                                    "tool": tool_name,
                                    "input": tool_input,
                                }
                            )
                    elif block_type in ("toolResult", "tool_result"):
                        tc = block.get("content", "")
                        if isinstance(tc, list):
                            tc = "\n".join(
                                b.get("text", "") for b in tc if isinstance(b, dict)
                            )
                        tool_results.append(
                            {
                                "tool_use_id": block.get(
                                    "tool_use_id", block.get("id", "")
                                ),
                                "content": tc,
                                "is_error": block.get(
                                    "isError", block.get("is_error", False)
                                ),
                            }
                        )
                content_text = "\n".join(text_parts) if text_parts else ""
                entry = {"role": role, "content": content_text}
                if thinking_parts:
                    entry["thinking"] = "\n".join(thinking_parts)
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                if tool_results:
                    entry["tool_results"] = tool_results
            else:
                entry = {"role": role, "content": content}

            usage = msg.get("usage")
            if usage:
                entry["usage"] = usage
            stop_reason = msg.get("stopReason")
            if stop_reason:
                entry["stop_reason"] = stop_reason
            error = msg.get("errorMessage")
            if error:
                entry["error"] = error

            trajectory.append(entry)

        elif event_type == "tool_use":
            trajectory.append(
                {
                    "role": "tool_use",
                    "tool": event.get("name", ""),
                    "input": event.get("input", {}),
                    "id": event.get("id", ""),
                }
            )

        elif event_type == "tool_result":
            trajectory.append(
                {
                    "role": "tool_result",
                    "tool_use_id": event.get("toolUseId", ""),
                    "content": event.get("content", ""),
                    "is_error": event.get("isError", False),
                }
            )

    return trajectory
