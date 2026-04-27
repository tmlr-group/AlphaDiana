"""Outcome-conditioned behavioral metrics for Phase 14 trajectory events."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from alphadiana.results.status import VALID_SCORE_STATUS

MAIN_METRIC_NAMES = (
    "DeltaVerifyShare",
    "DeltaToolUseShare",
    "AnswerAfterVerificationRate",
    "ErrorRecoveryRate",
    "PrematureAnswerRate",
    "MotifOutcomeLift",
    "FailureCostRatio",
)
ERROR_STATUSES = ("agent_error", "provider_error", "runtime_error", "scorer_error")

_RECOVERY_STATUSES = {"fail", "error", "timeout", "truncated"}
_ACTION_ORDER = ("plan", "reason", "tool_use", "verify", "recover", "answer")


def trajectory_key(row: Mapping[str, Any]) -> tuple[str, int, str]:
    """Return the task/sample/harness identity for a trajectory event row."""
    return (
        str(row.get("task_id") or ""),
        int(row.get("sample_index") or 0),
        str(row.get("harness") or ""),
    )


def group_event_sequences(event_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group flat event rows into ordered task/sample action sequences."""
    grouped: dict[tuple[str, str, int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in event_rows:
        run_id = str(row.get("run_id") or "")
        harness = str(row.get("harness") or "")
        task_id = str(row.get("task_id") or "")
        sample_index = int(row.get("sample_index") or 0)
        grouped[(run_id, harness, sample_index, task_id)].append(row)

    sequences: list[dict[str, Any]] = []
    for (run_id, harness, sample_index, task_id), rows in grouped.items():
        ordered = sorted(rows, key=_step_sort_key)
        first = ordered[0]
        sequences.append(
            {
                "run_id": run_id,
                "harness": harness,
                "task_id": task_id,
                "sample_index": sample_index,
                "actions": [str(row.get("canonical_action") or "") for row in ordered],
                "tool_subtypes": [str(row.get("tool_subtype") or "other") for row in ordered],
                "observation_statuses": [str(row.get("observation_status") or "none") for row in ordered],
                "correct": _coerce_correct(first.get("correct")),
                "score_status": str(first.get("score_status") or ""),
                "rows": [dict(row) for row in ordered],
            }
        )
    return sorted(sequences, key=lambda item: (item["harness"], item["task_id"], item["sample_index"], item["run_id"]))


def has_answer_after_verification(actions: Sequence[str]) -> bool:
    """Return whether an answer occurs after an explicit verification action."""
    seen_verify = False
    for action in actions:
        if action == "verify":
            seen_verify = True
        elif action == "answer" and seen_verify:
            return True
    return False


def has_error_recovery(actions: Sequence[str], observation_statuses: Sequence[str]) -> bool:
    """Return whether a recovery action follows a failed/error observation."""
    seen_error = False
    for action, status in zip(actions, observation_statuses):
        if status in _RECOVERY_STATUSES:
            seen_error = True
        if action == "recover" and seen_error:
            return True
    return False


def has_premature_answer(actions: Sequence[str]) -> bool:
    """Return whether the first answer arrives without prior verification."""
    seen_verify = False
    for action in actions:
        if action == "verify":
            seen_verify = True
        elif action == "answer":
            return not seen_verify
    return False


def has_tool_grounded_reasoning(actions: Sequence[str]) -> bool:
    """Return whether reasoning/verification/answering follows tool use."""
    seen_tool = False
    for action in actions:
        if action == "tool_use":
            seen_tool = True
        elif seen_tool and action in {"reason", "verify", "answer"}:
            return True
    return False


def has_budget_collapse(actions: Sequence[str], score_status: str) -> bool:
    """Return whether a sequence ended in an operational error without an answer."""
    return score_status in ERROR_STATUSES and "answer" not in actions


def compute_outcome_conditioned_metrics(
    event_rows: Sequence[Mapping[str, Any]],
    inventory_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute Phase 14 headline behavior metrics from event rows."""
    sequences = group_event_sequences(event_rows)
    valid_sequences = [
        sequence
        for sequence in sequences
        if sequence["score_status"] == VALID_SCORE_STATUS and sequence["correct"] in {True, False}
    ]

    return {
        "metric_names": MAIN_METRIC_NAMES,
        "action_allocation": _action_allocation(valid_sequences),
        "motif_metrics": _motif_metrics(valid_sequences),
        "motif_outcome_lift": _motif_outcome_lift(valid_sequences),
        "failure_cost": _failure_cost(sequences, inventory_rows),
        "diagnostics": {
            "pooled_action_distribution": _pooled_action_distribution(sequences),
            "sequence_count": len(sequences),
            "valid_scored_sequence_count": len(valid_sequences),
        },
    }


def _action_allocation(sequences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_harness: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for sequence in sequences:
        by_harness[str(sequence["harness"])].append(sequence)

    rows: list[dict[str, Any]] = []
    for harness in sorted(by_harness):
        harness_sequences = by_harness[harness]
        correct_actions = Counter(
            action
            for sequence in harness_sequences
            if sequence["correct"] is True
            for action in sequence["actions"]
        )
        incorrect_actions = Counter(
            action
            for sequence in harness_sequences
            if sequence["correct"] is False
            for action in sequence["actions"]
        )
        correct_total = sum(correct_actions.values())
        incorrect_total = sum(incorrect_actions.values())
        for action in _ACTION_ORDER:
            correct_share = _safe_div(correct_actions[action], correct_total)
            incorrect_share = _safe_div(incorrect_actions[action], incorrect_total)
            rows.append(
                {
                    "harness": harness,
                    "canonical_action": action,
                    "correct_n": correct_actions[action],
                    "correct_share": correct_share,
                    "incorrect_n": incorrect_actions[action],
                    "incorrect_share": incorrect_share,
                    "delta_correct_minus_incorrect": correct_share - incorrect_share,
                }
            )
    return rows


def _motif_metrics(sequences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric_name, motif_name, _ in _resolved_motif_specs():
        correct_flags = [_motif_flag(sequence, motif_name) for sequence in sequences if sequence["correct"] is True]
        incorrect_flags = [_motif_flag(sequence, motif_name) for sequence in sequences if sequence["correct"] is False]
        correct_rate = _rate(correct_flags)
        incorrect_rate = _rate(incorrect_flags)
        rows.append(
            {
                "metric_name": metric_name,
                "motif": motif_name,
                "correct": correct_rate,
                "incorrect": incorrect_rate,
                "delta_correct_minus_incorrect": correct_rate - incorrect_rate,
                "correct_n": len(correct_flags),
                "incorrect_n": len(incorrect_flags),
            }
        )
    return rows


def _motif_outcome_lift(sequences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _metric_name, motif_name, _ in _resolved_motif_specs():
        with_motif = [sequence for sequence in sequences if _motif_flag(sequence, motif_name)]
        without_motif = [sequence for sequence in sequences if not _motif_flag(sequence, motif_name)]
        p_with = _correct_rate(with_motif)
        p_without = _correct_rate(without_motif)
        rows.append(
            {
                "metric_name": "MotifOutcomeLift",
                "motif": motif_name,
                "p_correct_with_motif": p_with,
                "p_correct_without_motif": p_without,
                "lift": p_with - p_without,
                "with_motif_n": len(with_motif),
                "without_motif_n": len(without_motif),
            }
        )
    return rows


def _failure_cost(
    sequences: Sequence[Mapping[str, Any]],
    inventory_rows: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    by_harness: dict[str, dict[str, Any]] = defaultdict(lambda: {"valid_scored": 0, "status_counts": Counter()})
    for sequence in sequences:
        harness = str(sequence["harness"])
        status = str(sequence.get("score_status") or "")
        if status == VALID_SCORE_STATUS:
            by_harness[harness]["valid_scored"] += 1
        elif status in ERROR_STATUSES:
            by_harness[harness]["status_counts"][status] += 1

    for row in inventory_rows or ():
        harness = str(row.get("harness") or "")
        if not harness:
            continue
        status = str(row.get("score_status") or "")
        if status in ERROR_STATUSES:
            by_harness[harness]["status_counts"][status] += 1
        elif status == VALID_SCORE_STATUS and not sequences:
            by_harness[harness]["valid_scored"] += 1

    rows: list[dict[str, Any]] = []
    for harness in sorted(by_harness):
        valid_scored = by_harness[harness]["valid_scored"]
        status_counts = by_harness[harness]["status_counts"]
        error_records = sum(status_counts.values())
        row = {
            "harness": harness,
            "valid_scored": valid_scored,
            "error_records": error_records,
            "failure_cost_ratio": _safe_div(error_records, valid_scored),
        }
        row.update({status: status_counts[status] for status in ERROR_STATUSES})
        rows.append(row)
    return rows


def _pooled_action_distribution(sequences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(action for sequence in sequences for action in sequence["actions"])
    total = sum(counts.values())
    return [
        {"canonical_action": action, "n": counts[action], "share": _safe_div(counts[action], total)}
        for action in _ACTION_ORDER
    ]


def _motif_flag(sequence: Mapping[str, Any], motif_name: str) -> bool:
    actions = sequence["actions"]
    statuses = sequence["observation_statuses"]
    score_status = str(sequence.get("score_status") or "")
    if motif_name == "answer_after_verification":
        return has_answer_after_verification(actions)
    if motif_name == "error_recovery":
        return has_error_recovery(actions, statuses)
    if motif_name == "premature_answer":
        return has_premature_answer(actions)
    if motif_name == "tool_grounded_reasoning":
        return has_tool_grounded_reasoning(actions)
    if motif_name == "budget_collapse":
        return has_budget_collapse(actions, score_status)
    raise KeyError(f"unknown motif: {motif_name}")


def _resolved_motif_specs() -> tuple[tuple[str, str, Any], ...]:
    return (
        ("AnswerAfterVerificationRate", "answer_after_verification", has_answer_after_verification),
        ("ErrorRecoveryRate", "error_recovery", has_error_recovery),
        ("PrematureAnswerRate", "premature_answer", has_premature_answer),
        ("tool_grounded_reasoning", "tool_grounded_reasoning", has_tool_grounded_reasoning),
        ("budget_collapse", "budget_collapse", has_budget_collapse),
    )


def _step_sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    raw_step = row.get("step_id")
    try:
        return int(raw_step), str(raw_step)
    except (TypeError, ValueError):
        return 10**9, str(raw_step or "")


def _coerce_correct(value: Any) -> bool | None:
    if value is True or value is False:
        return value
    return None


def _rate(flags: Sequence[bool]) -> float:
    return _safe_div(sum(1 for flag in flags if flag), len(flags))


def _correct_rate(sequences: Sequence[Mapping[str, Any]]) -> float:
    return _safe_div(sum(1 for sequence in sequences if sequence["correct"] is True), len(sequences))


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0
