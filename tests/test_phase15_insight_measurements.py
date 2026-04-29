from __future__ import annotations

from alphadiana.analysis.insight_measurements import (
    INSIGHT_MECHANISMS,
    InsightClaim,
    build_insight_claims,
    claim_low_entropy_long_collapse,
    claim_model_harness_composite,
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
