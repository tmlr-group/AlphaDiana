"""Explicit last-call finalization keeps real tokens and strict capture bounds."""

from copy import deepcopy

import pytest

from alphadiana.harness._internal.training import (
    CaptureConfig, NativeCapture, capture_config_from_dict, validate_training_config,
)
from tests.harness.test_pi_training import completion, plan, request


def tool_request():
    return {
        **request(),
        "tools": [{"type": "function", "function": {
            "name": "bash", "parameters": {"type": "object"},
        }}],
        "tool_choice": "auto",
    }


def tool_completion():
    result = completion(finish="tool_calls")
    result["choices"][0]["message"] = {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "bash", "arguments": '{"command":"true"}'},
        }],
    }
    return result


def test_finalization_is_opt_in():
    capture = NativeCapture(plan(), "task-a", 0, "fixture-v1", 1)
    assert capture.prepare(tool_request())["tool_choice"] == "auto"


def test_last_real_call_finalizes_without_mutating_history_or_native_tokens():
    capture = NativeCapture(plan(), "task-a", 0, "fixture-v1", 2, finalize_at_budget=True)
    original = tool_request()
    first = capture.prepare(original)
    assert first["tool_choice"] == "auto"
    response = tool_completion()
    capture.record(first, response)
    followup = deepcopy(original)
    followup["messages"].extend([
        response["choices"][0]["message"],
        {"role": "tool", "tool_call_id": "call-1", "content": "exit 0"},
    ])
    before = deepcopy(followup)
    last = capture.prepare(followup)
    assert followup == before
    assert last["messages"] == before["messages"]
    assert last["tools"] == before["tools"]
    assert last["tool_choice"] == "none"
    final = completion()
    capture.record(last, final)
    assert len(capture.finish()) == 2
    assert capture.calls[-1]["request"]["tool_choice"] == "none"
    assert list(capture.turns[-1].response_token_ids) == final["choices"][0]["token_ids"]
    with pytest.raises(ValueError, match="model-call budget"):
        capture.prepare(followup)


def test_finalization_rejects_backend_that_ignores_no_tool_request():
    capture = NativeCapture(plan(), "task-a", 0, "fixture-v1", 1, finalize_at_budget=True)
    prepared = capture.prepare(tool_request())
    with pytest.raises(ValueError, match="must not contain tool calls"):
        capture.record(prepared, tool_completion())
    assert not capture.turns


def test_finalization_does_not_add_tools_to_text_only_request():
    capture = NativeCapture(plan(), "task-a", 0, "fixture-v1", 1, finalize_at_budget=True)
    assert "tool_choice" not in capture.prepare(request())


def test_finalization_config_roundtrip():
    config = CaptureConfig(plan(), "task-a", 0, "fixture-v1", "attempt-1",
                           finalize_at_budget=True)
    value = config.to_dict()
    # model/attempt are runtime identity fields rather than public settings.
    value.pop("model_name")
    value.pop("attempt_id")
    validate_training_config(value)
    restored = capture_config_from_dict(value, task_id="task-a", sample_index=0,
                                        model_name="fixture-v1", attempt_id="attempt-1")
    assert restored.finalize_at_budget is True


@pytest.mark.parametrize("value", [1, "true", None])
def test_finalization_requires_boolean(value):
    with pytest.raises(TypeError, match="finalize_at_budget"):
        NativeCapture(plan(), "task-a", 0, "fixture-v1", 1, finalize_at_budget=value)
