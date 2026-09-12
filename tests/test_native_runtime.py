"""Opt-in real Podman + actual pinned runtimes against a deterministic model."""

import importlib.util
import os
from pathlib import Path

import pytest
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness._internal.podman_agent import NativeHarnessError, tree_digest
from alphadiana.harness.deepseek_harness.agent import DeepSeekHarnessAgent
from alphadiana.harness.pi.agent import PiAgent

spec = importlib.util.spec_from_file_location(
    "native_fixture", Path(__file__).parents[1] / "scripts/native_model_fixture.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
IMAGE = os.environ.get("ALPHADIANA_NATIVE_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE, reason="Set ALPHADIANA_NATIVE_IMAGE to a built immutable image ID"
)


@pytest.mark.parametrize("agent_class", [PiAgent, DeepSeekHarnessAgent])
def test_real_baseline_and_plugin(tmp_path, agent_class):
    tmp_path.chmod(0o755)
    # pytest parent directories are private: use a standalone temporary candidate.
    import tempfile

    with (
        tempfile.TemporaryDirectory(prefix="native-candidate-") as directory,
        fixture.model_server(
            api_key="test-native-secret", reasoning_text="Check the observed marker."
        ) as (endpoint, requests),
    ):
        root = Path(directory)
        root.chmod(0o755)
        if agent_class is PiAgent:
            code = (
                'import {writeFileSync} from "node:fs"; export default function(pi) {'
                'pi.on("session_start", async () => {'
                'writeFileSync("/work/marker.txt", "CANDIDATE_GOOD");}); }'
            )
        else:
            code = (
                'import {writeFileSync} from "node:fs"; export const name="candidate";'
                "export function apply() {"
                'writeFileSync("/work/marker.txt", "CANDIDATE_GOOD");}'
            )
        (root / "candidate.ts").write_text(code)
        agent = agent_class()
        config = {
            "image": IMAGE,
            "model": "fixture-v1",
            "api_base": endpoint,
            "timeout": 90,
            "api_key": "test-native-secret",
        }
        agent.setup(config)
        try:
            baseline = agent.solve(
                BenchmarkTask(
                    task_id="base",
                    problem="Read the marker with the shell.",
                    ground_truth="PRIVATE_GOLD_73afdd7e",
                )
            )
            assert baseline.answer == "0", baseline
            agent.config.update(
                candidate_root=str(root),
                candidate_entry="candidate.ts",
                candidate_digest=tree_digest(root),
            )
            candidate = agent.solve(
                BenchmarkTask(
                    task_id="next",
                    problem="Read the marker with the shell.",
                    ground_truth="PRIVATE_GOLD_73afdd7e",
                )
            )
            assert candidate.answer == "1", candidate
            if agent_class in {PiAgent, DeepSeekHarnessAgent}:
                import json

                from alphadiana.analysis.io.result_store import ResultStore
                from alphadiana.scorer.numeric import NumericScorer

                task = BenchmarkTask("persist", "Read marker.", "1")
                store = ResultStore(str(tmp_path / "persist"), "review")
                store.append(task, candidate, NumericScorer().score(task, candidate))
                row = store.load()[0]
                assert row["token_usage"]["prompt_tokens"] == 20
                assert row["token_usage"]["completion_tokens"] == 8
                assert row["response_json"]["native_events"]
                assert row["reasoning_trajectory"][0]["content"] == "Check the observed marker."
                trace = json.loads(
                    (store.artifacts_dir / "persist/agent/normalized_trace.json").read_text()
                )
                calls = [call for step in trace["steps"] for call in step.get("tool_calls", [])]
                results = [
                    result for step in trace["steps"] for result in step.get("tool_results", [])
                ]
                assert len(calls) == len(results) == 1
                assert calls[0]["id"] == results[0]["tool_use_id"]
                assert "TOOL_RAN" in results[0]["content"]
            if agent_class is PiAgent:
                (root / "candidate.ts").write_text(
                    'export default function(pi) {pi.on("session_start", () => {'
                    'throw new Error("callback regression");});}'
                )
                agent.config["candidate_digest"] = tree_digest(root)
                before = len(requests)
                with pytest.raises(NativeHarnessError, match="plugin"):
                    agent.solve(BenchmarkTask("callback", "Read marker.", "private"))
                assert len(requests) > before  # Pi still called the model after callback failure.
            (root / "candidate.ts").write_text("export default {{{ definitely invalid syntax")
            agent.config["candidate_digest"] = tree_digest(root)
            with pytest.raises(NativeHarnessError):
                agent.solve(BenchmarkTask(task_id="broken", problem="test", ground_truth="hidden"))
            assert len(requests) >= 4
            assert any(
                "TOOL_RAN" in str(m.get("content"))
                for r in requests
                for m in r["messages"]
                if m.get("role") == "tool"
            )
            assert all("PRIVATE_GOLD_73afdd7e" not in str(request) for request in requests)
        finally:
            agent.teardown()


@pytest.mark.parametrize("agent_class", [PiAgent, DeepSeekHarnessAgent])
def test_real_timeout_cleanup(agent_class):
    from alphadiana.engine.container_runtime.podman_cli import PodmanCLI

    agent = agent_class()
    agent.setup(
        {
            "image": IMAGE,
            "model": "fixture-v1",
            "api_base": "http://host.containers.internal:1/v1",
            "timeout": 0.5,
        }
    )
    response = agent.solve(BenchmarkTask(task_id="timeout", problem="test", ground_truth="hidden"))
    assert response.answer is None and response.finish_reason == "timeout"
    assert response.metadata["native_harness_timeout_scored_zero"] is True
    assert PodmanCLI().list_ids(labels={"alphadiana.execution": agent.execution_id}) == []
    agent.teardown()


@pytest.mark.parametrize("agent_class", [PiAgent, DeepSeekHarnessAgent])
def test_real_nested_attachments_under_private_umask(agent_class):
    import tempfile

    with (
        tempfile.TemporaryDirectory(prefix="native-attachment-") as directory,
        fixture.model_server() as (endpoint, _requests),
    ):
        root = Path(directory)
        root.chmod(0o755)
        action = (
            'writeFileSync("/work/marker.txt", readFileSync("/work/attachments/nested/input.txt"));'
        )
        prefix = 'import {readFileSync,writeFileSync} from "node:fs";'
        code = (
            prefix + 'export default function(pi) {pi.on("session_start", () => {' + action + "});}"
            if agent_class is PiAgent
            else prefix + 'export const name="input";export function apply(){' + action + "}"
        )
        (root / "candidate.ts").write_text(code)
        agent = agent_class()
        agent.setup(
            {
                "image": IMAGE,
                "model": "fixture-v1",
                "api_base": endpoint,
                "candidate_root": str(root),
                "candidate_entry": "candidate.ts",
                "candidate_digest": tree_digest(root),
                "timeout": 90,
            }
        )
        old = os.umask(0o077)
        try:
            result = agent.solve(
                BenchmarkTask(
                    "input",
                    "Read the marker with the shell.",
                    "private",
                    attachments={"nested/input.txt": b"CANDIDATE_GOOD"},
                )
            )
            assert result.answer == "1"
        finally:
            os.umask(old)
            agent.teardown()


def test_real_pi_token_limit_answer_is_scored(tmp_path):
    with fixture.model_server(final_finish_reason="length") as (endpoint, requests):
        agent = PiAgent()
        agent.setup({"image": IMAGE, "model": "fixture-v1", "api_base": endpoint, "timeout": 90})
        try:
            task = BenchmarkTask("length", "Read marker with shell.", "0")
            response = agent.solve(task)
            assert response.answer == "0" and response.finish_reason == "length"
            from alphadiana.scorer.numeric import NumericScorer

            assert NumericScorer().score(task, response).score == 1.0
            assert len(requests) == 2
        finally:
            agent.teardown()
