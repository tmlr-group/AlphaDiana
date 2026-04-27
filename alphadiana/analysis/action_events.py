"""Action-intent event rows for persisted trajectory analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

CANONICAL_ACTIONS = ("plan", "reason", "tool_use", "verify", "recover", "answer")
ACTION_PRIORITY = ("answer", "recover", "verify", "tool_use", "plan", "reason")
ACTION_LABEL_CONFIDENCES = ("strict", "heuristic")
EVENT_SOURCES = ("trajectory", "normalized_trace", "artifact_stream", "raw_text")
TOOL_SUBTYPES = (
    "search",
    "read",
    "write",
    "memory_read",
    "memory_write",
    "shell",
    "python",
    "subagent",
    "browser",
    "other",
)
OBSERVATION_STATUSES = ("none", "info", "success", "fail", "error", "timeout", "truncated")

_RECOVERY_OBSERVATION_STATUSES = {"error", "fail", "timeout", "truncated"}


@dataclass(frozen=True)
class ActionEvent:
    """One non-overlapping canonical action label with record provenance."""

    run_id: str
    harness: str
    task_id: str
    sample_index: int
    step_id: str
    canonical_action: str
    action_label_confidence: str
    source: str
    tool_subtype: str
    observation_status: str
    recovery_context: bool
    text_span: str
    score_status: str
    correct: bool | None

    def to_row(self) -> dict[str, Any]:
        """Return a plain serializable event row."""
        return asdict(self)


def classify_tool_subtype(name: str, text: str = "") -> str:
    """Map harness-specific tool names or text into locked subtype buckets."""
    haystack = f"{name} {text}".lower()
    if "search" in haystack:
        return "search"
    if any(token in haystack for token in ("memory_write", "save memory", "save this memory", "write memory", "store memory")):
        return "memory_write"
    if any(token in haystack for token in ("memory_read", "read memory", "recall", "retrieve memory", "memory")):
        return "memory_read"
    if any(token in haystack for token in ("read", "view", "cat", "open_file")):
        return "read"
    if any(token in haystack for token in ("write", "edit", "patch", "replace", "create_file")):
        return "write"
    if any(token in haystack for token in ("bash", "shell", "terminal", "exec", "command")):
        return "shell"
    if "python" in haystack:
        return "python"
    if any(token in haystack for token in ("subagent", "delegate", "agent")):
        return "subagent"
    if any(token in haystack for token in ("browser", "page", "click", "screenshot")):
        return "browser"
    return "other"


def observation_status_from_text(text: str) -> str:
    """Infer observation status without treating observations as actions."""
    normalized = text.strip().lower()
    if not normalized:
        return "none"
    if "timeout" in normalized or "timed out" in normalized:
        return "timeout"
    if "incorrect" in normalized or "assertion failed" in normalized or "nonzero" in normalized:
        return "fail"
    if any(token in normalized for token in ("traceback", "exception", "error", "failed")):
        return "error"
    if any(token in normalized for token in ("success", "passed", "valid", "ok")):
        return "success"
    return "info"


def classify_action_intent(
    step: dict[str, Any],
    *,
    previous_observation_status: str = "none",
) -> tuple[str, str, str]:
    """Classify a persisted trajectory step into one canonical action."""
    text = _step_text(step)
    text_lower = text.lower()
    step_type = str(step.get("type") or "").strip().lower()
    tool_name = _first_text(
        step.get("tool_name"),
        step.get("tool"),
        step.get("name"),
        _first_tool_call_name(step),
    )
    tool_subtype = classify_tool_subtype(tool_name, text)
    has_tool = bool(tool_name or step.get("tool_calls") or step_type in {"tool", "tool_use", "toolcall", "tool_call"})

    if _looks_like_answer(step_type, text_lower):
        return "answer", "strict", tool_subtype
    if previous_observation_status in _RECOVERY_OBSERVATION_STATUSES and text.strip():
        return "recover", "heuristic", tool_subtype
    if _looks_like_verification(step_type, text_lower):
        return "verify", "heuristic", tool_subtype
    if has_tool:
        return "tool_use", "strict", tool_subtype
    if _looks_like_plan(step_type, text_lower):
        return "plan", "heuristic", tool_subtype
    return "reason", "heuristic", tool_subtype


def _step_text(step: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("content", "text", "message", "reasoning_content", "thinking", "raw_output", "predicted"):
        value = step.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return "\n".join(values)


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_tool_call_name(step: dict[str, Any]) -> str:
    raw_calls = step.get("tool_calls")
    if not isinstance(raw_calls, list):
        return ""
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        name = _first_text(raw_call.get("name"), raw_call.get("tool"), raw_call.get("tool_name"))
        if name:
            return name
    return ""


def _looks_like_answer(step_type: str, text_lower: str) -> bool:
    if step_type in {"final", "answer", "final_answer"}:
        return True
    return any(token in text_lower for token in ("final answer", "answer:", "the answer is", "therefore the answer"))


def _looks_like_verification(step_type: str, text_lower: str) -> bool:
    if step_type in {"verification", "verify"}:
        return True
    return any(token in text_lower for token in ("verify", "verification", "check", "validate", "test"))


def _looks_like_plan(step_type: str, text_lower: str) -> bool:
    if step_type in {"plan", "planning"}:
        return True
    return any(token in text_lower for token in ("plan:", "i will", "first ", "next ", "step "))
