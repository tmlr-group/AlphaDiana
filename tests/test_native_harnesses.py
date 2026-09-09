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


@pytest.mark.parametrize("agent_class", [PiAgent, DeepSeekHarnessAgent])
def test_public_task_stdin_and_container_contract(monkeypatch, agent_class):
    calls = []

    class Runtime:
        def rootless(self):
            return True

        def image_exists(self, image):
            return image == IMAGE

        def run(self, image, **kwargs):
            calls.append(kwargs)
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
    assert calls[1][1] == {"force": True, "check": True}


def test_runtime_failure_is_not_scored_and_removes_container(monkeypatch):
    removed = []

    class Runtime:
        def rootless(self):
            return True

        def image_exists(self, image):
            return True

        def run(self, *args, **kwargs):
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


def test_teardown_does_not_remove_sibling_samples(monkeypatch):
    removed = []

    class Runtime:
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
