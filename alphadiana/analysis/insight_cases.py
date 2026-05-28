"""Deterministic case anchors for Phase 15 insight claims."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from alphadiana.analysis.insight_corpus import sanitize_path_text
from alphadiana.analysis.trajectory_metrics import group_event_sequences, has_answer_after_verification
from alphadiana.results.status import VALID_SCORE_STATUS

CASE_ANCHOR_TYPES = (
    "paired_rescue",
    "paired_regression",
    "low_entropy_long_wrong",
    "verify_without_conversion",
    "tool_without_state_shift",
    "operational_error",
)

ERROR_SCORE_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}


def select_insight_case_anchors(
    *,
    event_rows: Sequence[Mapping[str, Any]],
    selected_records: Sequence[Mapping[str, Any]],
    measurement_summary: Mapping[str, Any] | None = None,
    per_type: int = 2,
) -> list[dict[str, Any]]:
    """Select reproducible sanitized anchors for insight mechanisms."""
    candidates: list[dict[str, Any]] = []
    sequences = group_event_sequences(event_rows)
    candidates.extend(_verify_without_conversion_cases(sequences))
    candidates.extend(_tool_without_state_shift_cases(sequences))
    candidates.extend(_low_entropy_long_cases(sequences, selected_records, measurement_summary))
    candidates.extend(_operational_error_cases(selected_records))
    candidates.extend(_paired_flip_cases(selected_records))

    selected: list[dict[str, Any]] = []
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_type[str(candidate.get("anchor_type") or "")].append(sanitize_case_anchor(candidate))

    for anchor_type in CASE_ANCHOR_TYPES:
        ranked = sorted(by_type.get(anchor_type, ()), key=_candidate_rank)
        selected.extend(ranked[: max(int(per_type), 0)])

    return sorted(
        selected,
        key=lambda case: (
            str(case.get("anchor_type") or ""),
            str(case.get("harness") or ""),
            str(case.get("task_id") or ""),
            int(case.get("sample_index") or 0),
        ),
    )


def case_anchor_id(case: Mapping[str, Any]) -> str:
    """Return the stable anchor identity for a selected case."""
    return (
        f"{case.get('anchor_type')}:{case.get('harness')}:{case.get('task_id')}:"
        f"{int(case.get('sample_index') or 0)}"
    )


def sanitize_case_anchor(case: Mapping[str, Any]) -> dict[str, Any]:
    """Sanitize snippets and fill the stable anchor ID."""
    sanitized = dict(case)
    snippets = sanitized.get("evidence_snippets")
    if snippets is None:
        snippets = _snippets_from_mapping(case)
    sanitized["evidence_snippets"] = [
        sanitize_path_text(str(snippet))[:240]
        for snippet in snippets
        if str(snippet).strip()
    ][:3]
    sanitized["anchor_id"] = case_anchor_id(sanitized)
    return sanitized


def _verify_without_conversion_cases(sequences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for sequence in sequences:
        if sequence.get("score_status") != VALID_SCORE_STATUS:
            continue
        actions = list(sequence.get("actions") or ())
        if "verify" not in actions or has_answer_after_verification(actions):
            continue
        verify_index = actions.index("verify")
        if any(action in {"reason", "plan", "tool_use", "recover"} for action in actions[verify_index + 1 :]):
            continue
        cases.append(_case_from_sequence("verify_without_conversion", sequence))
    return cases


def _tool_without_state_shift_cases(sequences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for sequence in sequences:
        if sequence.get("score_status") != VALID_SCORE_STATUS:
            continue
        actions = list(sequence.get("actions") or ())
        if "tool_use" not in actions:
            continue
        tool_index = actions.index("tool_use")
        if any(action in {"verify", "answer", "reason"} for action in actions[tool_index + 1 :]):
            continue
        cases.append(_case_from_sequence("tool_without_state_shift", sequence))
    return cases


def _low_entropy_long_cases(
    sequences: Sequence[Mapping[str, Any]],
    selected_records: Sequence[Mapping[str, Any]],
    measurement_summary: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    case_ids = _measurement_case_ids(measurement_summary)
    if not case_ids:
        return []
    by_key = {
        (str(sequence.get("harness") or ""), str(sequence.get("task_id") or ""), int(sequence.get("sample_index") or 0)): sequence
        for sequence in sequences
    }
    record_by_key = {
        (str(record.get("harness") or ""), str(record.get("task_id") or ""), int(record.get("sample_index") or 0)): record
        for record in selected_records
    }
    cases: list[dict[str, Any]] = []
    for raw_case_id in case_ids:
        parsed = _parse_measurement_case_id(raw_case_id)
        if parsed is None:
            continue
        harness, task_id, sample_index = parsed
        sequence = by_key.get((harness, task_id, sample_index))
        if sequence is not None:
            case = _case_from_sequence("low_entropy_long_wrong", sequence)
        else:
            record = record_by_key.get((harness, task_id, sample_index), {})
            case = _case_from_record("low_entropy_long_wrong", record, harness, task_id, sample_index)
        cases.append(case)
    return cases


def _operational_error_cases(selected_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for record in selected_records:
        status = str(record.get("score_status") or "")
        if status not in ERROR_SCORE_STATUSES:
            continue
        cases.append(
            _case_from_record(
                "operational_error",
                record,
                str(record.get("harness") or ""),
                str(record.get("task_id") or ""),
                int(record.get("sample_index") or 0),
            )
        )
    return cases


def _paired_flip_cases(selected_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    direct: dict[tuple[str, int], Mapping[str, Any]] = {}
    agents: list[Mapping[str, Any]] = []
    for record in selected_records:
        if str(record.get("score_status") or "") != VALID_SCORE_STATUS:
            continue
        key = (str(record.get("task_id") or ""), int(record.get("sample_index") or 0))
        if str(record.get("harness") or "").lower() == "directllm":
            direct[key] = record
        else:
            agents.append(record)

    cases: list[dict[str, Any]] = []
    for agent in agents:
        key = (str(agent.get("task_id") or ""), int(agent.get("sample_index") or 0))
        baseline = direct.get(key)
        if baseline is None:
            continue
        direct_correct = baseline.get("correct")
        agent_correct = agent.get("correct")
        if direct_correct is False and agent_correct is True:
            anchor_type = "paired_rescue"
        elif direct_correct is True and agent_correct is False:
            anchor_type = "paired_regression"
        else:
            continue
        case = _case_from_record(
            anchor_type,
            agent,
            str(agent.get("harness") or ""),
            str(agent.get("task_id") or ""),
            int(agent.get("sample_index") or 0),
        )
        case.update(
            {
                "directllm_correct": direct_correct,
                "agent_correct": agent_correct,
                "directllm_score_status": baseline.get("score_status"),
                "agent_score_status": agent.get("score_status"),
            }
        )
        cases.append(case)
    return cases


def _case_from_sequence(anchor_type: str, sequence: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(sequence.get("rows") or ())
    return {
        "anchor_type": anchor_type,
        "run_id": sequence.get("run_id"),
        "harness": sequence.get("harness"),
        "task_id": sequence.get("task_id"),
        "sample_index": int(sequence.get("sample_index") or 0),
        "score_status": sequence.get("score_status"),
        "correct": sequence.get("correct"),
        "actions": list(sequence.get("actions") or ()),
        "evidence_snippets": _event_snippets(rows),
    }


def _case_from_record(
    anchor_type: str,
    record: Mapping[str, Any],
    harness: str,
    task_id: str,
    sample_index: int,
) -> dict[str, Any]:
    return {
        "anchor_type": anchor_type,
        "run_id": record.get("run_id"),
        "harness": harness,
        "task_id": task_id,
        "sample_index": sample_index,
        "score_status": record.get("score_status"),
        "correct": record.get("correct"),
        "actions": [],
        "evidence_snippets": _snippets_from_mapping(record),
    }


def _event_snippets(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    snippets: list[str] = []
    for row in rows:
        text = " ".join(str(row.get("text_span") or "").split())
        if text:
            snippets.append(text)
        if len(snippets) >= 3:
            break
    return snippets


def _snippets_from_mapping(row: Mapping[str, Any]) -> list[str]:
    snippets: list[str] = []
    for key in ("text_span", "error", "predicted", "answer", "raw_output"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            snippets.append(" ".join(value.split()))
    return snippets


def _measurement_case_ids(measurement_summary: Mapping[str, Any] | None) -> list[str]:
    if not measurement_summary:
        return []
    candidates = measurement_summary.get("low_entropy_long_collapse")
    if isinstance(candidates, Mapping):
        raw = candidates.get("case_ids") or candidates.get("cases")
    else:
        raw = measurement_summary.get("low_entropy_long_case_ids")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [str(item) for item in raw]


def _parse_measurement_case_id(value: str) -> tuple[str, str, int] | None:
    parts = value.split(":")
    if len(parts) != 3:
        return None
    harness, task_id, sample_index = parts
    try:
        return harness, task_id, int(sample_index)
    except ValueError:
        return None


def _candidate_rank(case: Mapping[str, Any]) -> tuple[int, str, str, int]:
    is_valid = str(case.get("score_status") or "") == VALID_SCORE_STATUS
    is_operational = str(case.get("anchor_type") or "") == "operational_error"
    return (
        0 if is_valid or is_operational else 1,
        str(case.get("harness") or ""),
        str(case.get("task_id") or ""),
        int(case.get("sample_index") or 0),
    )
