"""Captured native events survive the canonical response/analysis boundary."""

import json
from pathlib import Path

import pytest

from alphadiana.analysis.action_events import classify_action_intent
from alphadiana.harness.zeroclaw.agent import ZeroClawAgent
from alphadiana.harness.zeroclaw.trace import normalize_trace

FIXTURE = Path(__file__).parent / "fixtures/zeroclaw/native-0.6.9-tool-trace.jsonl"


def records():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def test_captured_tool_trace_has_real_actions_and_complete_usage():
    trajectory, reasoning, usage, coverage = normalize_trace(
        [{"role": "user", "content": "Calculate"}], records(), final_output="5017"
    )
    assert len(trajectory) == 4
    call, observation, answer = trajectory[1:]
    assert call["name"] == "shell"
    assert classify_action_intent(call)[0] == "tool_use"
    assert observation["role"] == "tool" and observation["content"] == "5017\n"
    assert observation["success"] is True
    assert answer["type"] == "answer" and answer["content"] == "5017"
    assert reasoning == []  # Native trace does not provide private reasoning.
    assert usage == {"prompt_tokens": 25175, "completion_tokens": 45, "total_tokens": 25220}
    assert coverage["usage_complete"] is True
    assert coverage["unmapped_event_counts"] == {}


@pytest.mark.parametrize("missing", [None, -1, True, "40"])
def test_partial_or_malformed_usage_is_not_a_total(missing):
    rows = records()
    rows[1]["payload"]["output_tokens"] = missing
    _, _, usage, coverage = normalize_trace([], rows, final_output="5017")
    assert usage == {}
    assert coverage["responses_with_usage"] == 1
    assert coverage["usage_complete"] is False


def test_truncated_trace_and_unknown_events_are_visible():
    rows = records()[1:] + [{"event_type": "future_event", "payload": {}}]
    _, _, usage, coverage = normalize_trace([], rows, final_output="")
    assert usage == {}
    assert coverage["unmapped_event_counts"] == {"future_event": 1}


def test_native_response_boundary_persists_usage_and_raw_trace():
    agent = ZeroClawAgent()
    agent._logprob_capture = {"enabled": False}
    response = agent._build_cli_response(
        prompt="Calculate", raw_output="5017", raw_stderr="",
        runtime_trace=FIXTURE.read_text(), attachment_items=[], metadata={},
        wall_time_sec=1, system_prompt="", request_messages=[],
    )
    assert response.token_usage["total_tokens"] == 25220
    assert response.metadata["native_trace_coverage"]["usage_complete"]
    assert response.workspace_file_contents["runtime_trace.jsonl"] == FIXTURE.read_text()
    assert len(response.trajectory) == 3


def test_legacy_message_trace_retains_shared_projection():
    trajectory, _, usage, coverage = normalize_trace(
        [], [{"type": "text", "content": "old format"}], final_output="old format"
    )
    assert trajectory == [{"role": "assistant", "type": "text", "content": "old format"}]
    assert usage == {} and coverage == {"native_records": 0}
