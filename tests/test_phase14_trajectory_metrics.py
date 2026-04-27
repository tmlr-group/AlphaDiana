from __future__ import annotations

import pytest

from alphadiana.analysis.trajectory_metrics import (
    MAIN_METRIC_NAMES,
    compute_outcome_conditioned_metrics,
)


def _event(
    harness: str,
    task_id: str,
    step_id: int,
    action: str,
    *,
    correct: bool | None,
    score_status: str = "valid_scored",
    tool_subtype: str = "other",
    observation_status: str = "none",
    text_span: str | None = None,
    run_id: str = "run-a",
    sample_index: int = 0,
    action_label_confidence: str = "heuristic",
):
    return {
        "run_id": run_id,
        "harness": harness,
        "task_id": task_id,
        "sample_index": sample_index,
        "step_id": str(step_id),
        "canonical_action": action,
        "action_label_confidence": action_label_confidence,
        "source": "trajectory",
        "tool_subtype": tool_subtype,
        "observation_status": observation_status,
        "recovery_context": observation_status in {"fail", "error", "timeout", "truncated"},
        "text_span": text_span if text_span is not None else f"{action} text",
        "score_status": score_status,
        "correct": correct,
    }


def test_outcome_conditioned_metrics_include_required_metric_names():
    metrics = compute_outcome_conditioned_metrics([])

    assert MAIN_METRIC_NAMES == (
        "DeltaVerifyShare",
        "DeltaToolUseShare",
        "AnswerAfterVerificationRate",
        "ErrorRecoveryRate",
        "PrematureAnswerRate",
        "MotifOutcomeLift",
        "FailureCostRatio",
    )
    assert metrics["metric_names"] == MAIN_METRIC_NAMES
    assert "pass_power_k" not in metrics


def test_outcome_conditioned_action_allocation_uses_valid_scored_outcomes():
    rows = [
        _event("openclaw", "correct", 0, "tool_use", correct=True, tool_subtype="python"),
        _event("openclaw", "correct", 1, "verify", correct=True),
        _event("openclaw", "correct", 2, "answer", correct=True),
        _event("openclaw", "incorrect", 0, "reason", correct=False),
        _event("openclaw", "incorrect", 1, "answer", correct=False),
        _event("openclaw", "error", 0, "tool_use", correct=None, score_status="runtime_error"),
    ]

    metrics = compute_outcome_conditioned_metrics(rows)
    allocation_by_action = {
        row["canonical_action"]: row
        for row in metrics["action_allocation"]
        if row["harness"] == "openclaw"
    }

    assert allocation_by_action["verify"]["correct_n"] == 1
    assert allocation_by_action["verify"]["incorrect_n"] == 0
    assert allocation_by_action["verify"]["correct_share"] == pytest.approx(1 / 3)
    assert allocation_by_action["verify"]["incorrect_share"] == pytest.approx(0.0)
    assert allocation_by_action["verify"]["delta_correct_minus_incorrect"] == pytest.approx(1 / 3)
    assert allocation_by_action["tool_use"]["correct_n"] == 1
    assert allocation_by_action["tool_use"]["incorrect_n"] == 0
    assert "pooled_action_distribution" in metrics["diagnostics"]


def test_motif_metrics_and_failure_cost_separate_error_records():
    rows = [
        _event("openclaw", "correct", 0, "verify", correct=True),
        _event("openclaw", "correct", 1, "answer", correct=True),
        _event("openclaw", "incorrect", 0, "answer", correct=False),
        _event("openclaw", "runtime", 0, "reason", correct=None, score_status="runtime_error"),
    ]
    inventory_rows = [
        {"harness": "openclaw", "task_id": "agent", "sample_index": 0, "score_status": "agent_error"},
        {"harness": "openclaw", "task_id": "provider", "sample_index": 0, "score_status": "provider_error"},
        {"harness": "openclaw", "task_id": "scorer", "sample_index": 0, "score_status": "scorer_error"},
    ]

    metrics = compute_outcome_conditioned_metrics(rows, inventory_rows=inventory_rows)
    motif_by_name = {row["metric_name"]: row for row in metrics["motif_metrics"]}
    failure_cost = metrics["failure_cost"][0]

    assert motif_by_name["AnswerAfterVerificationRate"]["correct"] == pytest.approx(1.0)
    assert motif_by_name["PrematureAnswerRate"]["incorrect"] == pytest.approx(1.0)
    assert any(row["motif"] == "answer_after_verification" for row in metrics["motif_outcome_lift"])
    assert failure_cost["valid_scored"] == 2
    assert failure_cost["error_records"] == 4
    assert failure_cost["failure_cost_ratio"] == pytest.approx(2.0)
    assert failure_cost["agent_error"] == 1
    assert failure_cost["provider_error"] == 1
    assert failure_cost["runtime_error"] == 1
    assert failure_cost["scorer_error"] == 1
