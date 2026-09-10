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
        fixture.model_server(api_key="test-native-secret") as (endpoint, requests),
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
    with pytest.raises(NativeHarnessError):
        agent.solve(BenchmarkTask(task_id="timeout", problem="test", ground_truth="hidden"))
    assert PodmanCLI().list_ids(labels={"alphadiana.execution": agent.execution_id}) == []
    agent.teardown()
