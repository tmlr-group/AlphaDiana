"""Upstream result contracts fail closed before benchmark scoring."""

import json
from pathlib import Path

import pytest

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


def test_sweagent_image_override_and_solver_visible_inputs(tmp_path, monkeypatch):
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
            "output_dir": str(tmp_path / "out"),
            "image_overrides": {"example": "owned-image@sha256:fixture"},
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
    from alphadiana.benchmarks.terminal_bench2.harness.openclaw import (
        _classify_openclaw_failure,
    )
    from alphadiana.analysis.io.status import infer_score_status

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
