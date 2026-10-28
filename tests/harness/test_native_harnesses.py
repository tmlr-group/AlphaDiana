"""Native harness contracts; real container checks are in test_native_runtime.py."""

import json
from types import SimpleNamespace

import pytest
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness._internal import podman_agent as native
from alphadiana.harness.deepseek_harness.agent import DeepSeekHarnessAgent
from alphadiana.harness.pi.agent import PiAgent

IMAGE = "sha256:" + "a" * 64
CONFIG = {"image": IMAGE, "model": "fixture-v1", "api_base": "http://model.invalid/v1"}


@pytest.mark.parametrize(
    "patch",
    [
        {"image": "latest"},
        {"runtime": "local"},
        {"timeout": float("nan")},
        {"timeout": True},
        {"max_tokens": 0},
        {"context_window": 0},
        {"context_window": True},
        {"context_window": 4096},
        {"candidate_root": "/tmp"},
        {"shell_command": "echo forged"},
        {"_execution_id": "unsafe"},
    ],
)
def test_reject_unsupported_runtime_and_configuration(patch):
    with pytest.raises((ValueError, TypeError)):
        native.validate_config({**CONFIG, **patch})


def test_candidate_digest_and_symlinks(tmp_path):
    source = tmp_path / "plugin.ts"
    source.write_text("export const name = 'fixture';")
    config = {
        **CONFIG,
        "candidate_root": str(tmp_path),
        "candidate_entry": "plugin.ts",
        "candidate_digest": native.tree_digest(tmp_path),
    }
    native.validate_config(config)
    source.write_text("changed")
    with pytest.raises(ValueError, match="identity"):
        native.validate_config(config)
    source.unlink()
    source.symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlinks"):
        native.tree_digest(tmp_path)


def test_explicit_context_window_reserves_prompt_tokens():
    native.validate_config({**CONFIG, "context_window": 49152, "max_tokens": 32768})
    with pytest.raises(ValueError, match="leave room"):
        native.validate_config({**CONFIG, "context_window": 32768, "max_tokens": 32768})


def test_deepseek_rejects_pi_window_before_runtime_start(monkeypatch):
    def unexpected_runtime():
        pytest.fail("Unsupported configuration must fail before creating a runtime")

    monkeypatch.setattr(native, "PodmanCLI", unexpected_runtime)
    with pytest.raises(ValueError, match="supported only by Pi"):
        DeepSeekHarnessAgent().setup({**CONFIG, "context_window": 49152})


def test_deepseek_does_not_inherit_pi_default_window():
    native.validate_config({**CONFIG, "max_tokens": 128000}, harness="deepseek_harness")
    with pytest.raises(ValueError, match="leave room"):
        native.validate_config({**CONFIG, "max_tokens": 128000}, harness="pi")


@pytest.mark.parametrize("agent_name", ["pi", "deepseek_harness"])
def test_experiment_validation_applies_harness_window_scope(agent_name):
    from alphadiana.engine.config.experiment_config import ExperimentConfig
    from alphadiana.engine.config.validator import ConfigValidator

    config = ExperimentConfig(
        agent_name=agent_name, agent_version="fixture", benchmark_name="custom",
        scorer_name="numeric", agent_config={**CONFIG, "context_window": 49152},
    )
    errors = ConfigValidator().validate(config)
    assert ("context_window is supported only by Pi" in errors) == (agent_name == "deepseek_harness")


@pytest.mark.parametrize("agent_class", [PiAgent, DeepSeekHarnessAgent])
def test_public_task_stdin_and_container_contract(monkeypatch, agent_class):
    calls = []

    class Runtime:
        def stop(self, *args, **kwargs):
            pass

        def rootless(self):
            return True

        def image_exists(self, image):
            return image == IMAGE

        def create(self, image, **kwargs):
            calls.append(kwargs)
            return kwargs["name"]

        def start_attached(self, name, **kwargs):
            request = json.loads(kwargs["input_text"])
            assert request["problem"] == "public question"
            assert "protected-answer" not in kwargs["input_text"]
            assert "private-metadata" not in kwargs["input_text"]
            assert request["config"]["api_key"] == "secret-value"
            assert "secret-value" not in json.dumps(
                {k: v for k, v in kwargs.items() if k != "input_text"}
            )
            return SimpleNamespace(
                stdout=json.dumps({"answer": "done", "finish_reason": "completed"})
            )

        def rm(self, name, **kwargs):
            calls.append((name, kwargs))

    monkeypatch.setattr(native, "PodmanCLI", Runtime)
    agent = agent_class()
    agent.setup({**CONFIG, "api_key": "secret-value"})
    result = agent.solve(
        BenchmarkTask(
            task_id="x",
            problem="public question",
            ground_truth="protected-answer",
            metadata={"x": "private-metadata"},
        )
    )
    assert result.answer == "done"
    assert calls[0]["volumes"][0].endswith(":/input:ro")
    assert "--read-only" in calls[0]["extra_args"]
    assert calls[1][1] == {
        "force": True,
        "ignore_missing": True,
        "check": True,
        "timeout": 15,
    }


def test_runtime_failure_is_not_scored_and_removes_container(monkeypatch):
    removed = []

    class Runtime:
        def stop(self, *args, **kwargs):
            pass

        def rootless(self):
            return True

        def image_exists(self, image):
            return True

        def create(self, *args, **kwargs):
            return "owned"

        def start_attached(self, *args, **kwargs):
            raise TimeoutError("secret-value")

        def rm(self, name, **kwargs):
            removed.append(name)

    monkeypatch.setattr(native, "PodmanCLI", Runtime)
    agent = PiAgent()
    agent.setup(CONFIG)
    with pytest.raises(native.NativeHarnessError, match="timeout") as failure:
        agent.solve(BenchmarkTask(task_id="x", problem="test", ground_truth="answer"))
    assert "secret-value" not in str(failure.value)
    assert len(removed) == 1


def test_cleanup_is_scoped():
    calls = []

    class Runtime:
        def stop(self, *args, **kwargs):
            pass

        def rootless(self):
            return True

        def list_ids(self, *, labels):
            calls.append(labels)
            return ["owned"]

        def rm(self, name, **kwargs):
            calls.append(name)

    native.cleanup_execution("a" * 32, Runtime())
    assert calls == [{"alphadiana.execution": "a" * 32}, "owned"]
    with pytest.raises(ValueError):
        native.cleanup_execution("", Runtime())


@pytest.mark.parametrize("agent_class", [PiAgent, DeepSeekHarnessAgent])
def test_interrupted_solve_cleans_container_and_propagates(monkeypatch, agent_class):
    removed = []

    class Runtime:
        def rootless(self):
            return True

        def image_exists(self, image):
            return True

        def create(self, *args, **kwargs):
            return kwargs["name"]

        def start_attached(self, *args, **kwargs):
            raise KeyboardInterrupt()

        def stop(self, *args, **kwargs):
            pass

        def rm(self, name, **kwargs):
            removed.append(name)

    monkeypatch.setattr(native, "PodmanCLI", Runtime)
    agent = agent_class()
    agent.setup(CONFIG)
    with pytest.raises(KeyboardInterrupt):
        agent.solve(BenchmarkTask("cancel", "public", "private"))
    assert len(removed) == 1
    assert agent._containers == set()
    agent.teardown()
    assert len(removed) == 1


def test_teardown_does_not_remove_sibling_samples(monkeypatch):
    removed = []

    class Runtime:
        def stop(self, *args, **kwargs):
            pass

        def rootless(self):
            return True

        def image_exists(self, image):
            return True

        def rm(self, name, **kwargs):
            removed.append(name)

    monkeypatch.setattr(native, "PodmanCLI", Runtime)
    first, second = PiAgent(), PiAgent()
    for agent in (first, second):
        agent.setup({**CONFIG, "_execution_id": "b" * 32})
    first._containers.add("first-sample")
    second._containers.add("second-sample")
    first.teardown()
    assert removed == ["first-sample"]
    assert second._containers == {"second-sample"}


def test_transient_mount_cleanup_retries_but_other_failures_do_not(monkeypatch):
    from alphadiana.engine.container_runtime.podman_cli import PodmanError, PodmanResult

    calls = []

    class Runtime:
        def stop(self, *args, **kwargs):
            pass

        def rm(self, name, **kwargs):
            calls.append(name)
            if len(calls) < 2:
                raise PodmanError(
                    PodmanResult(("podman", "rm"), 125, "", "device or resource busy")
                )

    monkeypatch.setattr(native.time, "sleep", lambda _: None)
    native.remove_container(Runtime(), "owned")
    assert calls == ["owned", "owned"]

    class Broken:
        def stop(self, *args, **kwargs):
            pass

        def rm(self, *args, **kwargs):
            raise PodmanError(
                PodmanResult(("podman", "rm"), 125, "", "permission denied")
            )

    with pytest.raises(PodmanError):
        native.remove_container(Broken(), "owned")
