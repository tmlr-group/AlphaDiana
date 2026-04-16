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


def test_run_task_zeroclaw_uses_snake_case_config_keys():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    assert "workspace_only = false" in script
    assert "max_actions_per_hour = 200" in script
    assert "max_tool_iterations = 100" in script
    assert 'zeroclaw_provider="custom:${zeroclaw_api_base}"' in script
    assert 'export ZEROCLAW_PROVIDER="${zeroclaw_provider}"' in script
    assert "workspace-only" not in script
    assert "max-actions-per-hour" not in script
    assert "max-tool-iterations" not in script


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


def test_moltbench_docker_rewrites_kernelbench_setup_to_offline_file(monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    def fake_run(cmd, timeout=None, **kwargs):
        if cmd[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="container logs\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_docker_run(self, task_dir):
        captured["task_json"] = Path(task_dir, "task.json").read_text(encoding="utf-8")
        captured["problem_py"] = Path(task_dir, "problem.py").read_text(encoding="utf-8")
        return "container123"

    def fake_docker_cp(self, container_id, src, dst):
        dst_path = Path(dst)
        dst_path.mkdir(parents=True, exist_ok=True)
        (dst_path / "results.json").write_text(
            json.dumps({"correctness": "PASS", "speedup": 1.0}),
            encoding="utf-8",
        )

    monkeypatch.setattr("alphadiana.agent.moltbench_docker._run", fake_run)
    monkeypatch.setattr(MoltBenchDockerAgent, "_docker_run", fake_docker_run)
    monkeypatch.setattr(MoltBenchDockerAgent, "_docker_wait", lambda self, container_id: 0)
    monkeypatch.setattr(MoltBenchDockerAgent, "_docker_cp", fake_docker_cp)

    agent = MoltBenchDockerAgent()
    agent.setup({"agent_type": "zeroclaw", "output_dir": str(tmp_path)})

    task = BenchmarkTask(
        task_id="moltbench_L1_P001",
        problem="optimize this kernel",
        ground_truth={},
        metadata={
            "task_json": build_task_json(
                level=1,
                problem_id=1,
                max_rounds=1,
                backend="triton",
                source="hf",
                prompt="optimize this kernel",
            ),
            "source_code": "class Model:\n    pass\n",
            "sample_index": 0,
            "execution_id": "exec-offline",
        },
    )

    response = agent.solve(task)
    written = json.loads(captured["task_json"])

    assert written["source"] == "file"
    assert written["file_path"] == "/workspace/task/problem.py"
    assert written["setup_cmd"][-4:] == ["--source", "file", "--file-path", "/workspace/task/problem.py"]
    assert captured["problem_py"] == "class Model:\n    pass\n"
    assert response.metadata["agent_type"] == "zeroclaw"


def test_run_task_initializes_workdir_before_trap():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    # WORKDIR must be initialized before the EXIT trap can fire
    lines = script.splitlines()
    workdir_init = next(i for i, l in enumerate(lines) if l.strip().startswith('WORKDIR="$DEFAULT_WORKDIR"'))
    trap_line = next(i for i, l in enumerate(lines) if l.strip().startswith('trap collect_artifacts EXIT'))
    assert workdir_init < trap_line, "WORKDIR must be initialized before EXIT trap is set"


def test_run_task_setup_problem_calls_die_on_failure():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    assert 'die "Setup command failed:' in script
    assert 'die "KernelBench setup failed' in script


def test_run_task_final_verification_uses_uv_run():
    script = Path("moltbench/docker/run_task.sh").read_text(encoding="utf-8")
    assert 'verify_cmd="uv run ${verify_cmd}"' in script


def test_dockerfile_base_installs_python310_dev():
    dockerfile = Path("moltbench/docker/Dockerfile.base").read_text(encoding="utf-8")
    assert "python3.10-dev" in dockerfile


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
