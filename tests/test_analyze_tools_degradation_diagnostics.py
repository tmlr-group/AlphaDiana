from __future__ import annotations

from analyze_tools.degradation_diagnostics import (
    action_features,
    classify_cause,
    is_instruction_prompt_event,
    language_features,
    paired_outcome_label,
)


def test_language_features_detect_missing_boxed_and_looping_markers():
    features = language_features(
        "Let me try a different approach. I should verify this, but the answer is 9.",
        predicted="9",
    )

    assert features["malformed_prediction"] is True
    assert features["missing_boxed_answer"] is True
    assert features["looping_marker_count"] >= 2
    assert features["tool_intent_marker_count"] >= 1


def test_action_features_capture_verify_conversion_and_tool_order():
    events = [
        {"canonical_action": "plan", "tool_subtype": "other", "observation_status": "none"},
        {"canonical_action": "verify", "tool_subtype": "other", "observation_status": "none"},
        {"canonical_action": "tool_use", "tool_subtype": "python", "observation_status": "success"},
        {"canonical_action": "answer", "tool_subtype": "other", "observation_status": "none"},
    ]

    features = action_features(events)

    assert features["verify_before_answer"] is True
    assert features["post_verify_action_change"] is True
    assert features["tool_before_answer"] is True
    assert features["tool_subtypes"] == "python"
    assert features["tool_use_count"] == 1


def test_regression_cause_prioritizes_answer_format_failures():
    cause = classify_cause(
        paired_outcome="regression",
        harness_record={"score_status": "valid_scored"},
        harness_lang={"malformed_prediction": True, "missing_boxed_answer": True},
        action={
            "tool_use_count": 3,
            "tool_before_answer": True,
            "verify_count": 2,
            "post_verify_action_change": False,
            "plan_count": 4,
            "recover_count": 1,
            "premature_answer": False,
        },
        low_entropy_long=True,
        token_ratio=10.0,
    )

    assert cause == "answer_format_or_extraction"


def test_paired_outcome_labels_direct_regression_and_operational_gap():
    assert paired_outcome_label(True, True, True, False) == "regression"
    assert paired_outcome_label(True, False, True, True) == "rescue"
    assert paired_outcome_label(True, True, False, None) == "nonvalid_or_missing"


def test_instruction_prompt_event_filter_ignores_boxed_format_examples():
    row = {
        "text_span": (
            "You are solving expert-level science multiple-choice questions. "
            "When you have reached your final answer, use $$\\boxed{A}$$. "
            "Problem: Which option is correct? A. alpha B. beta"
        )
    }

    assert is_instruction_prompt_event(row) is True
    assert is_instruction_prompt_event({"text_span": "Therefore $$\\boxed{A}$$"}) is False
