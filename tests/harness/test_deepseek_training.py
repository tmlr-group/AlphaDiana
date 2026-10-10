"""Pinned DeepSeek wire fields must not silently change the sampled policy."""

import copy

import pytest
from alphadiana.harness._internal.training import NativeCapture, TrainingCapture
from alphadiana.harness.deepseek_harness.training import DeepSeekCapture
from test_pi_training import completion, plan, request


def wire_request():
    return {
        **request(),
        "thinking": {"type": "disabled"},
        "dsh_plugin_packages": {"version": 1, "packages": [{"name": "p", "version": "1"}]},
    }


def test_deepseek_wire_adapter_preserves_model_input_and_native_ids():
    original = wire_request()
    capture = DeepSeekCapture(plan(), "task-a", 0, "fixture-v1", 2)
    prepared = capture.prepare(original)
    assert prepared["messages"] == original["messages"]
    assert "thinking" not in prepared and "dsh_plugin_packages" not in prepared
    assert original == wire_request()
    capture.record(prepared, completion())
    saved = capture.capture().to_dict()
    saved["harness"] = "deepseek_harness"
    loaded = TrainingCapture.from_dict(saved)
    assert loaded.to_dict()["harness"] == "deepseek_harness"
    assert loaded.turns[0].response_token_ids == (11, 12)


@pytest.mark.parametrize(
    "fields",
    [
        {"thinking": {"type": "enabled"}},
        {"thinking": None},
        {"reasoning_effort": "high"},
        {"dsh_plugin_packages": {"version": 2, "packages": []}},
        {"dsh_plugin_packages": {"version": 1, "packages": [{"name": "p"}]}},
    ],
)
def test_deepseek_rejects_policy_changes_and_unknown_metadata(fields):
    capture = DeepSeekCapture(plan(), "task-a", 0, "fixture-v1", 2)
    with pytest.raises(ValueError):
        capture.prepare({**wire_request(), **fields})
    assert not capture.turns


def test_pi_does_not_silently_accept_deepseek_fields():
    capture = NativeCapture(plan(), "task-a", 0, "fixture-v1", 2)
    with pytest.raises(ValueError, match="unsupported provider"):
        capture.prepare(wire_request())


def test_terminal_answer_cannot_be_followed_by_another_call():
    capture = DeepSeekCapture(plan(), "task-a", 0, "fixture-v1", 3)
    capture.record(capture.prepare(wire_request()), completion())
    with pytest.raises(ValueError, match="terminal answer"):
        capture.prepare(wire_request())


def test_every_grpo_call_requires_policy_attestation():
    current = plan(algorithm="grpo", group_size=2)
    capture = DeepSeekCapture(
        current,
        "task-a",
        0,
        "fixture-v1",
        3,
        require_logprobs=True,
        require_policy_attestation=True,
    )
    response = completion()
    response.pop("policy_identity")
    with pytest.raises(ValueError, match="weights|attest"):
        capture.record(capture.prepare(wire_request()), response)


def test_tool_result_ids_cannot_be_rewritten():
    capture = DeepSeekCapture(plan(), "task-a", 0, "fixture-v1", 3)
    first = wire_request()
    response = completion(finish="tool_calls")
    tool = {"id": "call-1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
    response["choices"][0]["message"] = {"role": "assistant", "content": None, "tool_calls": [tool]}
    capture.record(capture.prepare(first), response)
    second = copy.deepcopy(first)
    second["messages"] += [
        response["choices"][0]["message"],
        {"role": "tool", "tool_call_id": "wrong-id", "content": "result"},
    ]
    with pytest.raises(ValueError, match="tool result identity"):
        capture.prepare(second)
