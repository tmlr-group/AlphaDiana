"""Project native ZeroClaw events without treating lifecycle logs as actions."""

from __future__ import annotations

from collections import Counter
from typing import Any

from alphadiana.harness.proxies.preservation import build_event_trajectories


def normalize_trace(
    request_messages: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    final_output: str,
) -> tuple[list[dict], list[dict], dict, dict]:
    """Return trajectories, complete observed usage and explicit coverage.

    Native 0.6.9 emits event_type/payload, whereas legacy traces use the shared
    message format. Raw records remain persisted by the caller. Native event IDs
    identify observations; they are not fabricated provider tool-call IDs.
    """
    native = [row for row in records if "event_type" in row]
    if not native:
        trajectory, reasoning = build_event_trajectories(
            request_messages, records, final_output=final_output
        )
        return trajectory, reasoning, {}, {"native_records": 0}

    trajectory, reasoning = build_event_trajectories(request_messages, [])
    counts: Counter = Counter()
    unknown: Counter = Counter()
    inputs = outputs = observed_usage = 0
    native_final_seen = False
    for row in native:
        kind = str(row.get("event_type", ""))
        counts[kind] += 1
        payload = row.get("payload")
        if not isinstance(payload, dict):
            unknown[kind] += 1
            continue
        step = None
        if kind == "llm_response":
            incoming, outgoing = payload.get("input_tokens"), payload.get("output_tokens")
            if all(type(value) is int and value >= 0 for value in (incoming, outgoing)):
                observed_usage += 1
                inputs += incoming
                outputs += outgoing
            content = payload.get("raw_response")
            if isinstance(content, str) and content.strip():
                step = {"role": "assistant", "content": content}
        elif kind == "turn_final_response":
            content = payload.get("text")
            if isinstance(content, str) and content.strip():
                native_final_seen = True
                step = {"role": "assistant", "type": "answer", "content": content}
        elif kind in {"tool_call_start", "tool_call_result"}:
            tool = str(payload.get("tool", ""))
            is_call = kind == "tool_call_start"
            content = payload.get("arguments" if is_call else "output")
            if content is None:
                content = row.get("error") or tool
            step = {
                "role": "assistant" if is_call else "tool",
                "type": "tool_call" if is_call else "tool_result",
                "name": tool,
                "content": str(content),
            }
            if is_call:
                step["arguments"] = content
            if not is_call and isinstance(row.get("success"), bool):
                step["success"] = row["success"]
        elif kind != "llm_request":
            unknown[kind] += 1
        if step is None:
            continue
        last = trajectory[-1] if trajectory else {}
        # Native final-response events repeat the preceding LLM response.
        if (
            kind == "turn_final_response"
            and last.get("type") in {None, "answer"}
            and all(last.get(key) == step.get(key) for key in ("role", "content"))
        ):
            last["type"] = "answer"
            last["native_final_event_id"] = row.get("id")
            continue
        step["native_event_id"] = row.get("id")
        step["timestamp"] = row.get("timestamp")
        trajectory.append(step)

    if final_output.strip() and not native_final_seen:
        last = trajectory[-1] if trajectory else {}
        if last.get("role") != "assistant" or last.get("type") not in {None, "answer"} or str(
            last.get("content", "")
        ).strip() != final_output.strip():
            trajectory.append({"role": "assistant", "content": final_output.strip()})
    complete = (
        observed_usage > 0
        and observed_usage == counts["llm_response"] == counts["llm_request"]
    )
    usage = (
        {"prompt_tokens": inputs, "completion_tokens": outputs, "total_tokens": inputs + outputs}
        if complete else {}
    )
    coverage = {
        "native_records": len(native),
        "event_counts": dict(counts),
        "unmapped_event_counts": dict(unknown),
        "non_native_records": len(records) - len(native),
        "responses_with_usage": observed_usage,
        "usage_complete": complete,
    }
    return trajectory, reasoning, usage, coverage
