from __future__ import annotations

from alphadiana.analysis.insight_measurements import (
    INSIGHT_MECHANISMS,
    InsightClaim,
    build_insight_claims,
    claim_low_entropy_long_collapse,
    claim_model_harness_composite,
)
from alphadiana.analysis.insight_cases import (
    CASE_ANCHOR_TYPES,
    case_anchor_id,
    select_insight_case_anchors,
)


def _denominator_rows() -> list[dict]:
    return [
        {
            "corpus_label": "gpqa-openclaw-v2",
            "benchmark": "gpqa",
            "harness": "openclaw",
            "status": "validated_records",
            "expected_samples": 198,
            "valid_scored": 162,
            "selected_records": 198,
        },
        {
            "corpus_label": "gpqa-opencode-v2",
            "benchmark": "gpqa",
            "harness": "opencode",
            "status": "validated_records",
            "expected_samples": 198,
            "valid_scored": 197,
            "selected_records": 198,
        },
        {
            "corpus_label": "hle-opencode-env",
            "benchmark": "hle",
            "harness": "opencode",
            "status": "manifest_only",
            "expected_samples": 100,
            "valid_scored": 0,
            "selected_records": 0,
        },
    ]


def _metrics() -> dict:
    return {
        "measurement_insights": {
            "available": True,
            "low_entropy_long_collapse": {
                "bucket": "low_entropy_long",
                "n": 32,
                "wrong_rate": 1.0,
                "median_tokens": 41742,
                "median_entropy": 0.031,
                "token_threshold_q75": 5348,
                "entropy_threshold_q25": 0.1465,
            },
            "posttool_entropy_separation": {
                "strongest_turn": {
                    "turn_label": "after_tool_3+",
                    "wrong_minus_correct_entropy": 0.272,
                    "separation_gain_vs_baseline": 0.286,
                    "correct_n": 81,
                    "wrong_n": 19,
                },
                "boundary_shock_integral_0_15": 3.319,
            },
            "verification_conversion": {
                "lifts": [
                    {
                        "harness": "opencode",
                        "verify_rate_lift": 0.089,
                        "verify_before_answer_lift": 0.137,
                        "post_verify_action_change_lift": 0.223,
                        "correct_n": 142,
                        "wrong_n": 51,
                    }
                ]
            },
            "operational_tax_adjusted_accuracy": [
                {
                    "harness": "directllm",
                    "behavioral_accuracy": 0.803,
                    "operational_tax": 0.0,
                    "deployable_accuracy": 0.803,
                },
                {
                    "harness": "zeroclaw",
                    "behavioral_accuracy": 0.807,
                    "operational_tax": 0.187,
                    "deployable_accuracy": 0.657,
                },
            ],
            "paired_net_gain": [
                {
                    "harness": "zeroclaw",
                    "paired_valid": 161,
                    "rescue": 13,
                    "regression": 11,
                    "net_gain": 2,
                }
            ],
            "scaffold_dominance": [
                {
                    "pair": "DirectLLM vs OpenClaw",
                    "jsd": 0.398,
                    "support_overlap": 0.333,
                }
            ],
        }
    }


def test_claims_include_old_story_falsifier_and_failure_to_establish():
    claims = build_insight_claims(denominator_rows=_denominator_rows(), metrics=_metrics())

    assert INSIGHT_MECHANISMS == (
        "Model-Harness Composite",
        "Scaffold Dominance",
        "State-Update Yield",
        "Verification Conversion Gap",
        "Low-Entropy Long Collapse",
        "Operational Tax",
        "Paired Rescue Regression",
    )
    assert len(claims) == len(INSIGHT_MECHANISMS)
    assert {claim["mechanism_name"] for claim in claims} == set(INSIGHT_MECHANISMS)
    for claim in claims:
        assert claim["old_story"]
        assert claim["falsifier_question"]
        assert claim["measurement"]
        assert claim["denominator_filter"]
        assert claim["result"] is not None
        assert "failure_to_establish" in claim

    unsupported = claim_low_entropy_long_collapse(
        denominator_rows=[_denominator_rows()[-1]],
        metrics=_metrics(),
        case_anchor_ids=(),
    )
    assert isinstance(unsupported, InsightClaim)
    assert unsupported.failure_to_establish
    assert "validated_records" in unsupported.failure_to_establish


def test_verification_claim_uses_conversion_not_raw_verify_rate():
    claims = build_insight_claims(denominator_rows=_denominator_rows(), metrics=_metrics())
    verification = next(claim for claim in claims if claim["mechanism_name"] == "Verification Conversion Gap")

    assert verification["measurement"] == "VerificationConversionRate"
    assert verification["result"]["post_verify_action_change_lift"] == 0.223
    assert verification["result"]["verify_before_answer_lift"] == 0.137
    assert "verify_rate_lift" not in verification["result"]
    assert "raw verify rate" in verification["failure_to_establish"]


def test_raw_accuracy_is_not_model_capability_claim_without_tax_and_scaffold_fields():
    incomplete_metrics = {"raw_accuracy": {"openclaw": 0.67, "zeroclaw": 0.81}}
    claim = claim_model_harness_composite(
        denominator_rows=_denominator_rows(),
        metrics=incomplete_metrics,
        case_anchor_ids=(),
    )

    assert claim.failure_to_establish
    assert "action-space" in claim.failure_to_establish
    assert "base-model capability" not in claim.interpretation.lower()

    claims = build_insight_claims(denominator_rows=_denominator_rows(), metrics=_metrics())
    composite = next(claim for claim in claims if claim["mechanism_name"] == "Model-Harness Composite")
    operational = next(claim for claim in claims if claim["mechanism_name"] == "Operational Tax")

    assert composite["result"]["action_space_distance"][0]["pair"] == "DirectLLM vs OpenClaw"
    assert "raw_accuracy" not in composite["result"]
    assert {"behavioral_accuracy", "operational_tax", "deployable_accuracy"} <= set(
        operational["result"]["by_harness"][0]
    )


def _event(
    task_id: str,
    action: str,
    *,
    harness: str = "opencode",
    step_id: str = "0",
    correct: bool | None = False,
    score_status: str = "valid_scored",
    text_span: str = "ordinary evidence",
) -> dict:
    return {
        "run_id": f"run-{harness}",
        "harness": harness,
        "task_id": task_id,
        "sample_index": 0,
        "step_id": step_id,
        "canonical_action": action,
        "action_label_confidence": "strict",
        "source": "trajectory",
        "tool_subtype": "search" if action == "tool_use" else "other",
        "observation_status": "none",
        "recovery_context": False,
        "text_span": text_span,
        "score_status": score_status,
        "correct": correct,
    }


def _selected_records() -> list[dict]:
    return [
        {"harness": "directllm", "task_id": "task_rescue", "sample_index": 0, "score_status": "valid_scored", "correct": False},
        {"harness": "opencode", "task_id": "task_rescue", "sample_index": 0, "score_status": "valid_scored", "correct": True},
        {"harness": "directllm", "task_id": "task_regress", "sample_index": 0, "score_status": "valid_scored", "correct": True},
        {"harness": "opencode", "task_id": "task_regress", "sample_index": 0, "score_status": "valid_scored", "correct": False},
        {
            "harness": "openclaw",
            "task_id": "task_error",
            "sample_index": 0,
            "score_status": "runtime_error",
            "correct": None,
            "error": "token sk-private-secret at /data2/private/run",
        },
    ]


def test_case_anchors_are_deterministic_and_sanitized():
    long_secret_text = "verify token sk-private-secret at /data2/private/run " + ("x" * 400)
    event_rows = [
        _event("task_tool", "tool_use", text_span="/home/user/work key sk-private-secret"),
        _event("task_verify", "verify", text_span=long_secret_text),
        _event("task_other", "answer", text_span="not selected"),
    ]

    anchors = select_insight_case_anchors(
        event_rows=list(reversed(event_rows)),
        selected_records=_selected_records(),
        measurement_summary={"low_entropy_long_collapse": {"case_ids": ["opencode:task_verify:0"]}},
        per_type=1,
    )

    assert CASE_ANCHOR_TYPES == (
        "paired_rescue",
        "paired_regression",
        "low_entropy_long_wrong",
        "verify_without_conversion",
        "tool_without_state_shift",
        "operational_error",
    )
    assert anchors == sorted(
        anchors,
        key=lambda case: (
            case["anchor_type"],
            case["harness"],
            case["task_id"],
            case["sample_index"],
        ),
    )
    assert {case["anchor_type"] for case in anchors} >= {
        "verify_without_conversion",
        "tool_without_state_shift",
        "low_entropy_long_wrong",
        "operational_error",
    }
    verify_case = next(case for case in anchors if case["anchor_type"] == "verify_without_conversion")
    assert case_anchor_id(verify_case) == verify_case["anchor_id"]
    assert len(verify_case["evidence_snippets"][0]) <= 240
    assert "/data2" not in verify_case["evidence_snippets"][0]
    assert "sk-private-secret" not in verify_case["evidence_snippets"][0]


def test_paired_rescue_and_regression_anchors_compare_directllm_to_agent():
    anchors = select_insight_case_anchors(
        event_rows=[],
        selected_records=_selected_records(),
        per_type=2,
    )

    rescue = next(case for case in anchors if case["anchor_type"] == "paired_rescue")
    regression = next(case for case in anchors if case["anchor_type"] == "paired_regression")

    assert rescue["anchor_id"] == "paired_rescue:opencode:task_rescue:0"
    assert rescue["directllm_correct"] is False
    assert rescue["agent_correct"] is True
    assert regression["anchor_id"] == "paired_regression:opencode:task_regress:0"
    assert regression["directllm_correct"] is True
    assert regression["agent_correct"] is False
