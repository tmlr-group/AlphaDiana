from __future__ import annotations

import pytest

from alphadiana.analysis.action_events import (
    ACTION_LABEL_CONFIDENCES,
    ACTION_PRIORITY,
    CANONICAL_ACTIONS,
    EVENT_SOURCES,
    OBSERVATION_STATUSES,
    TOOL_SUBTYPES,
    ActionEvent,
    classify_action_intent,
    classify_tool_subtype,
    observation_status_from_text,
)


def test_action_event_constants_are_locked():
    assert CANONICAL_ACTIONS == ("plan", "reason", "tool_use", "verify", "recover", "answer")
    assert ACTION_PRIORITY == ("answer", "recover", "verify", "tool_use", "plan", "reason")
    assert ACTION_LABEL_CONFIDENCES == ("strict", "heuristic")
    assert EVENT_SOURCES == ("trajectory", "normalized_trace", "artifact_stream", "raw_text")
    assert TOOL_SUBTYPES == (
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
    assert OBSERVATION_STATUSES == ("none", "info", "success", "fail", "error", "timeout", "truncated")


def test_action_event_to_row_shape():
    event = ActionEvent(
        run_id="run-a",
        harness="openclaw",
        task_id="gpqa_1",
        sample_index=0,
        step_id="2",
        canonical_action="tool_use",
        action_label_confidence="strict",
        source="trajectory",
        tool_subtype="shell",
        observation_status="none",
        recovery_context=False,
        text_span="python solve.py",
        score_status="valid_scored",
        correct=True,
    )

    assert event.to_row() == {
        "run_id": "run-a",
        "harness": "openclaw",
        "task_id": "gpqa_1",
        "sample_index": 0,
        "step_id": "2",
        "canonical_action": "tool_use",
        "action_label_confidence": "strict",
        "source": "trajectory",
        "tool_subtype": "shell",
        "observation_status": "none",
        "recovery_context": False,
        "text_span": "python solve.py",
        "score_status": "valid_scored",
        "correct": True,
    }


def test_classify_action_intent_priority_order():
    assert classify_action_intent({"content": "Final answer: C", "type": "tool_use"}) == (
        "answer",
        "strict",
        "other",
    )
    assert classify_action_intent(
        {"content": "I should verify this by running a check", "tool_name": "python"},
        previous_observation_status="error",
    ) == ("recover", "heuristic", "python")
    assert classify_action_intent({"content": "verify the answer with a quick check", "type": "tool_use"}) == (
        "verify",
        "heuristic",
        "other",
    )
    assert classify_action_intent({"content": "search web for supporting facts", "tool_name": "search"}) == (
        "tool_use",
        "strict",
        "search",
    )
    assert classify_action_intent({"content": "Plan: first compare the options"}) == ("plan", "heuristic", "other")
    assert classify_action_intent({"content": "The molecule likely forms an intermediate"}) == (
        "reason",
        "heuristic",
        "other",
    )


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("web_search", "", "search"),
        ("read_file", "", "read"),
        ("edit", "write the answer file", "write"),
        ("memory", "recall stored note", "memory_read"),
        ("memory", "save this memory", "memory_write"),
        ("bash", "", "shell"),
        ("python", "", "python"),
        ("agent", "delegate to subagent", "subagent"),
        ("browser_open", "", "browser"),
        ("custom", "", "other"),
    ],
)
def test_classify_tool_subtype_mapping(name, text, expected):
    assert classify_tool_subtype(name, text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "none"),
        ("command timed out", "timeout"),
        ("Traceback: exception raised", "error"),
        ("assertion failed", "fail"),
        ("all tests passed", "success"),
        ("neutral observation text", "info"),
    ],
)
def test_observation_status_from_text_mapping(text, expected):
    assert observation_status_from_text(text) == expected
