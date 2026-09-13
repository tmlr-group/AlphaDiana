"""Regression coverage for native harness review: bridge through persistence."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.container_runtime.podman_cli import PodmanError, PodmanResult
from alphadiana.harness._internal import podman_agent as native
from alphadiana.harness.base import AgentResponse
from alphadiana.harness.pi.agent import PiAgent
from alphadiana.scorer.numeric import NumericScorer

CONFIG = {"image": "sha256:" + "a" * 64, "model": "fixture", "api_base": "http://fixture/v1"}


def bridge_response(tmp_path, monkeypatch, *, stderr=""):
    path = Path(__file__).parents[1] / "alphadiana/harness/_internal/deploy/bridge.py"
    spec = importlib.util.spec_from_file_location("bridge_regression", path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    message = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "Check the sum before answering."},
            {"type": "text", "text": "4"},
        ],
        "stopReason": "stop",
    }
    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps({"type": "message_end", "message": message}), stderr=stderr
        ),
    )
    return bridge.pi_run({"config": CONFIG, "problem": "2+2?"})


def test_callback_failure_rejects_successful_model_answer(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="extension failed"):
        bridge_response(
            tmp_path, monkeypatch, stderr="Extension error (session_start): candidate failed\n"
        )


def test_pi_thinking_survives_result_and_trace_persistence(tmp_path, monkeypatch):
    response = AgentResponse(**bridge_response(tmp_path, monkeypatch))
    task = BenchmarkTask(task_id="reasoning", problem="2+2?", ground_truth="4")
    store = ResultStore(str(tmp_path / "results"), "review")
    store.append(task, response, NumericScorer().score(task, response))
    row = store.load()[0]
    assert row["reasoning_trajectory"][0]["content"] == "Check the sum before answering."
    trace = json.loads((store.artifacts_dir / "reasoning/agent/normalized_trace.json").read_text())
    assert trace["reasoning_steps"][0]["content"] == "Check the sum before answering."
    assert store.completed_task_ids() == {"reasoning"}


class Runtime:
    def rootless(self):
        return True

    def image_exists(self, image):
        return True

    def create(self, image, **kwargs):
        self.mount = Path(kwargs["volumes"][0].split(":")[0])

    def start_attached(self, *args, **kwargs):
        return SimpleNamespace(stdout=json.dumps({"answer": "4", "finish_reason": "stop"}))

    def stop(self, *args, **kwargs):
        pass

    def rm(self, *args, **kwargs):
        self.removed = True


def test_nested_public_attachments_ignore_restrictive_umask(monkeypatch):
    class Inspect(Runtime):
        def start_attached(self, *args, **kwargs):
            for path in [self.mount, *self.mount.rglob("*")]:
                assert path.stat().st_mode & 0o777 == (0o755 if path.is_dir() else 0o644)
            assert (self.mount / "attachments/nested/input.txt").read_bytes() == b"public input"
            return super().start_attached(*args, **kwargs)

    monkeypatch.setattr(native, "PodmanCLI", Inspect)
    agent = PiAgent()
    agent.setup(CONFIG)
    old = os.umask(0o077)
    try:
        agent.solve(
            BenchmarkTask(
                "input",
                "read input",
                "private",
                attachments={
                    "nested/input.txt": b"public input",
                },
            )
        )
    finally:
        os.umask(old)
    assert agent.runtime.removed


@pytest.mark.parametrize("phase", ["task", "create", "provider"])
def test_only_owned_task_deadline_is_completed_zero(tmp_path, monkeypatch, phase):
    class Deadline(Runtime):
        def create(self, *args, **kwargs):
            if phase == "create":
                raise PodmanError(PodmanResult(("podman", "create"), 124, "", "timeout"))
            return super().create(*args, **kwargs)

        def start_attached(self, *args, **kwargs):
            if phase == "task":
                raise PodmanError(PodmanResult(("podman", "start"), 124, "", "timeout"))
            raise PodmanError(
                PodmanResult(
                    ("podman", "start"), 1, "", '{"error":"TimeoutError","stage":"execution"}'
                )
            )

    monkeypatch.setattr(native, "PodmanCLI", Deadline)
    agent = PiAgent()
    agent.setup(CONFIG)
    task = BenchmarkTask("deadline", "2+2?", "4")
    if phase != "task":
        with pytest.raises(native.NativeHarnessError):
            agent.solve(task)
    else:
        response = agent.solve(task)
        assert response.finish_reason == "timeout"
        assert response.metadata["native_harness_timeout_scored_zero"] is True
        store = ResultStore(str(tmp_path), "timeout")
        store.append(task, response, NumericScorer().score(task, response))
        row = store.load()[0]
        assert row["score"] == 0 and row["correct"] is False
        assert row["score_status"] == "valid_scored"
        assert store.completed_task_ids() == {"deadline"}
    assert agent.runtime.removed


def test_native_import_does_not_initialize_optional_dataset_runtime():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from alphadiana.engine.runner import Runner; "
            "from alphadiana.harness._internal.podman_agent import validate_config; "
            "assert 'datasets' not in sys.modules; assert 'pyarrow.dataset' not in sys.modules",
        ],
        check=True,
        timeout=30,
    )


def test_captured_deepseek_usage_and_tools_survive_persistence(tmp_path):
    path = Path(__file__).parents[1] / "alphadiana/harness/_internal/deploy/bridge.py"
    spec = importlib.util.spec_from_file_location("bridge_events", path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    events = json.loads(
        (Path(__file__).parent / "fixtures/native_deepseek_events.json").read_text()
    )
    trajectory, usage = bridge.deepseek_events(events)
    assert usage == {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
    response = AgentResponse(
        answer="0",
        trajectory=trajectory,
        token_usage=usage,
        response_json={"native_events": events},
        finish_reason="stop",
    )
    task = BenchmarkTask("tools", "Read marker with shell.", "0")
    store = ResultStore(str(tmp_path / "results"), "review")
    store.append(task, response, NumericScorer().score(task, response))
    row = store.load()[0]
    assert row["token_usage"] == usage
    trace = json.loads((store.artifacts_dir / "tools/agent/normalized_trace.json").read_text())
    calls = [call for step in trace["steps"] for call in step.get("tool_calls", [])]
    results = [result for step in trace["steps"] for result in step.get("tool_results", [])]
    assert len(calls) == len(results) == 1
    assert calls[0]["id"] == results[0]["tool_use_id"] == "fixture_call"
    assert calls[0]["tool"] == "bash" and "command" in calls[0]["input"]
    assert "TOOL_RAN" in results[0]["content"] and not results[0]["is_error"]
    assert row["response_json"]["native_events"] == events


@pytest.mark.parametrize("answer", ["4", ""])
def test_token_limit_is_scored_instead_of_protocol_error(tmp_path, monkeypatch, answer):
    class Length(Runtime):
        def start_attached(self, *args, **kwargs):
            return SimpleNamespace(stdout=json.dumps({"answer": answer, "finish_reason": "length"}))

    monkeypatch.setattr(native, "PodmanCLI", Length)
    agent = PiAgent()
    agent.setup(CONFIG)
    response = agent.solve(BenchmarkTask("length", "2+2?", "4"))
    task = BenchmarkTask("length", "2+2?", "4")
    store = ResultStore(str(tmp_path / "results"), "review")
    store.append(task, response, NumericScorer().score(task, response))
    row = store.load()[0]
    assert row["score"] == (1.0 if answer else 0.0)
    assert row["score_status"] == "valid_scored" and row["finish_reason"] == "length"
    assert store.completed_task_ids() == {"length"}
