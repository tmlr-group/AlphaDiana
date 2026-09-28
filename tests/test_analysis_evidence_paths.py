"""Analysis reads canonical sidecars and excludes prompts from agent events."""

import json

import pytest

from alphadiana.analysis.action_events import extract_action_event_rows
from alphadiana.analysis.result_reader import RunBundle, resolve_run_relative_path


def test_trace_survives_rescore_in_another_directory(tmp_path):
    original = tmp_path / "original/artifacts"
    trace = original / "task/agent/trace.json"
    trace.parent.mkdir(parents=True)
    trace.write_text(
        json.dumps(
            {
                "steps": [
                    {"role": "system", "content": "Plan carefully"},
                    {"role": "user", "content": "Verify this problem"},
                    {"role": "assistant", "type": "answer", "content": "2"},
                ]
            }
        )
    )
    record = {
        "task_id": "task",
        "sample_index": 0,
        "artifact_origin": str(original),
        "artifact_manifest": {"files": {"normalized_trace": "task/agent/trace.json"}},
    }
    target = tmp_path / "derived"
    bundle = RunBundle(
        target,
        "rescore",
        target / "rescore.jsonl",
        target / "rescore",
        {},
        [record],
        {},
    )
    rows = extract_action_event_rows(bundle, harness="custom")
    assert len(rows) == 1
    assert rows[0]["text_span"] == "2"
    assert rows[0]["source"] == "normalized_trace"


@pytest.mark.parametrize("reference", ["../outside", "/tmp/outside"])
def test_artifact_references_cannot_escape_root(tmp_path, reference):
    with pytest.raises(ValueError):
        resolve_run_relative_path(tmp_path, reference)


def test_native_opencode_bookkeeping_is_not_an_action():
    from alphadiana.harness.opencode.agent import _opencode_trajectory_records
    from alphadiana.harness.proxies.preservation import build_event_trajectories

    events = [
        {"type": "step_start", "part": {"type": "step-start"}},
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "bash",
                "callID": "c",
                "state": {"input": {"command": "echo 2"}, "output": "2"},
            },
        },
        {"type": "text", "part": {"type": "text", "text": "Answer 2"}},
        {"type": "step_finish", "part": {"type": "step-finish"}},
    ]
    steps, _ = build_event_trajectories(
        [], _opencode_trajectory_records(events), final_output="Answer 2"
    )
    assert len(steps) == 3
    assert [s["role"] for s in steps] == ["assistant", "tool", "assistant"]
    assert steps[0]["type"] == "tool_use"
    assert "echo 2" in steps[0]["content"]
    assert steps[1]["type"] == "tool_result"


def test_openclaw_external_capture_has_single_owner(monkeypatch):
    from alphadiana.harness.openclaw import runtime

    manager = runtime.OpenClawPodmanRuntimeManager(
        {
            "runtime_backend": "podman",
            "capture_logprobs": True,
            "OPENAI_BASE_URL": "http://127.0.0.1:18960/v1",
            "OPENAI_API_KEY": "EMPTY",
            "OPENAI_MODEL_NAME": "fixture",
        }
    )

    def duplicate_proxy(*args, **kwargs):
        raise AssertionError(
            "Second capture proxy would route a container alias from the host"
        )

    monkeypatch.setattr(runtime, "OpenClawLogprobProxy", duplicate_proxy)
    manager.set_external_provider_proxy(
        "http://host.containers.internal:1234/v1", "fixture-key"
    )
    env = manager._resolve_podman_env({})
    assert env["OPENAI_BASE_URL"] == "http://host.containers.internal:1234/v1"
    assert env["OPENAI_API_KEY"] == "fixture-key"
    assert manager._logprob_proxy is None
