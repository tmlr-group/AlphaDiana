from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alphadiana.dashboard.backend.data_loader import _build_summary
from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.analysis.report import ReportGenerator
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.benchmarks.terminal_bench2.benchmark import TerminalBench2Benchmark
from alphadiana.benchmarks.terminal_bench2.harness.pi_harbor import (
    HarborTrialError,
    TerminalBench2PiHarborAgent,
    _read_pi_trace,
    _request_audit_error,
    _provider_reasoning_effort,
)
from alphadiana.benchmarks.terminal_bench2.scorer import TerminalBench2Scorer
from alphadiana.engine.config.experiment_config import ExperimentConfig
from alphadiana.engine.config.validator import ConfigValidator
from alphadiana.metrics.answer_length import AnswerLengthMetric


def _write(path: Path, value: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def _configured_agent(
    tmp_path: Path, *, missing_reward_policy: str = "zero"
) -> tuple[TerminalBench2PiHarborAgent, Path]:
    pi_install = tmp_path / "pi-install"
    pi_install.mkdir()
    _write(pi_install / "pi", "#!/usr/bin/env node\n")
    node = _write(tmp_path / "node")
    node.chmod(0o755)
    candidate = _write(tmp_path / "candidate.ts", "export default function() {}\n")
    audit = _write(tmp_path / "audit.ts", "export default function() {}\n")
    models = _write(tmp_path / "models.json", "{}\n")
    compose = _write(tmp_path / "compose.yaml", "services: {main: {}}\n")
    jobs = tmp_path / "jobs"

    agent = TerminalBench2PiHarborAgent()
    agent.setup(
        {
            "harbor_bin": "/bin/true",
            "pi_install_path": str(pi_install),
            "node_bin_path": str(node),
            "candidate_path": str(candidate),
            "audit_path": str(audit),
            "models_path": str(models),
            "compose_path": str(compose),
            "jobs_dir": str(jobs),
            "pi_model": "local/model",
            "harbor_model": "local/model",
            "missing_reward_policy": missing_reward_policy,
        }
    )
    return agent, jobs


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        task_id="tb2_demo-task",
        problem="do it",
        ground_truth="1",
        metadata={"sample_index": 0, "execution_id": "abcdef0123456789"},
    )


def _write_harbor_trial(
    jobs: Path,
    command: list[str],
    *,
    trace_events: list[dict],
    reward: float | None,
    exception_info: dict | None = None,
    tokens: tuple[int, int, int] = (12, 3, 4),
    write_audit: bool = True,
    trial_log: str = "",
) -> Path:
    job_name = command[command.index("--job-name") + 1]
    trial = jobs / job_name / "demo-task__abc"
    _write(
        trial / "agent" / "pi.jsonl",
        "".join(json.dumps(event) + "\n" for event in trace_events),
    )
    if write_audit:
        _write(
            trial / "agent" / "request-audit.jsonl",
            json.dumps(
                {
                    "model": "model",
                    "reasoning": {"effort": "max"},
                }
            )
            + "\n",
        )
    if reward is not None:
        _write(trial / "verifier" / "reward.txt", f"{reward}\n")
    if trial_log:
        _write(trial / "trial.log", trial_log)
    input_tokens, output_tokens, cache_tokens = tokens
    _write(
        trial / "result.json",
        json.dumps(
            {
                "id": "trial-id",
                "task_name": "demo-task",
                "trial_name": "demo-task__abc",
                "task_checksum": "checksum",
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:00:02Z",
                "verifier_result": (
                    {"rewards": {"reward": reward}} if reward is not None else None
                ),
                "agent_result": {
                    "n_input_tokens": input_tokens,
                    "n_output_tokens": output_tokens,
                    "n_cache_tokens": cache_tokens,
                    "cost_usd": 0.1,
                },
                "exception_info": exception_info,
            }
        ),
    )
    return trial


def test_pi_trace_extracts_final_visible_answer(tmp_path: Path) -> None:
    trace = _write(
        tmp_path / "pi.jsonl",
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "secret"},
                                {"type": "text", "text": "first"},
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "toolResult",
                            "content": [{"type": "text", "text": "tool output"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "final answer"}],
                        },
                    }
                ),
            ]
        )
        + "\n",
    )
    trajectory, reasoning, final_answer = _read_pi_trace(trace)
    assert final_answer == "final answer"
    assert trajectory[-1]["content"] == "final answer"
    assert reasoning == [
        {"role": "assistant", "content": "secret", "type": "reasoning"}
    ]


def test_harbor_bridge_imports_frozen_trial_and_metric(
    tmp_path: Path, monkeypatch
) -> None:
    agent, jobs = _configured_agent(tmp_path)
    task = _task()

    def fake_harbor(command: list[str]) -> subprocess.CompletedProcess[str]:
        _write_harbor_trial(
            jobs,
            command,
            trace_events=[
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done now"}],
                    },
                }
            ],
            reward=1.0,
        )
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(agent, "_run_harbor", fake_harbor)
    response = agent.solve(task)
    assert response.answer == "1"
    assert response.raw_output == "done now"
    assert response.sandbox_metadata == {
        "container_engine": "docker",
        "isolation_mode": "harbor_disposable_task_container",
        "alphadiana_in_agent_runtime": False,
        "metrics_in_agent_runtime": False,
        "post_run_import_only": True,
    }
    assert (
        response.artifact_manifest["files"]["pi_jsonl"]["access"]
        == "read_only_post_run"
    )
    assert response.artifact_manifest["runtime_inputs"]["candidate_extension"]["sha256"]
    assert agent.owns_task_runtime is True
    assert agent.runtime_isolation_mode == "harbor_disposable_task_container"

    score = TerminalBench2Scorer().score(task, response)
    metric = AnswerLengthMetric()
    metric.setup({"source": "final_answer"})
    response.evaluation_metrics[metric.name] = metric.measure(
        task, response, score
    ).to_dict()
    assert response.evaluation_metrics["answer_length"]["value"] == 8

    store = ResultStore(
        str(tmp_path / "results"),
        "run",
        run_metadata={
            "agent_name": agent.name,
            "agent_version": "1.0",
            "benchmark_name": "terminal_bench2",
            "scorer_name": "terminal_bench2",
            "num_samples": 1,
        },
    )
    store.save_manifest(
        {
            "run_id": "run",
            "expected_task_count": 1,
            "expected_sample_count": 1,
            "expected_task_ids": [task.task_id],
            "num_samples": 1,
        }
    )
    store.append(task, response, score)
    record = store.load()[0]
    assert record["score"] == 1.0
    assert record["evaluation_metrics"]["answer_length"]["value"] == 8
    summary = ReportGenerator().generate(store)
    assert summary.evaluation_metrics["answer_length"] == {
        "count": 1,
        "mean": 8.0,
        "min": 8.0,
        "max": 8.0,
        "unit": "unicode_codepoints",
    }
    dashboard_summary = _build_summary("run", [record])
    assert dashboard_summary.evaluation_metrics == summary.evaluation_metrics


def test_provider_error_preserves_completed_harbor_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    agent, jobs = _configured_agent(tmp_path)

    def fake_harbor(command: list[str]) -> subprocess.CompletedProcess[str]:
        _write_harbor_trial(
            jobs,
            command,
            trace_events=[
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [],
                        "stopReason": "error",
                        "errorMessage": "OpenAI API error (429): model_cooldown",
                    },
                }
            ],
            reward=0.0,
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run_harbor", fake_harbor)
    with pytest.raises(HarborTrialError) as raised:
        agent.solve(_task())
    response = raised.value.partial_response
    assert response.metadata["failure_reason"] == "provider_error"
    assert response.metadata["verifier_reward"] == 0.0
    assert response.artifact_manifest["files"]["pi_jsonl"]["sha256"]


def test_official_agent_timeout_remains_scored_zero(
    tmp_path: Path, monkeypatch
) -> None:
    agent, jobs = _configured_agent(tmp_path)

    def fake_harbor(command: list[str]) -> subprocess.CompletedProcess[str]:
        _write_harbor_trial(
            jobs,
            command,
            trace_events=[],
            reward=0.0,
            exception_info={
                "exception_type": "AgentTimeoutError",
                "exception_message": "agent timed out",
            },
            tokens=(0, 0, 0),
            write_audit=False,
        )
        return subprocess.CompletedProcess(command, 1, "", "timed out")

    monkeypatch.setattr(agent, "_run_harbor", fake_harbor)
    response = agent.solve(_task())
    score = TerminalBench2Scorer().score(_task(), response)
    assert response.finish_reason == "timeout"
    assert response.metadata["request_audit_status"] == "no_requests_before_timeout"
    assert score.score == 0.0
    assert score.correct is False
    store = ResultStore(
        str(tmp_path / "timeout-results"),
        "timeout",
        run_metadata={
            "benchmark_name": "terminal_bench2",
        },
    )
    store.append(_task(), response, score)
    assert store.load()[0]["score_status"] == "valid_scored"


def test_configured_missing_verifier_reward_is_observable_scored_zero(
    tmp_path: Path, monkeypatch
) -> None:
    """Keep missing-reward trials in the denominator only when configured."""
    agent, jobs = _configured_agent(tmp_path)

    def fake_harbor(command: list[str]) -> subprocess.CompletedProcess[str]:
        trial = _write_harbor_trial(
            jobs,
            command,
            trace_events=[
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "partial answer"}],
                    },
                }
            ],
            reward=None,
            exception_info={
                "exception_type": "AgentTimeoutError",
                "exception_message": "agent timed out",
            },
            trial_log="harbor.trial.errors.VerifierTimeoutError: verifier timed out\n",
        )
        _write(trial / "verifier" / "test-stdout.txt", "..F")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run_harbor", fake_harbor)
    response = agent.solve(_task())
    score = TerminalBench2Scorer().score(_task(), response)

    assert score.score == 0.0
    assert score.correct is False
    assert response.metadata["verifier_reward_observed"] is False
    assert response.metadata["verifier_reward"] is None
    assert response.metadata["verifier_status"] == "ok"
    assert response.metadata["harbor_verifier_status"] == (
        "missing_reward_normalized_zero"
    )
    assert response.metadata["normalized_reward"] == 0.0
    assert response.metadata["reward_normalization"] == (
        "configured_missing_reward_zero"
    )
    assert response.metadata["harbor_verifier_timeout_observed"] is True
    assert "verifier_reward" not in response.artifact_manifest["files"]
    assert response.artifact_manifest["files"]["verifier_stdout"]["sha256"]
    assert response.artifact_manifest["files"]["harbor_trial_log"]["sha256"]

    store = ResultStore(
        str(tmp_path / "missing-reward-results"),
        "missing-reward",
        run_metadata={"benchmark_name": "terminal_bench2"},
    )
    store.append(_task(), response, score)
    record = store.load()[0]
    assert record["score_status"] == "valid_scored"
    assert record["score"] == 0.0


def test_missing_verifier_reward_fails_closed_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    agent, jobs = _configured_agent(tmp_path, missing_reward_policy="error")

    def fake_harbor(command: list[str]) -> subprocess.CompletedProcess[str]:
        _write_harbor_trial(
            jobs,
            command,
            trace_events=[
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                    },
                }
            ],
            reward=None,
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run_harbor", fake_harbor)
    with pytest.raises(HarborTrialError, match="finite numeric verifier reward") as raised:
        agent.solve(_task())
    response = raised.value.partial_response
    assert response.metadata["failure_reason"] == "missing_verifier_reward"
    assert response.metadata["verifier_status"] == "error"
    assert response.metadata["reward_normalization"] == "missing_reward_error"


def test_config_parses_and_validates_metrics(tmp_path: Path) -> None:
    pi_install = tmp_path / "pi-install"
    pi_install.mkdir()
    _write(pi_install / "pi", "#!/usr/bin/env node\n")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    node = _write(tmp_path / "node")
    models = _write(tmp_path / "models.json", "{}\n")
    config_path = _write(
        tmp_path / "run.yaml",
        f"""
agent:
  name: terminal_bench2_pi_harbor
  version: '1.0'
  config:
    harbor_bin: /bin/true
    pi_install_path: {pi_install}
    node_bin_path: {node}
    models_path: {models}
    pi_model: local/model
    harbor_model: local/model
benchmark:
  name: terminal_bench2
  config:
    tasks_dir: {tasks}
scorer:
  name: terminal_bench2
metrics:
  - name: answer_length
    config:
      source: final_answer
""",
    )
    config = ExperimentConfig.from_yaml(str(config_path))
    assert config.metrics == [
        {
            "name": "answer_length",
            "config": {"source": "final_answer"},
        }
    ]
    assert ConfigValidator().validate(config) == []


def test_harbor_task_index_does_not_require_local_runtime_config(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "tasks" / "demo-task"
    _write(task_dir / "instruction.md", "Do the demo task.\n")
    tasks = TerminalBench2Benchmark().load_tasks(
        {
            "tasks_dir": str(tmp_path / "tasks"),
            "harbor_task_index": True,
            "task_ids": ["demo-task"],
        }
    )
    assert [(task.task_id, task.problem) for task in tasks] == [
        ("tb2_demo-task", "Do the demo task.\n"),
    ]
    assert tasks[0].metadata["runtime_owner"] == "harbor"


def test_each_solve_attempt_gets_a_fresh_harbor_job_name(tmp_path: Path) -> None:
    agent, _ = _configured_agent(tmp_path)
    first = agent._job_name(_task())
    second = agent._job_name(_task())
    assert first != second
    assert first.startswith("alphadiana-pi-demo-task-s0-abcdef012345-")


def test_request_audit_rejects_wrong_model(tmp_path: Path) -> None:
    audit = _write(
        tmp_path / "request-audit.jsonl",
        json.dumps(
            {
                "model": "wrong",
                "reasoning": {"effort": "max"},
            }
        )
        + "\n",
    )
    assert "expected model='model'" in _request_audit_error(audit, "model", "max")


def test_models_config_rejects_explicitly_unsupported_reasoning(tmp_path: Path) -> None:
    models = _write(
        tmp_path / "models.json",
        json.dumps(
            {
                "providers": {
                    "local": {
                        "models": [
                            {
                                "id": "model",
                                "thinkingLevelMap": {"low": None, "max": "max"},
                            }
                        ]
                    }
                },
            }
        ),
    )
    with pytest.raises(
        ValueError, match="explicitly marks reasoning level 'low' unsupported"
    ):
        _provider_reasoning_effort(models, "local/model", "low")
    assert _provider_reasoning_effort(models, "local/model", "max") == "max"


def test_config_validator_uses_agent_runtime_capability(
    tmp_path: Path,
) -> None:
    config = ExperimentConfig(
        agent_name="terminal_bench2_pi_harbor",
        agent_version="1.0",
        benchmark_name="terminal_bench2",
        scorer_name="terminal_bench2",
        sandbox_name="local",
        agent_config={
            "pi_model": "local/model",
            "harbor_model": "local/model",
        },
        benchmark_config={"tasks_dir": str(tmp_path)},
    )
    errors = ConfigValidator().validate(config)
    assert any("owns its task runtime" in error for error in errors)
