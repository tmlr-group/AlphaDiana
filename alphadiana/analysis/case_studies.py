"""Representative Phase 14 trajectory case-study selection."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

from alphadiana.analysis.trajectory_metrics import (
    group_event_sequences,
    has_answer_after_verification,
    has_budget_collapse,
    has_error_recovery,
    has_premature_answer,
    has_tool_grounded_reasoning,
)
from alphadiana.analysis.io.status import VALID_SCORE_STATUS

CASE_STUDY_MOTIFS = (
    "verification_circuit",
    "recovery_circuit",
    "premature_answer",
    "tool_grounded_reasoning",
    "budget_collapse",
)

_ABSOLUTE_PATH_RE = re.compile(r"(?<!\w)/(?:data\d*|home|tmp|var|mnt|scratch)/[^\s,;:)\]]+")
_SECRET_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{8,}|(?:api[_-]?key|token|secret)\s*[:=]\s*[a-z0-9_.-]{8,})"
)


def summarize_event_sequence(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize one grouped event sequence as a reviewer-facing case anchor."""
    if not events:
        raise ValueError("cannot summarize an empty event sequence")

    sequence = group_event_sequences(events)[0]
    rows = sequence["rows"]
    actions = list(sequence["actions"])
    statuses = list(sequence["observation_statuses"])
    score_status = str(sequence.get("score_status") or "")

    return {
        "run_id": sequence["run_id"],
        "harness": sequence["harness"],
        "task_id": sequence["task_id"],
        "sample_index": sequence["sample_index"],
        "correct": sequence["correct"],
        "score_status": score_status,
        "actions": actions,
        "motifs": _motif_labels(actions, statuses, score_status),
        "evidence_snippets": _evidence_snippets(rows),
        "strict_label_count": sum(
            1 for row in rows if str(row.get("action_label_confidence") or "") == "strict"
        ),
    }


def select_case_studies(event_rows: Sequence[Mapping[str, Any]], *, per_harness: int = 2) -> list[dict[str, Any]]:
    """Select deterministic correct/incorrect micro cases from event rows."""
    grouped: dict[tuple[str, str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in event_rows:
        run_id = str(row.get("run_id") or "")
        harness = str(row.get("harness") or "")
        task_id = str(row.get("task_id") or "")
        sample_index = int(row.get("sample_index") or 0)
        grouped[(run_id, harness, task_id, sample_index)].append(row)

    summaries = [summarize_event_sequence(rows) for rows in grouped.values()]
    by_harness: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in summaries:
        by_harness[str(summary["harness"])].append(summary)

    selected: list[dict[str, Any]] = []
    for harness in sorted(by_harness):
        candidates = sorted(by_harness[harness], key=_selection_rank)
        chosen: list[dict[str, Any]] = []
        for desired_correct in (True, False):
            match = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate["correct"] is desired_correct and candidate not in chosen
                ),
                None,
            )
            if match is not None and len(chosen) < per_harness:
                chosen.append(match)

        for candidate in candidates:
            if len(chosen) >= per_harness:
                break
            if candidate not in chosen:
                chosen.append(candidate)

        selected.extend(chosen)

    return selected


def _motif_labels(actions: Sequence[str], statuses: Sequence[str], score_status: str) -> list[str]:
    labels: list[str] = []
    if has_answer_after_verification(actions):
        labels.append("verification_circuit")
    if has_error_recovery(actions, statuses):
        labels.append("recovery_circuit")
    if has_premature_answer(actions):
        labels.append("premature_answer")
    if has_tool_grounded_reasoning(actions):
        labels.append("tool_grounded_reasoning")
    if has_budget_collapse(actions, score_status):
        labels.append("budget_collapse")
    return labels


def _evidence_snippets(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    snippets: list[str] = []
    for row in rows:
        text = str(row.get("text_span") or "")
        normalized = " ".join(text.split())
        if not normalized:
            continue
        sanitized = _sanitize_snippet(normalized)
        snippets.append(sanitized[:240])
        if len(snippets) >= 3:
            break
    return snippets


def _sanitize_snippet(text: str) -> str:
    without_paths = _ABSOLUTE_PATH_RE.sub("[path]", text)
    return _SECRET_RE.sub("[secret]", without_paths)


def _selection_rank(case: Mapping[str, Any]) -> tuple[int, int, int, str, int]:
    has_motif = bool(case.get("motifs"))
    is_valid = case.get("score_status") == VALID_SCORE_STATUS
    strict_count = int(case.get("strict_label_count") or 0)
    return (
        0 if is_valid else 1,
        0 if has_motif else 1,
        -strict_count,
        str(case.get("task_id") or ""),
        int(case.get("sample_index") or 0),
    )
