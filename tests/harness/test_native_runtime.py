"""Opt-in real Podman + actual pinned runtimes against a deterministic model."""

import importlib.util
import os
from pathlib import Path

import pytest
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness._internal.podman_agent import NativeHarnessError, tree_digest
from alphadiana.harness._internal.training import (
    ModelIdentity,
    NativeTrainingCollector,
    PolicyIdentity,
    SamplingPlan,
)
from alphadiana.harness.deepseek_harness.agent import DeepSeekHarnessAgent
from alphadiana.harness.pi.agent import PiAgent

spec = importlib.util.spec_from_file_location(
    "native_fixture", Path(__file__).parents[2] / "scripts/validation/native_model_fixture.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
IMAGE = os.environ.get("ALPHADIANA_NATIVE_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE, reason="Set ALPHADIANA_NATIVE_IMAGE to a built immutable image ID"
)


@pytest.mark.parametrize("scenario", ["compaction", "shell_timeout"])
def test_real_pi_compaction_sampling_and_shell_budget(scenario):
    from alphadiana.engine.container_runtime.podman_cli import PodmanCLI

    compact = scenario == "compaction"
    command = "printf '%49000s' X" if compact else "printf START; sleep 3; printf TOO_LATE"
    with fixture.model_server(
        shell_command=command, prompt_tokens=35000 if compact else 10
    ) as (endpoint, requests):
        agent = PiAgent()
        agent.setup(
            {
                "image": IMAGE,
                "model": "fixture-v1",
                "api_base": endpoint,
                "timeout": 90,
                "context_window": 40000,
                "max_tokens": 32000,
                "presence_penalty": 1.5,
                **({} if compact else {"bash_timeout": 1}),
            }
        )
        try:
            response = agent.solve(BenchmarkTask("pi-budget", "Run the shell task.", None))
            assert response.answer == "0" and response.finish_reason == "stop"
            assert all(r.get("presence_penalty") == 1.5 for r in requests)
            if compact:
                summaries = [r for r in requests if not r.get("tools")]
                assert summaries, "Pi did not compact above the configured threshold"
                budgets = {
                    r.get("max_tokens", r.get("max_completion_tokens"))
                    for r in summaries
                }
                assert budgets <= {int(16384 * 0.8), int(16384 * 0.5)}, budgets
            else:
                assert len(requests) == 2
                tool = next(m for m in requests[-1]["messages"] if m.get("role") == "tool")
                assert "START" in tool["content"] and "TOO_LATE" not in tool["content"]
                assert "124" in tool["content"]
            assert PodmanCLI().list_ids(
                labels={"alphadiana.execution": agent.execution_id}
            ) == []
        finally:
            agent.teardown()


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

                from alphadiana.results.store import ResultStore
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


@pytest.mark.parametrize("agent_class", [PiAgent, DeepSeekHarnessAgent])
@pytest.mark.parametrize("finalize_at_budget", [False, True])
def test_real_native_training_capture_uses_native_ids_and_policy_attestation(
    agent_class, tmp_path, finalize_at_budget
):
    """Opt-in integration through the actual Pi image and fixture server."""

    import hashlib

    digest = hashlib.sha256(b"fixture-policy").hexdigest()
    with fixture.model_server(
        api_key="test-native-secret",
        native_capture=True,
        policy_publication="policy-step-001",
        weights_digest=digest,
        tokenizer_digest=digest,
        chat_template_digest=digest,
    ) as (endpoint, requests):
        # The host-side capture proxy connects to the fixture over loopback;
        # only the proxy URL is advertised to the disposable Pi container.
        host_endpoint = endpoint.replace("host.containers.internal", "127.0.0.1")
        agent = agent_class()
        agent.setup(
            {
                "image": IMAGE,
                "model": "fixture-v1",
                "api_base": host_endpoint,
                "api_key": "test-native-secret",
                "timeout": 90,
                "training": {
                    "enabled": True,
                    "max_calls": 2,
                    "finalize_at_budget": finalize_at_budget,
                    "algorithm": "sft",
                    "round_id": "sft-round-001",
                    "task_ids": ["training"],
                    "group_size": 1,
                    "max_new_tokens": 8,
                    "temperature": 1.0,
                    "seed": 17,
                    "policy_publication": "policy-step-001",
                    "policy": {
                        "weights_digest": digest,
                        "tokenizer_digest": digest,
                        "chat_template_digest": digest,
                    },
                    "model_identity": {
                        "model_name": "fixture-v1",
                        "model_revision": "fixture-revision-1",
                        "tokenizer_digest": digest,
                        "chat_template_digest": digest,
                    },
                },
            }
        )
        try:
            response = agent.solve(BenchmarkTask("training", "Read marker.", None))
            capture = response.metadata["training_capture"]
            from alphadiana.results.store import ResultStore
            from alphadiana.harness._internal.training import TrainingCapture
            from alphadiana.scorer.numeric import NumericScorer

            persisted_task = BenchmarkTask("training", "Read marker.", "0")
            store = ResultStore(str(tmp_path / "capture"), "native-training")
            store.append(persisted_task, response, NumericScorer().score(persisted_task, response))
            row = store.load()[0]
            restored = TrainingCapture.from_dict(row["metadata"]["training_capture"])
            assert restored.harness == agent_class.name
            assert len(restored.calls) == 2
            if agent_class is DeepSeekHarnessAgent:
                assert restored.calls[0]["harness_request"]["thinking"] == {"type": "disabled"}
            assert row["response_json"]["native_events"]
            assert capture["harness"] == agent_class.name
            assert len(capture["turns"]) == 2
            if finalize_at_budget:
                assert requests[-1]["tool_choice"] == "none"
                assert restored.calls[-1]["request"]["tool_choice"] == "none"
            assert capture["tokens_native"] is True
            assert capture["policy_attestation"] == "verified"
            assert capture["model_identity"]["model_revision"] == "fixture-revision-1"
            assert capture["turns"][0]["response_token_ids"]
            assert all(request.get("return_token_ids") for request in requests)
            assert all(
                request.get("chat_template_kwargs") == {"enable_thinking": False}
                for request in requests
            )
        finally:
            agent.teardown()


@pytest.mark.parametrize("agent_class", [PiAgent, DeepSeekHarnessAgent])
def test_real_native_grpo_collector_isolates_samples_and_keeps_behavior_logprobs(agent_class):
    """The real Pi containers produce a complete, native-token GRPO group."""

    import hashlib

    digest = hashlib.sha256(b"fixture-policy").hexdigest()
    policy = PolicyIdentity(digest, digest, digest)
    identity = ModelIdentity("fixture-v1", "fixture-revision-1", digest, digest)
    plan = SamplingPlan(
        "grpo-round-001",
        policy,
        ("training",),
        2,
        8,
        1.0,
        17,
        identity,
        "policy-step-001",
        "grpo",
    )
    with fixture.model_server(
        api_key="test-native-secret",
        native_capture=True,
        policy_publication="policy-step-001",
        weights_digest=digest,
        tokenizer_digest=digest,
        chat_template_digest=digest,
    ) as (endpoint, requests):
        collector = NativeTrainingCollector(agent_class)
        batch = collector.collect(
            [BenchmarkTask("training", "Read marker.", None)],
            plan,
            {
                "image": IMAGE,
                "model": "fixture-v1",
                "api_base": endpoint.replace("host.containers.internal", "127.0.0.1"),
                "api_key": "test-native-secret",
                "timeout": 90,
            },
            score=lambda _task, response: float(
                response.metadata["training_capture"]["sample_index"]
            ),
        )
    assert [sample.sample_index for sample in batch.samples] == [0, 1]
    assert [sample.reward for sample in batch.samples] == [0.0, 1.0]
    assert all(
        turn.behavior_log_probs is not None for sample in batch.samples for turn in sample.turns
    )
    assert all(request["return_token_ids"] for request in requests)
    assert all(request["logprobs"] for request in requests)
    assert len(requests) == 4
