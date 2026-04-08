"""Regression tests for MoltBench Docker/runtime integration fixes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from alphadiana.agent.moltbench_docker import MoltBenchDockerAgent
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.benchmark.moltbench import MoltBenchBenchmark
from alphadiana.benchmark.moltbench_utils import build_task_json
from alphadiana.runner.runner import _bind_runtime_task


def test_claude_code_dockerfile_installs_uv():
    dockerfile = Path("moltbench/docker/Dockerfile.claude-code").read_text(encoding="utf-8")
    assert "python3 -m pip install --no-cache-dir uv" in dockerfile


def test_build_task_json_uses_configured_source():
    task_json = build_task_json(
        level=1,
        problem_id=7,
        max_rounds=3,
        backend="triton",
        source="local",
        prompt="optimize this kernel",
    )

    assert task_json["source"] == "local"
    assert task_json["setup_cmd"][-4:] == ["--backend", "triton", "--source", "local"]


def test_run_task_fallback_uses_task_source():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    assert "SOURCE=$(python3 -c" in script
    assert '--source "$SOURCE"' in script


def test_run_task_die_relies_on_exit_trap_for_single_artifact_collection():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    assert 'RUN_INVALID=1' in script
    assert 'RUN_INVALID_REASON="$*"' in script
    assert 'log "FATAL: $*"' in script


def test_run_task_invalid_failures_write_invalid_results_json():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    assert 'final_verification_results.json' in script
    assert '"correctness": "INVALID"' in script
    assert 'results["invalid_reason"] = reason' in script
    assert 'Recorded INVALID results.json due to agent failure' in script


def test_run_task_openclaw_fails_when_gateway_returns_no_events():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    assert 'die "OpenClaw agent produced no events"' in script
    assert 'OpenClaw produced no SSE events' in script


def test_run_task_opencode_fails_when_cli_outputs_error_record():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    assert 'die "OpenCode exited with status $opencode_status"' in script
    assert 'die "OpenCode agent error: ${opencode_error_msg:-unknown error}"' in script
    assert 'if record.get("type") == "error":' in script


def test_bind_runtime_task_attaches_sample_and_unique_execution_id():
    task = BenchmarkTask(task_id="moltbench_L1_P007", problem="p", ground_truth={})

    first = _bind_runtime_task(task, sample_index=0)
    second = _bind_runtime_task(task, sample_index=2)

    assert first is not task
    assert first.metadata["sample_index"] == 0
    assert second.metadata["sample_index"] == 2
    assert first.metadata["execution_id"] != second.metadata["execution_id"]
    assert "execution_id" not in task.metadata


def test_moltbench_docker_isolates_artifacts_per_execution(monkeypatch, tmp_path):
    copied_dirs: list[Path] = []

    def fake_run(cmd, timeout=None, **kwargs):
        if cmd[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="container logs\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_docker_cp(self, container_id, src, dst):
        dst_path = Path(dst)
        copied_dirs.append(dst_path)
        dst_path.mkdir(parents=True, exist_ok=True)
        (dst_path / "results.json").write_text(
            json.dumps({"correctness": "PASS", "speedup": 2.0}),
            encoding="utf-8",
        )

    monkeypatch.setattr("alphadiana.agent.moltbench_docker._run", fake_run)
    monkeypatch.setattr(MoltBenchDockerAgent, "_docker_run", lambda self, task_dir: "container123")
    monkeypatch.setattr(MoltBenchDockerAgent, "_docker_wait", lambda self, container_id: 0)
    monkeypatch.setattr(MoltBenchDockerAgent, "_docker_cp", fake_docker_cp)

    agent = MoltBenchDockerAgent()
    agent.setup({"agent_type": "claude-code", "output_dir": str(tmp_path)})

    task_a = BenchmarkTask(
        task_id="moltbench_L1_P001",
        problem="p",
        ground_truth={},
        metadata={"sample_index": 1, "execution_id": "exec-a"},
    )
    task_b = BenchmarkTask(
        task_id="moltbench_L1_P001",
        problem="p",
        ground_truth={},
        metadata={"sample_index": 1, "execution_id": "exec-b"},
    )

    response_a = agent.solve(task_a)
    response_b = agent.solve(task_b)

    path_a = Path(response_a.metadata["artifacts_dir"])
    path_b = Path(response_b.metadata["artifacts_dir"])

    assert path_a != path_b
    assert path_a == tmp_path / "moltbench_L1_P001" / "sample_1" / "exec-a"
    assert path_b == tmp_path / "moltbench_L1_P001" / "sample_1" / "exec-b"
    assert copied_dirs == [path_a, path_b]
    assert json.loads((path_a / "results.json").read_text(encoding="utf-8"))["speedup"] == 2.0
    assert json.loads((path_b / "results.json").read_text(encoding="utf-8"))["speedup"] == 2.0


def test_moltbench_benchmark_propagates_source_to_task_json(monkeypatch):
    monkeypatch.setattr(
        "alphadiana.benchmark.moltbench.load_kernelbench_problems",
        lambda **kwargs: [{
            "level": 1,
            "problem_id": 3,
            "name": "L1_P003",
            "source_code": "class Model:\n    pass\n",
        }],
    )

    benchmark = MoltBenchBenchmark()
    tasks = benchmark.load_tasks({"levels": [1], "source": "local", "backend": "triton"})

    assert len(tasks) == 1
    assert tasks[0].metadata["source"] == "local"
    assert tasks[0].metadata["task_json"]["source"] == "local"
    assert tasks[0].metadata["task_json"]["setup_cmd"][-1] == "local"
