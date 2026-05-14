from __future__ import annotations

from typing import Any

from alphadiana.agent.opencode import OpenCodeAgent
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.container_runtime import PodmanError, PodmanResult


class FakePodmanRuntime:
    def __init__(self, *, timeout: bool = False) -> None:
        self.timeout = timeout
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.container_id = "opencode-podman-cid"

    def run(self, image: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("run", {"image": image, **kwargs}))
        return PodmanResult(("podman", "run"), 0, f"{self.container_id}\n", "")

    def exec(self, container_id: str, command: list[str], **kwargs: Any) -> PodmanResult:
        self.calls.append(("exec", {"container_id": container_id, "command": command, **kwargs}))
        if self.timeout:
            raise PodmanError(
                PodmanResult(
                    ("podman", "exec"),
                    124,
                    "",
                    "podman command timed out after 5s",
                )
            )
        return PodmanResult(
            ("podman", "exec"),
            0,
            '{"type":"assistant","message":{"content":"Done.\\\\boxed{A}"}}\n',
            "",
        )

    def logs(self, container_id: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("logs", {"container_id": container_id, **kwargs}))
        return PodmanResult(("podman", "logs"), 0, "opencode controller log\n", "")

    def stop(self, container_id: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("stop", {"container_id": container_id, **kwargs}))
        return PodmanResult(("podman", "stop"), 0, "", "")

    def rm(self, container_id: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("rm", {"container_id": container_id, **kwargs}))
        return PodmanResult(("podman", "rm"), 0, "", "")


def _agent(fake_runtime: FakePodmanRuntime) -> OpenCodeAgent:
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
        "controller_mode": "podman",
        "controller_image": "localhost/opencode-controller:test",
        "controller_network": "host",
        "_podman_runtime": fake_runtime,
    })
    return agent


def test_opencode_podman_controller_runs_via_central_runtime() -> None:
    fake = FakePodmanRuntime()
    agent = _agent(fake)
    task = BenchmarkTask(task_id="text_0", problem="Which option is correct?", ground_truth="A")

    result = agent.solve(task)

    assert result.answer == "A"
    assert result.metadata["controller_mode"] == "podman"
    assert result.metadata["transport"] == "opencode_cli_podman"
    assert result.metadata["container_engine"] == "podman"
    assert result.metadata["controller_image"] == "localhost/opencode-controller:test"
    assert result.metadata["controller_container_id"] == "opencode-podman-cid"
    assert result.metadata["logprob_support"] == "disabled"
    assert result.metadata["adapter_name"] == "opencode-podman"
    assert result.metadata["container_logs_collected"] is True
    assert result.metadata["container_cleanup_status"] == "removed"
    assert result.workspace_file_contents["opencode_podman_container.log"] == "opencode controller log\n"

    run_call = fake.calls[0]
    assert run_call[0] == "run"
    assert run_call[1]["image"] == "localhost/opencode-controller:test"
    assert run_call[1]["command"] == ["sleep", "infinity"]
    assert run_call[1]["network"] == "host"
    assert any(str(item).startswith("alphadiana.component=opencode-controller") for item in run_call[1]["extra_args"])
    assert not any(str(item).startswith("--user=") for item in run_call[1]["extra_args"])

    exec_call = next(
        payload
        for name, payload in fake.calls
        if name == "exec" and payload["command"][:2] == ["node", "/usr/lib/node_modules/opencode-ai/bin/opencode"]
    )
    assert exec_call["container_id"] == "opencode-podman-cid"
    assert exec_call["command"][:2] == ["node", "/usr/lib/node_modules/opencode-ai/bin/opencode"]
    assert exec_call["env"]["OPENAI_API_KEY"] == "test-key"
    assert exec_call["env"]["OPENAI_BASE_URL"] == "http://localhost:9001/v1"
    assert [name for name, _ in fake.calls][-2:] == ["stop", "rm"]


def test_opencode_podman_controller_timeout_cleans_up() -> None:
    fake = FakePodmanRuntime(timeout=True)
    agent = _agent(fake)
    task = BenchmarkTask(task_id="text_timeout", problem="Which option is correct?", ground_truth="A")

    result = agent.solve(task)

    assert result.answer is None
    assert result.finish_reason == "timeout"
    assert result.metadata["returncode"] == -1
    assert result.metadata["opencode_timeout_scored_zero"] is True
    assert result.metadata["transport"] == "opencode_cli_podman"
    assert result.metadata["container_cleanup_status"] == "removed"
    assert [name for name, _ in fake.calls][-2:] == ["stop", "rm"]


def test_setup_accepts_podman_and_rejects_unknown_controller_mode() -> None:
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "controller_mode": "podman",
        "_podman_runtime": FakePodmanRuntime(),
    })

    bad_agent = OpenCodeAgent()
    try:
        bad_agent.setup({
            "model_name": "test-model",
            "api_base": "http://localhost:9001/v1",
            "api_key": "test-key",
            "controller_mode": "doccker",
        })
    except ValueError as exc:
        assert "Unsupported opencode controller_mode" in str(exc)
    else:
        raise AssertionError("Expected unknown controller mode to fail")


def test_setup_uses_podman_only_controller_image_by_default() -> None:
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "controller_mode": "podman",
        "_podman_runtime": FakePodmanRuntime(),
    })

    assert agent._controller_image == "alphadiana-opencode-podman:latest"
