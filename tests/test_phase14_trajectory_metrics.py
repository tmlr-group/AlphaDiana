from __future__ import annotations

import pytest

from alphadiana.analysis.case_studies import select_case_studies, summarize_event_sequence
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
        "LowEntropyLongCollapseRate",
        "ConfidenceInversion",
        "PostToolEntropySeparation",
        "VerificationConversionRate",
        "OperationalTaxAdjustedAccuracy",
        "PairedNetGain",
        "ScaffoldDominance",
    )
    assert metrics["metric_names"] == MAIN_METRIC_NAMES
    assert metrics["measurement_insights"]["available"] is False
    assert "pass_power_k" not in metrics


def test_outcome_conditioned_metrics_fold_in_analyze_tools_measurements():
    metrics = compute_outcome_conditioned_metrics(
        [],
        measurement_summary={
            "entropy_token_quadrants": [
                {
                    "bucket": "low_entropy_long",
                    "n": 4,
                    "wrong_rate": 0.75,
                    "median_tokens": 9000,
                    "median_entropy": 0.05,
                    "token_threshold_q75": 5000,
                    "entropy_threshold_q25": 0.1,
                }
            ],
            "confidence_inversion": [
                {
                    "entropy_threshold": 0.2,
                    "low_entropy_n": 5,
                    "high_entropy_n": 10,
                    "wrong_rate_low_entropy": 0.8,
                    "wrong_rate_high_entropy": 0.3,
                    "inversion_lift": 0.5,
                },
                {
                    "entropy_threshold": 0.1,
                    "low_entropy_n": 3,
                    "high_entropy_n": 12,
                    "wrong_rate_low_entropy": 1.0,
                    "wrong_rate_high_entropy": 0.2,
                    "inversion_lift": 0.8,
                },
            ],
            "posttool_state_shift": [
                {
                    "turn_label": "baseline",
                    "wrong_minus_correct_entropy": -0.01,
                    "separation_gain_vs_baseline": 0.0,
                },
                {
                    "turn_label": "after_tool_1+",
                    "wrong_minus_correct_entropy": 0.1,
                    "separation_gain_vs_baseline": 0.11,
                    "correct_n": 7,
                    "wrong_n": 2,
                },
                {
                    "turn_label": "boundary_shock_integral_0_15",
                    "wrong_minus_correct_entropy": 3.5,
                },
            ],
            "verification_conversion": [
                {
                    "harness": "openclaw",
                    "correct": "False",
                    "n": 6,
                    "verify_rate": 0.8,
                    "verify_before_answer_rate": 0.1,
                    "post_verify_action_change_rate": 0.2,
                },
                {
                    "harness": "openclaw",
                    "correct": "True",
                    "n": 8,
                    "verify_rate": 0.7,
                    "verify_before_answer_rate": 0.4,
                    "post_verify_action_change_rate": 0.5,
                },
            ],
            "operational_tax_adjusted_accuracy": [{"harness": "openclaw", "deployable_accuracy": 0.55}],
            "paired_net_gain": [{"harness": "openclaw", "paired_net_gain": -2}],
            "action_space_distance": [
                {"harness_a": "openclaw", "harness_b": "zeroclaw", "canonical_action_jsd": 0.51}
            ],
        },
    )

    insights = metrics["measurement_insights"]

    assert insights["available"] is True
    assert insights["low_entropy_long_collapse"]["wrong_rate"] == pytest.approx(0.75)
    assert insights["confidence_inversion"]["entropy_threshold"] == pytest.approx(0.1)
    assert insights["posttool_entropy_separation"]["strongest_turn"]["turn_label"] == "after_tool_1+"
    assert insights["posttool_entropy_separation"]["boundary_shock_integral_0_15"] == pytest.approx(3.5)
    assert insights["verification_conversion"]["lifts"][0]["verify_before_answer_lift"] == pytest.approx(0.3)
    assert insights["operational_tax_adjusted_accuracy"][0]["harness"] == "openclaw"
    assert insights["paired_net_gain"][0]["paired_net_gain"] == -2
    assert insights["scaffold_dominance"][0]["canonical_action_jsd"] == pytest.approx(0.51)


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


def test_select_case_studies_prefers_correct_and_incorrect_motif_examples():
    rows = [
        _event(
            "openclaw",
            "verify_recover_correct",
            0,
            "tool_use",
            correct=True,
            tool_subtype="python",
            text_span="python solve.py",
            action_label_confidence="strict",
            run_id="openclaw-run",
        ),
        _event(
            "openclaw",
            "verify_recover_correct",
            1,
            "verify",
            correct=True,
            observation_status="error",
            text_span="Traceback while checking intermediate result",
            run_id="openclaw-run",
        ),
        _event(
            "openclaw",
            "verify_recover_correct",
            2,
            "recover",
            correct=True,
            text_span="Retry with the corrected unit conversion",
            run_id="openclaw-run",
        ),
        _event(
            "openclaw",
            "verify_recover_correct",
            3,
            "answer",
            correct=True,
            text_span="Final answer: B",
            run_id="openclaw-run",
        ),
        _event(
            "opencode",
            "premature_incorrect",
            0,
            "answer",
            correct=False,
            text_span="Final answer: /data2/xuan/AlphaDiana/secret-looking-path with sk-test-1234567890abcdef",
            run_id="opencode-run",
            action_label_confidence="strict",
        ),
        _event(
            "zeroclaw",
            "budget_error",
            0,
            "reason",
            correct=None,
            score_status="runtime_error",
            text_span="continued reasoning until timeout",
            run_id="zeroclaw-run",
        ),
        _event(
            "directllm",
            "degenerate_answer",
            0,
            "reason",
            correct=False,
            text_span="The direct model compares choices quickly.",
            run_id="direct-run",
        ),
        _event(
            "directllm",
            "degenerate_answer",
            1,
            "answer",
            correct=False,
            text_span="Final answer: D",
            run_id="direct-run",
        ),
    ]

    cases = select_case_studies(rows, per_harness=2)
    by_task = {case["task_id"]: case for case in cases}

    assert by_task["verify_recover_correct"]["correct"] is True
    assert by_task["premature_incorrect"]["correct"] is False
    assert by_task["budget_error"]["score_status"] == "runtime_error"
    assert "verification_circuit" in by_task["verify_recover_correct"]["motifs"]
    assert "recovery_circuit" in by_task["verify_recover_correct"]["motifs"]
    assert "tool_grounded_reasoning" in by_task["verify_recover_correct"]["motifs"]
    assert "premature_answer" in by_task["premature_incorrect"]["motifs"]
    assert "budget_collapse" in by_task["budget_error"]["motifs"]
    assert all(len(snippet) <= 240 for case in cases for snippet in case["evidence_snippets"])
    assert all("/data2/" not in snippet for case in cases for snippet in case["evidence_snippets"])
    assert all("sk-test" not in snippet for case in cases for snippet in case["evidence_snippets"])


def test_summarize_event_sequence_returns_required_case_shape():
    summary = summarize_event_sequence(
        [
            _event("openclaw", "gpqa_1", 0, "verify", correct=True, run_id="run-a"),
            _event("openclaw", "gpqa_1", 1, "answer", correct=True, run_id="run-a"),
        ]
    )

    assert set(summary) >= {
        "run_id",
        "harness",
        "task_id",
        "sample_index",
        "correct",
        "score_status",
        "actions",
        "motifs",
        "evidence_snippets",
    }
    assert summary["motifs"] == ["verification_circuit"]
