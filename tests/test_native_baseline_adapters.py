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
