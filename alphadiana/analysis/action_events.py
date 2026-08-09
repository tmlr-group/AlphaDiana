"""Action-intent event rows for persisted trajectory analysis."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from alphadiana.analysis.result_reader import RunBundle, load_run_bundle, resolve_run_relative_path
from alphadiana.analysis.io.status import VALID_SCORE_STATUS, infer_score_status

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
PRIMARY_CORPORA = {
    "openclaw": "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
    "opencode": "full_gpqa_v2_opencode_qwen35_27b_logprobs",
    "zeroclaw": "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
}
DIRECTLLM_CANDIDATES = (
    "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs",
    "hf-alphadiana-benchmark-results/pilot_run/pilot_20260418_qwen35_27b_gpqa_diamond_directllm_t3",
    "/path/to/xxx/hub/datasets--T-MARS--alphadiana-benchmark-results/snapshots/751f852c58f6e6f9ba7b0242817ed458fa30572f/pilot_run/pilot_20260418_qwen35_27b_gpqa_diamond_directllm_t3",
)
BACKUP_EXCLUDE_PREFIX = "bkp_gpqa_20260425"

_RECOVERY_OBSERVATION_STATUSES = {"error", "fail", "timeout", "truncated"}
_TOOL_RESULT_TYPES = {"tool_result", "toolresult", "observation", "result"}
_OPENCODE_LIFECYCLE_MARKERS = ("step-start", "step-finish")


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


def normalized_records(bundle: RunBundle) -> list[dict[str, Any]]:
    """Return scorer-aware, duplicate-normalized records for behavior metrics."""
    source_records = _raw_task_records(bundle) if bundle.task_records else list(bundle.records)
    selected: dict[tuple[str, int], dict[str, Any]] = {}
    selected_statuses: dict[tuple[str, int], str] = {}

    for record in source_records:
        key = _sample_key(record)
        if key is None:
            continue
        status = infer_score_status(record)
        current = selected.get(key)
        if current is None:
            selected[key] = record
            selected_statuses[key] = status
            continue
        current_status = selected_statuses[key]
        if status == VALID_SCORE_STATUS and current_status != VALID_SCORE_STATUS:
            selected[key] = record
            selected_statuses[key] = status
        elif current_status != VALID_SCORE_STATUS:
            selected[key] = record
            selected_statuses[key] = status

    return list(selected.values())


def inventory_corpus(results_dir: Path, run_id: str, harness: str) -> dict[str, Any]:
    """Summarize persisted corpus shape before event extraction."""
    if _is_backup_candidate(run_id):
        raise ValueError(f"backup corpus is excluded: {run_id}")
    bundle = load_run_bundle(results_dir, run_id)
    raw_task_records = _raw_task_records(bundle)
    normalized = normalized_records(bundle)
    key_counts = Counter(
        key
        for key in (_sample_key(record) for record in (raw_task_records or bundle.records))
        if key is not None
    )
    status_counts = Counter(infer_score_status(record) for record in normalized)
    expected_sample_count = bundle.manifest.get("expected_sample_count")
    if expected_sample_count is None:
        expected_sample_count = len(normalized)

    return {
        "run_id": run_id,
        "harness": harness,
        "jsonl_records": len(bundle.records),
        "task_record_files": len(bundle.task_records),
        "task_records": len(raw_task_records),
        "expected_sample_count": int(expected_sample_count),
        "status_counts": dict(status_counts),
        "duplicate_sample_keys": sorted(key for key, count in key_counts.items() if count > 1),
    }


def select_directllm_baseline(
    results_dir: Path,
    candidates: Sequence[str] = DIRECTLLM_CANDIDATES,
) -> dict[str, Any]:
    """Select the most complete persisted DirectLLM baseline candidate."""
    best: dict[str, Any] | None = None
    for candidate in candidates:
        if _is_backup_candidate(candidate):
            continue
        candidate_path = Path(candidate)
        candidate_results_dir = candidate_path.parent if candidate_path.is_absolute() else results_dir
        candidate_run_id = candidate_path.name if candidate_path.is_absolute() else candidate
        bundle = load_run_bundle(candidate_results_dir, candidate_run_id)
        records = normalized_records(bundle)
        raw_task_records = _raw_task_records(bundle)
        summary = {
            "candidate": candidate,
            "run_id": candidate_run_id,
            "valid_scored": sum(1 for record in records if infer_score_status(record) == VALID_SCORE_STATUS),
            "task_records": len(raw_task_records),
            "jsonl_records": len(bundle.records),
            "bundle": bundle,
        }
        if best is None or _directllm_score(summary) > _directllm_score(best):
            best = summary
    if best is None:
        return {
            "candidate": None,
            "run_id": None,
            "valid_scored": 0,
            "task_records": 0,
            "jsonl_records": 0,
            "bundle": None,
        }
    return best


def extract_action_events(bundle: RunBundle, *, harness: str) -> list[ActionEvent]:
    """Extract canonical action rows from persisted task artifacts only."""
    events: list[ActionEvent] = []
    for record in normalized_records(bundle):
        if harness == "directllm":
            events.extend(_extract_directllm_events(bundle, record, harness=harness))
        else:
            events.extend(_extract_agent_events(bundle, record, harness=harness))
    return events


def extract_action_event_rows(bundle: RunBundle, *, harness: str) -> list[dict[str, Any]]:
    """Extract action events and return plain row dictionaries."""
    return [event.to_row() for event in extract_action_events(bundle, harness=harness)]


def _step_text(step: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("content", "text", "message", "reasoning_content", "thinking", "raw_output", "predicted"):
        value = step.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return "\n".join(values)


def _raw_task_records(bundle: RunBundle) -> list[dict[str, Any]]:
    return [
        record
        for task_id in sorted(bundle.task_records)
        for record in bundle.task_records[task_id]
    ]


def _sample_key(record: dict[str, Any]) -> tuple[str, int] | None:
    task_id = str(record.get("task_id") or "").strip()
    if not task_id:
        return None
    return task_id, int(record.get("sample_index") or 0)


def _is_backup_candidate(candidate: str) -> bool:
    return any(part == BACKUP_EXCLUDE_PREFIX for part in Path(candidate).parts)


def _directllm_score(summary: dict[str, Any]) -> tuple[int, int, int]:
    return (
        int(summary.get("valid_scored") or 0),
        int(summary.get("task_records") or 0),
        int(summary.get("jsonl_records") or 0),
    )


def _extract_agent_events(bundle: RunBundle, record: dict[str, Any], *, harness: str) -> list[ActionEvent]:
    steps, source = _record_steps(bundle, record)
    events: list[ActionEvent] = []
    previous_observation_status = "none"
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        text = _step_text(step)
        if harness == "opencode" and _is_opencode_lifecycle_metadata(text):
            continue
        if _is_observation_step(step):
            previous_observation_status = observation_status_from_text(text)
            continue
        action, confidence, tool_subtype = classify_action_intent(
            step,
            previous_observation_status=previous_observation_status,
        )
        event = _make_event(
            bundle,
            record,
            harness=harness,
            step_id=str(step.get("step_id") or step.get("id") or index),
            source=source,
            canonical_action=action,
            action_label_confidence=confidence,
            tool_subtype=tool_subtype,
            observation_status="none",
            recovery_context=previous_observation_status in _RECOVERY_OBSERVATION_STATUSES,
            text_span=text,
        )
        events.append(event)
        previous_observation_status = "none"
    return events


def _extract_directllm_events(bundle: RunBundle, record: dict[str, Any], *, harness: str) -> list[ActionEvent]:
    events: list[ActionEvent] = []
    raw_output = _string_value(record.get("raw_output"))
    predicted = _string_value(record.get("predicted"))
    if raw_output:
        events.append(
            _make_event(
                bundle,
                record,
                harness=harness,
                step_id="raw_output",
                source="raw_text",
                canonical_action="reason",
                action_label_confidence="heuristic",
                tool_subtype="other",
                observation_status="none",
                recovery_context=False,
                text_span=raw_output,
            )
        )
    if predicted:
        events.append(
            _make_event(
                bundle,
                record,
                harness=harness,
                step_id="predicted",
                source="raw_text",
                canonical_action="answer",
                action_label_confidence="strict",
                tool_subtype="other",
                observation_status="none",
                recovery_context=False,
                text_span=predicted,
            )
        )
    return events


def _record_steps(bundle: RunBundle, record: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    trace = _load_normalized_trace(bundle, record)
    if trace:
        return trace, "normalized_trace"

    trajectory = record.get("trajectory")
    if isinstance(trajectory, list) and trajectory:
        return [step for step in trajectory if isinstance(step, dict)], "trajectory"

    reasoning = record.get("reasoning_trajectory")
    if isinstance(reasoning, list) and reasoning:
        return [step for step in reasoning if isinstance(step, dict)], "trajectory"

    raw_output = _string_value(record.get("raw_output"))
    predicted = _string_value(record.get("predicted"))
    steps = []
    if raw_output:
        steps.append({"type": "message", "content": raw_output})
    if predicted:
        steps.append({"type": "answer", "content": predicted})
    return steps, "raw_text"


def _load_normalized_trace(bundle: RunBundle, record: dict[str, Any]) -> list[dict[str, Any]]:
    rel_path = _normalized_trace_ref(record)
    if not rel_path:
        return []
    try:
        path = resolve_run_relative_path(bundle.results_dir, rel_path)
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(parsed, dict):
        return []
    steps = parsed.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def _normalized_trace_ref(record: dict[str, Any]) -> str:
    manifest = record.get("artifact_manifest")
    if not isinstance(manifest, dict):
        return ""
    files = manifest.get("files")
    if not isinstance(files, dict):
        return ""
    return _string_value(files.get("normalized_trace"))


def _make_event(
    bundle: RunBundle,
    record: dict[str, Any],
    *,
    harness: str,
    step_id: str,
    source: str,
    canonical_action: str,
    action_label_confidence: str,
    tool_subtype: str,
    observation_status: str,
    recovery_context: bool,
    text_span: str,
) -> ActionEvent:
    return ActionEvent(
        run_id=str(record.get("run_id") or bundle.run_id),
        harness=harness,
        task_id=str(record.get("task_id") or ""),
        sample_index=int(record.get("sample_index") or 0),
        step_id=step_id,
        canonical_action=canonical_action,
        action_label_confidence=action_label_confidence,
        source=source,
        tool_subtype=tool_subtype,
        observation_status=observation_status,
        recovery_context=recovery_context,
        text_span=text_span,
        score_status=infer_score_status(record),
        correct=record.get("correct") if isinstance(record.get("correct"), bool) else None,
    )


def _is_observation_step(step: dict[str, Any]) -> bool:
    step_type = str(step.get("type") or "").strip().lower()
    role = str(step.get("role") or "").strip().lower()
    return step_type in _TOOL_RESULT_TYPES or role in {"tool", "observation"}


def _is_opencode_lifecycle_metadata(text: str) -> bool:
    text_lower = text.lower()
    return any(marker in text_lower for marker in _OPENCODE_LIFECYCLE_MARKERS)


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


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
