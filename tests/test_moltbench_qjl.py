from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.benchmarks.moltbench.harness import MoltBenchDockerAgent
from alphadiana.benchmarks.moltbench.qjl import MoltBenchQJLBenchmark
from alphadiana.benchmarks.registry import BenchmarkRegistry
from alphadiana.harness.base import AgentResponse
from alphadiana.scorer.moltbench_qjl import MoltBenchQJLScorer
from alphadiana.scorer.registry import ScorerRegistry


_AUTO_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL_NAME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def _qjl_task() -> BenchmarkTask:
    return BenchmarkTask(
        task_id="qjl",
        problem="",
        ground_truth={"runtime_ms": 100.0},
        metadata={},
    )


def _qjl_scorer() -> MoltBenchQJLScorer:
    scorer = MoltBenchQJLScorer()
    scorer.setup({"distortion_threshold": 0.15, "min_og_for_correct": 1.0})
    return scorer


def test_qjl_registry_entries_are_available() -> None:
    assert BenchmarkRegistry.get("moltbench_qjl") is MoltBenchQJLBenchmark
    assert ScorerRegistry.get("moltbench_qjl") is MoltBenchQJLScorer


def test_qjl_benchmark_requires_baseline() -> None:
    with pytest.raises(ValueError, match="baseline"):
        MoltBenchQJLBenchmark().load_tasks({})


def test_qjl_task_json_contract() -> None:
    task = MoltBenchQJLBenchmark().load_tasks({
        "baseline": {"runtime_ms": 100.0, "distortion": 0.1},
        "max_rounds": 7,
    })[0]

    task_json = task.metadata["task_json"]
    assert task_json["benchmark_id"] == "qjl"
    assert task_json["workdir"] == "/workspace/repo"
    assert task_json["max_rounds"] == 7
    assert task_json["result_parser"] == "qjl"
    assert task_json["agent_eval_cmd"] == "/eval/run_eval"
    assert "/eval/run_eval" in task_json["protected_eval_paths"]
    assert task_json["verify_cmd"] == ["python3", "/eval/bench_cuda.py"]


def test_qjl_scorer_zero_for_bad_metrics() -> None:
    scorer = _qjl_scorer()
    task = _qjl_task()

    bad_distortion = AgentResponse(
        answer={"runtime_ms": 50.0, "distortion": 0.2, "correctness_pass": True},
        metadata={},
    )
    assert scorer.score(task, bad_distortion).score == 0.0

    timeout = AgentResponse(answer={}, metadata={"timeout": True})
    assert scorer.score(task, timeout).score == 0.0

    invalid_baseline = BenchmarkTask("qjl", "", {"runtime_ms": 0.0}, {})
    valid_metrics = AgentResponse(
        answer={"runtime_ms": 50.0, "distortion": 0.1, "correctness_pass": True},
        metadata={},
    )
    assert scorer.score(invalid_baseline, valid_metrics).score == 0.0


def test_qjl_scorer_returns_og_from_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    score_path = artifacts / "score.json"
    score_path.write_text(
        json.dumps({
            "schema_version": "moltbench.score.v3",
            "correct": True,
            "score": 2.5,
            "G": {"value": 2.5},
            "S": {"value": 1.0},
            "official_result": {
                "metrics": {"runtime_ms": 40.0, "distortion": 0.1, "gain": 2.5},
            },
        }),
        encoding="utf-8",
    )
    (artifacts / "manifest.json").write_text(
        json.dumps({
            "host_owned": True,
            "execution_id": "exec-1",
            "artifact_validation": {"complete": True},
            "artifact_hashes": {
                "score.json": hashlib.sha256(score_path.read_bytes()).hexdigest(),
            },
        }),
        encoding="utf-8",
    )

    result = _qjl_scorer().score(
        _qjl_task(),
        AgentResponse(
            answer={},
            metadata={
                "artifacts_dir": str(artifacts),
                "execution_id": "exec-1",
                "official_eval_host_owned": True,
                "exit_code": 0,
            },
        ),
    )

    assert result.correct is True
    assert result.score == pytest.approx(2.5)
    assert result.metadata["og"] == pytest.approx(2.5)


def test_moltbench_docker_default_command_uses_legacy_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _AUTO_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    agent = MoltBenchDockerAgent()
    agent.setup({"agent_type": "openclaw", "env": {"FOO": "bar"}})

    assert agent._build_run_command("/tmp/task") == [
        "docker",
        "run",
        "-d",
        "--gpus",
        "all",
        "-v",
        "/tmp/task:/workspace/task:ro",
        "-e",
        "FOO=bar",
        "-e",
        "MOLTBENCH_AGENT=openclaw",
        "tmlrgroup/alphadiana:openclaw",
    ]


def test_moltbench_docker_podman_command(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _AUTO_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    agent = MoltBenchDockerAgent()
    agent.setup({
        "agent_type": "openclaw",
        "container_engine": "podman",
        "container_engine_binary": "/opt/podman/bin/podman",
        "container_runtime": "runc",
        "image": "localhost/alphadiana:qjl-openclaw",
        "network_mode": "host",
        "shm_size": "1g",
        "env": {"FOO": "bar"},
    })

    cmd = agent._build_run_command("/tmp/task")
    assert cmd[:9] == [
        "/opt/podman/bin/podman",
        "run",
        "-d",
        "--runtime",
        "runc",
        "--device",
        "nvidia.com/gpu=all",
        "-v",
        "/tmp/task:/workspace/task:ro",
    ]
    assert "--gpus" not in cmd
    assert ["--shm-size", "1g"] == cmd[cmd.index("--shm-size"):cmd.index("--shm-size") + 2]
    assert ["--ipc", "private"] == cmd[cmd.index("--ipc"):cmd.index("--ipc") + 2]
    assert ["--network", "host"] == cmd[cmd.index("--network"):cmd.index("--network") + 2]
    assert ["-e", "FOO=bar"] == cmd[cmd.index("FOO=bar") - 1:cmd.index("FOO=bar") + 1]
    assert cmd[-1] == "localhost/alphadiana:qjl-openclaw"


def test_moltbench_docker_rejects_host_ipc_with_shm_size() -> None:
    agent = MoltBenchDockerAgent()

    with pytest.raises(ValueError, match="shm_size cannot be combined with ipc_mode=host"):
        agent.setup({
            "container_engine": "podman",
            "shm_size": "1g",
            "ipc_mode": "host",
        })
