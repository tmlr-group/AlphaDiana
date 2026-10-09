"""Upstream result contracts fail closed before benchmark scoring."""

import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("reference", ["sha256:" + "a" * 64, "runtime:v1"])
def test_tb2_docker_build_uses_local_identity_as_a_valid_from_reference(
    monkeypatch, reference
):
    import subprocess
    from alphadiana.benchmarks.terminal_bench2.harness import incontainer

    aliases = {}
    commands = []

    def docker(command, **kwargs):
        commands.append(command)
        if command[:3] == ["docker", "image", "tag"]:
            aliases[command[4]] = command[3]
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["docker", "build"]:
            argument = next(value for value in command if value.startswith("RUNTIME_IMAGE="))
            resolved = argument.split("=", 1)[1]
            if resolved.startswith("sha256:"):
                return subprocess.CompletedProcess(command, 1, "", "BuildKit interpreted a local image ID as a registry repository")
            assert aliases.get(resolved, resolved) == reference
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(incontainer.subprocess, "run", docker)
    arguments = {"RUNTIME_IMAGE": reference}
    incontainer.TerminalBench2InContainerMixin()._docker_build_image(
        "owned-test-runtime", "ARG RUNTIME_IMAGE\nFROM ${RUNTIME_IMAGE}",
        build_args=arguments,
    )
    assert arguments == {"RUNTIME_IMAGE": reference}
    assert bool(aliases) == reference.startswith("sha256:")

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.benchmarks.terminal_bench2.harness import terminus


@pytest.mark.parametrize("reward", [0, 1, 0.5, True, None])
def test_harbor_requires_binary_observed_reward(tmp_path, monkeypatch, reward):
    task_dir = tmp_path / "fixture"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text('version = "1.0"')
    task = BenchmarkTask("fixture", "Solve", None, {"task_dir": str(task_dir)})

    def native(argv, **kwargs):
        job = json.loads(Path(argv[3]).read_text())
        assert job["agents"][0]["name"] == "terminus-2"
        assert job["retry"]["max_retries"] == 0
        assert job["agents"][0]["kwargs"]["max_turns"] == 30
        assert job["agents"][0]["kwargs"]["model_info"] == {"max_input_tokens": 32768}
        assert job["agents"][0]["kwargs"]["llm_call_kwargs"] == {
            "max_tokens": 512,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        result_dir = kwargs["cwd"] / "evaluation/fixture_trial"
        result_dir.mkdir(parents=True)
        (result_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_name": "fixture",
                    "exception_info": None,
                    "verifier_result": {"rewards": {"reward": reward}},
                    "agent_result": {"n_output_tokens": 10},
                }
            )
        )

    monkeypatch.setattr(terminus, "run_native", native)
    agent = terminus.Terminus2Agent()
    agent.setup(
        {
            "executable": "harbor",
            "max_turns": 30,
            "model_info": {"max_input_tokens": 32768},
            "model": "fixture-model",
            "api_base": "http://localhost:1/v1",
            "output_dir": str(tmp_path / "out"),
            "llm_call_kwargs": {
                "max_tokens": 512,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            },
        }
    )
    if reward is None or isinstance(reward, bool) or reward == 0.5:
        with pytest.raises(ValueError, match="binary"):
            agent.solve(task)
    else:
        response = agent.solve(task)
        assert response.answer == str(reward)
        assert response.token_usage["completion_tokens"] == 10


@pytest.mark.parametrize("harness", ["openclaw", "opencode"])
def test_tb2_generation_settings_reach_provider_overrides(tmp_path, harness):
    from alphadiana.benchmarks.terminal_bench2.harness.openclaw import (
        TerminalBench2OpenClawAgent,
    )
    from alphadiana.benchmarks.terminal_bench2.harness.opencode import (
        TerminalBench2OpenCodeAgent,
    )

    cls = {
        "openclaw": TerminalBench2OpenClawAgent,
        "opencode": TerminalBench2OpenCodeAgent,
    }[harness]
    agent = cls()
    config = {
        "logs_base_dir": str(tmp_path),
        "model_name": "fixture-model",
        "api_base": "http://localhost:1/v1",
        "max_tokens": 512,
        "temperature": 0.0,
        "enable_thinking": False,
        "capture_logprobs": False,
    }
    agent.setup(config)
    assert agent._provider_request_overrides() == {
        "max_tokens": 512,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    with pytest.raises(ValueError, match="boolean"):
        cls().setup({**config, "enable_thinking": "false"})


def test_openclaw_install_version_is_explicit_and_override_is_preserved():
    from alphadiana.harness.openclaw.container_runtime import (
        OpenClawContainerRuntimeManager,
    )

    manager = OpenClawContainerRuntimeManager({"openclaw_version": "2026.3.2"})
    assert "openclaw@2026.3.2" in manager._install_openclaw_command
    assert "openclaw@2026.3.14" not in manager._install_openclaw_command
    manager = OpenClawContainerRuntimeManager(
        {"install_openclaw_command": "custom installer"}
    )
    assert manager._install_openclaw_command == "custom installer"


@pytest.mark.parametrize("history_processors", [None, []])
def test_sweagent_image_override_and_solver_visible_inputs(tmp_path, monkeypatch, history_processors):
    from alphadiana.benchmarks.swe_bench import sweagent

    config_path = tmp_path / "default.yaml"
    config_path.write_text("agent: {}")
    task = BenchmarkTask(
        "swe_example",
        "Fix the bug",
        None,
        {
            "instance_id": "example",
            "base_commit": "pinned-base",
            "patch": "REFERENCE PATCH",
            "test_patch": "REFERENCE TEST",
        },
    )

    def native(argv, **kwargs):
        sampling = Path(argv[argv.index("--output_dir") + 1]).parent / "model-sampling.json"
        assert sampling.is_file()
        assert json.loads(sampling.read_text()) == {"agent": {"model": {"top_p": None}}}
        assert argv[argv.index(str(sampling)) - 1] == "--config"
        history = sampling.parent / "history-processors.json"
        if history_processors is not None:
            assert json.loads(history.read_text()) == {"agent": {"history_processors": history_processors}}
            assert argv[argv.index(str(history)) - 1] == "--config"
        else:
            assert not history.exists()
        instances = Path(argv[argv.index("--instances.path") + 1])
        data = json.loads(instances.read_text())
        assert data == [
            {
                "instance_id": "example",
                "problem_statement": "Fix the bug",
                "image_name": "owned-image@sha256:fixture",
                "repo_name": "testbed",
                "base_commit": "pinned-base",
            }
        ]
        output = Path(argv[argv.index("--output_dir") + 1])
        output.mkdir()
        (output / "preds.json").write_text(
            json.dumps({"example": {"model_patch": "predicted diff"}})
        )

    monkeypatch.setattr(sweagent, "run_native", native)
    agent = sweagent.SWEAgent()
    agent.setup(
        {
            "executable": "sweagent",
            "config_path": str(config_path),
            "model": "fixture",
            "api_base": "http://localhost:1/v1",
            "top_p": None,
            "output_dir": str(tmp_path / "out"),
            "image_overrides": {"example": "owned-image@sha256:fixture"},
            **({"history_processors": history_processors} if history_processors is not None else {}),
        }
    )
    response = agent.solve(task)
    assert response.answer == "predicted diff"
    assert response.metadata["native_image"] == "owned-image@sha256:fixture"


def test_opencode_context_limits_reach_native_model_config(tmp_path):
    from alphadiana.benchmarks.terminal_bench2.harness.opencode import (
        TerminalBench2OpenCodeAgent,
    )

    agent = TerminalBench2OpenCodeAgent()
    agent.setup({"model_name": "fixture", "context_limit": 32768, "output_limit": 8192})
    path = tmp_path / "provider.json"
    agent._write_provider_config(path)
    model = json.loads(path.read_text())["provider"]["custom"]["models"]["fixture"]
    assert model["limit"] == {"context": 32768, "output": 8192}


def test_sweagent_native_exit_error_retains_evidence_and_is_not_scored(tmp_path, monkeypatch):
    from alphadiana.benchmarks.swe_bench import sweagent
    from alphadiana.analysis.io.status import infer_score_status

    # Captured on A100: upstream run-batch exited zero, emitted an empty
    # prediction, and recorded UnsupportedParamsError before any API call.
    trajectory = json.loads(
        (Path(__file__).parent / "fixtures/sweagent-unsupported-params.json").read_text()
    )
    config_path = tmp_path / "default.yaml"
    config_path.write_text("agent: {}")
    task = BenchmarkTask("swe_example", "Fix", None, {"instance_id": "example", "base_commit": "base"})

    def native(argv, **kwargs):
        output = Path(argv[argv.index("--output_dir") + 1])
        (output / "example").mkdir(parents=True)
        (output / "preds.json").write_text(json.dumps({"example": {"model_patch": ""}}))
        (output / "example/example.traj").write_text(json.dumps(trajectory))

    monkeypatch.setattr(sweagent, "run_native", native)
    agent = sweagent.SWEAgent()
    agent.setup({"executable": "sweagent", "config_path": str(config_path), "model": "fixture", "api_base": "http://localhost:1/v1", "output_dir": str(tmp_path / "out")})
    with pytest.raises(RuntimeError, match="exit_error") as caught:
        agent.solve(task)
    response = caught.value.partial_response
    assert response.metadata["exit_status"] == "exit_error"
    assert "UnsupportedParamsError" in response.workspace_file_contents["sweagent_trajectory.json"]
    assert response.metadata["token_usage_provenance"]["requests"] == 0
    assert infer_score_status({"benchmark_name": "swe_bench", "metadata": response.metadata, "error": {"error_type": caught.value.error_type}}) == "runtime_error"


@pytest.mark.parametrize("status,expected", [("exit_error", "runtime_error"), ("exit_api", "provider_error"), ("exit_environment_error", "runtime_error"), ("submitted", "valid_scored"), ("exit_format", "valid_scored")])
def test_saved_sweagent_native_failure_overrides_legacy_valid_score(status, expected):
    from alphadiana.analysis.io.status import infer_score_status

    assert infer_score_status({"benchmark_name": "swe_bench", "score_status": "valid_scored", "score": 0, "correct": False, "metadata": {"native_runtime": "sweagent", "exit_status": status}}) == expected


@pytest.mark.parametrize("configured", [None, 240])
def test_tb2_docker_start_accepts_observed_startup_over_sixty_seconds(tmp_path, monkeypatch, configured):
    import subprocess
    from alphadiana.benchmarks.terminal_bench2.harness import common

    def docker(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            if kwargs["timeout"] <= 61:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            assert kwargs["timeout"] == (180 if configured is None else configured)
            return subprocess.CompletedProcess(command, 0, "a" * 64, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(common.subprocess, "run", docker)
    owner = common.TerminalBench2ContainerMixin()
    owner._setup_container_config({} if configured is None else {"docker_start_timeout_sec": configured})
    assert owner._start_container("fixture", tmp_path, BenchmarkTask("fixture", "Solve", None)) == "a" * 64


def test_tb2_failed_start_removes_only_the_container_recorded_by_docker(tmp_path, monkeypatch):
    import subprocess
    from alphadiana.benchmarks.terminal_bench2.harness import common

    removed = []
    def docker(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            cidfile = Path(command[command.index("--cidfile") + 1])
            cidfile.write_text("b" * 64)
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        assert command == ["docker", "rm", "-f", "b" * 64]
        removed.append(command[-1])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(common.subprocess, "run", docker)
    owner = common.TerminalBench2ContainerMixin()
    owner._setup_container_config({})
    with pytest.raises(subprocess.TimeoutExpired):
        owner._start_container("fixture", tmp_path, BenchmarkTask("fixture", "Solve", None))
    assert removed == ["b" * 64]


def test_tb2_startup_timeout_stays_runtime_failure_when_normalized_twice():
    from alphadiana.analysis.io.status import infer_score_status, normalize_legacy_timeout_zero_record

    captured = json.loads((Path(__file__).parent / "fixtures/tb2-startup-timeout.json").read_text())
    first = dict(normalize_legacy_timeout_zero_record(captured))
    first["score_status"] = infer_score_status(first)
    second = dict(normalize_legacy_timeout_zero_record(first))
    second["score_status"] = infer_score_status(second)
    assert first["score_status"] == "runtime_error"
    assert second == first


def test_reopened_tb2_startup_timeout_has_no_measured_score(tmp_path):
    from alphadiana.artifacts import Artifacts
    from alphadiana.analysis.run_summary import RunStatistics

    path = tmp_path / "captured.jsonl"
    captured = json.loads((Path(__file__).parent / "fixtures/tb2-startup-timeout.json").read_text())
    path.write_text(json.dumps(captured) + "\n")
    row = Artifacts(path).records()[0]
    assert row["score_status"] == "runtime_error" and row["score"] is None
    assert row["error"] == captured["error"]
    stats = RunStatistics.from_records([row])
    assert stats.valid == stats.metric_results == []


@pytest.mark.parametrize(
    "stderr,returncode,expected",
    [
        ('FailoverError: No API key found for provider "custom".', 1, "provider_error"),
        ("unexpected native CLI failure", 1, "control_plane_unavailable"),
        ("", 1, "control_plane_unavailable"),
        ("", 0, ""),
    ],
)
def test_openclaw_native_failure_cannot_be_scored_as_verifier_zero(
    stderr, returncode, expected
):
    from alphadiana.analysis.io.status import infer_score_status
    from alphadiana.benchmarks.terminal_bench2.harness.openclaw import (
        _classify_openclaw_failure,
    )

    failure = _classify_openclaw_failure("", stderr, returncode)
    assert failure == expected
    row = {
        "benchmark_name": "terminal_bench2",
        "score_status": "valid_scored",
        "score": 0,
        "correct": False,
        "metadata": {
            "failure_reason": failure,
            "verifier_status": "ok",
            "verifier_reward_observed": True,
        },
    }
    if failure:
        assert infer_score_status(row) != "valid_scored"


def test_harbor_snapshot_requires_explicit_native_python(tmp_path):
    agent = terminus.Terminus2Agent()
    with pytest.raises(ValueError, match="python_executable"):
        agent.setup(
            {
                "retain_verifier_snapshot": True,
                "executable": "harbor",
                "model": "fixture",
                "api_base": "http://localhost:1/v1",
                "output_dir": str(tmp_path),
            }
        )


def test_harbor_snapshot_hook_refuses_ambiguous_container(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from alphadiana.benchmarks.terminal_bench2.harness import harbor_job

    event = SimpleNamespace(
        task_name=tmp_path.name,
        config=SimpleNamespace(task=SimpleNamespace(path=tmp_path)),
    )
    monkeypatch.setattr(
        harbor_job.subprocess, "check_output", lambda *a, **k: "first\nsecond\n"
    )
    with pytest.raises(RuntimeError, match="one owned"):
        harbor_job.snapshot_trial(
            event,
            project="owned",
            task_id="fixture",
            destination=tmp_path / "snapshot.json",
        )
    assert not (tmp_path / "snapshot.json").exists()


@pytest.mark.parametrize(
    "exception_type,has_timing,accepted",
    [
        ("AgentTimeoutError", True, True),
        ("AgentTimeoutError", False, False),
        ("AgentSetupTimeoutError", True, False),
        ("EnvironmentStartTimeoutError", True, False),
        ("NonZeroAgentExitCodeError", True, False),
    ],
)
def test_harbor_only_owned_agent_deadline_can_use_observed_reward(
    tmp_path, monkeypatch, exception_type, has_timing, accepted
):
    task_dir = tmp_path / "fixture"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text('version = "1.0"')
    task = BenchmarkTask("fixture", "Solve", None, {"task_dir": str(task_dir)})

    def native(argv, **kwargs):
        result_dir = kwargs["cwd"] / "evaluation/trial"
        result_dir.mkdir(parents=True)
        result = {
            "task_name": "fixture",
            "exception_info": {"exception_type": exception_type},
            "verifier_result": {"rewards": {"reward": 0}},
            "agent_execution": {"started_at": "start", "finished_at": "end"}
            if has_timing
            else None,
        }
        (result_dir / "result.json").write_text(json.dumps(result))

    monkeypatch.setattr(terminus, "run_native", native)
    agent = terminus.Terminus2Agent()
    agent.setup(
        {
            "executable": "harbor",
            "model": "fixture",
            "api_base": "http://localhost:1/v1",
            "output_dir": str(tmp_path / "out"),
        }
    )
    if accepted:
        response = agent.solve(task)
        assert response.finish_reason == "timeout"
        assert response.metadata["verifier_reward_observed"]
        assert response.metadata["native_exception"]["exception_type"] == exception_type
    else:
        with pytest.raises(RuntimeError, match="trial failed"):
            agent.solve(task)
