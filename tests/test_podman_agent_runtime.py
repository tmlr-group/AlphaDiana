from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alphadiana.container_runtime import (
    PodmanAgentRuntime,
    PodmanAgentRuntimeError,
    PodmanAgentSpec,
    PodmanResult,
    RuntimeFile,
)


class FakePodmanRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.files: dict[str, str] = {}
        self.container_id = "fake-container"
        self.port_output = "8080/tcp -> 0.0.0.0:18080\n"

    def run(self, image: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("run", {"image": image, **kwargs}))
        return PodmanResult(("podman", "run"), 0, f"{self.container_id}\n", "")

    def exec(self, container_id: str, command: list[str], **kwargs: Any) -> PodmanResult:
        self.calls.append(("exec", {"container_id": container_id, "command": command, **kwargs}))
        shell_command = command[-1] if command[:2] == ["/bin/sh", "-lc"] else ""
        if "cat " in shell_command:
            path = shell_command.split("cat ", 1)[1].split(" || ", 1)[0].strip("'\"")
            return PodmanResult(("podman", "exec"), 0, self.files.get(path, ""), "")
        if "echo $!" in shell_command:
            return PodmanResult(("podman", "exec"), 0, "4242\n", "")
        return PodmanResult(("podman", "exec"), 0, "", "")

    def cp(self, src: str, dst: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("cp", {"src": src, "dst": dst, **kwargs}))
        _, container_path = dst.split(":", 1)
        self.files[container_path] = Path(src).read_text(encoding="utf-8")
        return PodmanResult(("podman", "cp"), 0, "", "")

    def logs(self, container_id: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("logs", {"container_id": container_id, **kwargs}))
        return PodmanResult(("podman", "logs"), 0, "container log line\n", "")

    def port(self, container_id: str, private_port: int | str | None = None, **kwargs: Any) -> PodmanResult:
        self.calls.append(("port", {"container_id": container_id, "private_port": private_port, **kwargs}))
        return PodmanResult(("podman", "port"), 0, self.port_output, "")

    def stop(self, container_id: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("stop", {"container_id": container_id, **kwargs}))
        return PodmanResult(("podman", "stop"), 0, "", "")

    def rm(self, container_id: str, **kwargs: Any) -> PodmanResult:
        self.calls.append(("rm", {"container_id": container_id, **kwargs}))
        return PodmanResult(("podman", "rm"), 0, "", "")


def test_podman_agent_runtime_starts_collects_and_cleans_up() -> None:
    fake = FakePodmanRuntime()
    fake.files["/tmp/runtime-artifact.txt"] = "agent artifact\n"
    runtime = PodmanAgentRuntime(runtime=fake)  # type: ignore[arg-type]
    spec = PodmanAgentSpec(
        adapter_name="openclaw",
        image="localhost/openclaw:test",
        workdir="/workspace",
        env={"OPENAI_BASE_URL": "http://provider/v1"},
        install_commands=("echo install",),
        run_command="python -m openclaw.gateway",
        exposed_port=8080,
        files=(
            RuntimeFile(
                "/tmp/openclaw/config.json",
                '{"api_key":"secret","nested":{"authorization":"bearer secret","token":"tok"}}',
            ),
        ),
        safe_config_paths=("/tmp/openclaw/config.json",),
        artifact_paths=("/tmp/runtime-artifact.txt",),
        healthcheck=lambda api_base: api_base == "http://127.0.0.1:18080/v1",
        metadata={"api_key": "secret", "mode": "podman"},
    )

    result = runtime.start(spec)

    assert result.container_id == "fake-container"
    assert result.process_id == "4242"
    assert result.api_base == "http://127.0.0.1:18080/v1"
    assert result.metadata["container_engine"] == "podman"
    assert result.metadata["adapter_name"] == "openclaw"
    assert result.metadata["image"] == "localhost/openclaw:test"
    assert result.metadata["runtime_container_id"] == "fake-container"
    assert result.metadata["published_api_base"] == "http://127.0.0.1:18080/v1"
    assert result.metadata["api_key"] == "REDACTED"
    assert "container log line" in result.logs
    assert result.artifacts["/tmp/runtime-artifact.txt"] == "agent artifact\n"
    assert "secret" not in result.config_snapshots["/tmp/openclaw/config.json"]
    assert "REDACTED" in result.config_snapshots["/tmp/openclaw/config.json"]

    call_names = [name for name, _ in fake.calls]
    assert call_names[:2] == ["run", "exec"]
    assert "cp" in call_names
    assert any(name == "exec" and payload["command"] == ["/bin/sh", "-lc", "echo install"] for name, payload in fake.calls)
    assert any(name == "port" and payload["private_port"] == 8080 for name, payload in fake.calls)

    runtime.cleanup()

    assert [name for name, _ in fake.calls][-2:] == ["stop", "rm"]


def test_podman_agent_runtime_healthcheck_failure_payload_and_cleanup() -> None:
    fake = FakePodmanRuntime()
    runtime = PodmanAgentRuntime(runtime=fake)  # type: ignore[arg-type]
    spec = PodmanAgentSpec(
        adapter_name="zeroclaw",
        image="localhost/zeroclaw:test",
        run_command="zeroclaw serve",
        exposed_port=8080,
        startup_timeout=0.15,
        healthcheck=lambda _api_base: False,
        metadata={"token": "secret-token"},
    )

    with pytest.raises(PodmanAgentRuntimeError) as exc_info:
        runtime.start(spec)

    payload = exc_info.value.payload
    assert payload["stage"] == "healthcheck"
    assert payload["adapter_name"] == "zeroclaw"
    assert payload["container_id"] == "fake-container"
    assert payload["command"] == "zeroclaw serve"
    assert payload["port_output"] == fake.port_output
    assert "container log line" in payload["logs"]
    assert payload["metadata"]["container_engine"] == "podman"
    assert payload["metadata"]["token"] == "REDACTED"
    assert [name for name, _ in fake.calls][-2:] == ["stop", "rm"]
