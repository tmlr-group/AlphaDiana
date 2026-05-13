from __future__ import annotations

from pathlib import Path

import pytest

from alphadiana.container_runtime.podman_cli import PodmanResult
from alphadiana.sandbox.podman import PodmanSandbox
from alphadiana.sandbox.registry import SandboxRegistry


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def run(self, image, **kwargs):
        self.calls.append(("run", image, kwargs))
        return PodmanResult(("podman", "run"), 0, "cid123\n", "")

    def exec(self, container_id, command, **kwargs):
        self.calls.append(("exec", container_id, command, kwargs))
        return PodmanResult(("podman", "exec"), 7, "out", "err")

    def cp(self, src, dst, **kwargs):
        self.calls.append(("cp", src, dst, kwargs))
        if str(src).startswith("cid123:"):
            Path(dst).write_bytes(b"downloaded")
        return PodmanResult(("podman", "cp"), 0, "", "")

    def logs(self, container_id, **kwargs):
        self.calls.append(("logs", container_id, kwargs))
        return PodmanResult(("podman", "logs"), 0, "log text", "")

    def port(self, container_id, private_port=None, **kwargs):
        self.calls.append(("port", container_id, private_port, kwargs))
        return PodmanResult(("podman", "port"), 0, "8080/tcp -> 0.0.0.0:18080\n", "")

    def stop(self, container_id, **kwargs):
        self.calls.append(("stop", container_id, kwargs))
        return PodmanResult(("podman", "stop"), 0, "", "")

    def rm(self, container_id, **kwargs):
        self.calls.append(("rm", container_id, kwargs))
        return PodmanResult(("podman", "rm"), 0, "", "")


def _session(runtime: FakeRuntime | None = None):
    runtime = runtime or FakeRuntime()
    sandbox = PodmanSandbox()
    sandbox.setup({
        "runtime": runtime,
        "image": "python:3.11",
        "workdir": "/workspace",
        "ports": {8080: 18080},
        "env": {"A": "1"},
    })
    return sandbox.create_session(), runtime


def test_podman_sandbox_registered() -> None:
    assert SandboxRegistry.get("podman") is PodmanSandbox


def test_runner_imports_podman_sandbox_registration() -> None:
    runner_source = Path("alphadiana/runner/runner.py").read_text()
    assert "import alphadiana.sandbox.podman" in runner_source


def test_create_session_records_container_metadata() -> None:
    session, runtime = _session()

    assert runtime.calls[0][0] == "run"
    metadata = session.metadata()
    assert metadata["sandbox_backend"] == "podman"
    assert metadata["container_engine"] == "podman"
    assert metadata["container_id"] == "cid123"
    assert metadata["image"] == "python:3.11"
    assert metadata["runtime_config_digest"]


def test_execute_maps_runtime_result() -> None:
    session, runtime = _session()

    result = session.execute(["python", "-c", "print(1)"])

    assert result.exit_code == 7
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert runtime.calls[-1][0] == "exec"
    assert runtime.calls[-1][2] == ["python", "-c", "print(1)"]


def test_upload_download_use_podman_cp() -> None:
    session, runtime = _session()

    session.upload("data/input.txt", b"hello")
    content = session.download("data/output.txt")

    mkdir_calls = [
        call
        for call in runtime.calls
        if call[0] == "exec" and call[2] == ["mkdir", "-p", "/workspace/data"]
    ]
    cp_calls = [call for call in runtime.calls if call[0] == "cp"]
    assert mkdir_calls
    assert mkdir_calls[0][3]["check"] is True
    assert cp_calls[0][2] == "cid123:/workspace/data/input.txt"
    assert cp_calls[1][1] == "cid123:/workspace/data/output.txt"
    assert content == b"downloaded"


@pytest.mark.parametrize("bad_path", ["/abs.txt", "../escape.txt", "a/../../escape.txt"])
def test_upload_download_reject_unsafe_paths(bad_path: str) -> None:
    session, _ = _session()

    with pytest.raises(ValueError):
        session.upload(bad_path, b"x")
    with pytest.raises(ValueError):
        session.download(bad_path)


def test_published_port_and_logs() -> None:
    session, _ = _session()

    port = session.published_port(8080)

    assert port is not None
    assert port.api_base == "http://127.0.0.1:18080"
    assert session.logs() == "log text"


def test_close_stops_and_removes_container() -> None:
    session, runtime = _session()

    session.close()

    assert ("stop", "cid123", {"stop_timeout": 10, "check": False}) in runtime.calls
    assert ("rm", "cid123", {"force": True, "volumes": True, "check": False}) in runtime.calls
