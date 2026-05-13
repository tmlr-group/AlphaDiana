from __future__ import annotations

import subprocess

import pytest

from alphadiana.container_runtime.gpu import nvidia_cdi_args
from alphadiana.container_runtime.podman_cli import PodmanCLI, PodmanError
from alphadiana.container_runtime.podman_socket import podman_socket_env
from alphadiana.container_runtime.ports import first_published_port, parse_podman_port_output


class FakeRunner:
    def __init__(self, returncode: int = 0, stdout: str = "ok\n", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv, **kwargs):
        assert kwargs["shell"] is False
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(
            args=argv,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def test_podman_cli_create_builds_argv() -> None:
    runner = FakeRunner(stdout="container-1\n")
    podman = PodmanCLI(runner=runner)

    container_id = podman.create(
        "python:3.11",
        name="ad-test",
        command=["sleep", "60"],
        env={"B": "2", "A": "1"},
        ports={8080: 18080},
        network="bridge",
    )

    assert container_id == "container-1"
    assert runner.calls == [[
        "podman",
        "create",
        "--name",
        "ad-test",
        "--network",
        "bridge",
        "--env",
        "A=1",
        "--env",
        "B=2",
        "-p",
        "18080:8080",
        "python:3.11",
        "sleep",
        "60",
    ]]


def test_podman_cli_pull_and_build_argv() -> None:
    runner = FakeRunner()
    podman = PodmanCLI(runner=runner)

    podman.pull("alpine:3.20")
    podman.build(".", tag="ad-image:latest", file="Containerfile", extra_args=["--pull=false"])

    assert runner.calls == [
        ["podman", "pull", "alpine:3.20"],
        [
            "podman",
            "build",
            "-t",
            "ad-image:latest",
            "-f",
            "Containerfile",
            "--pull=false",
            ".",
        ],
    ]


def test_podman_cli_run_exec_cp_logs_port_stop_rm_argv() -> None:
    runner = FakeRunner(stdout="cid\n")
    podman = PodmanCLI(runner=runner)

    podman.run("alpine:3.20", detach=True, remove=True, command=["sleep", "1"])
    podman.exec("cid", ["echo", "hi"], env={"X": "1"}, workdir="/tmp")
    podman.cp("local.txt", "cid:/tmp/local.txt")
    podman.logs("cid", tail=20)
    podman.port("cid", 8080)
    podman.stop("cid", stop_timeout=3)
    podman.rm("cid", force=True, volumes=True)

    assert runner.calls[0] == ["podman", "run", "-d", "--rm", "alpine:3.20", "sleep", "1"]
    assert runner.calls[1] == [
        "podman",
        "exec",
        "--workdir",
        "/tmp",
        "--env",
        "X=1",
        "cid",
        "echo",
        "hi",
    ]
    assert runner.calls[2] == ["podman", "cp", "local.txt", "cid:/tmp/local.txt"]
    assert runner.calls[3] == ["podman", "logs", "--tail", "20", "cid"]
    assert runner.calls[4] == ["podman", "port", "cid", "8080"]
    assert runner.calls[5] == ["podman", "stop", "-t", "3", "cid"]
    assert runner.calls[6] == ["podman", "rm", "-f", "-v", "cid"]


def test_podman_cli_nonzero_exit_raises() -> None:
    runner = FakeRunner(returncode=125, stderr="bad image")
    podman = PodmanCLI(runner=runner)

    with pytest.raises(PodmanError) as exc:
        podman.run("missing")

    assert exc.value.result.returncode == 125
    assert "bad image" in str(exc.value)


def test_podman_cli_timeout_raises() -> None:
    def timeout_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=kwargs["timeout"])

    podman = PodmanCLI(runner=timeout_runner)

    with pytest.raises(PodmanError) as exc:
        podman.version(timeout=0.01)

    assert exc.value.result.returncode == 124
    assert "timed out" in exc.value.result.stderr


def test_parse_podman_port_output_normalizes_loopback() -> None:
    ports = parse_podman_port_output("8080/tcp -> 0.0.0.0:32768\n")

    assert len(ports) == 1
    assert ports[0].container_port == 8080
    assert ports[0].protocol == "tcp"
    assert ports[0].loopback_host == "127.0.0.1"
    assert ports[0].api_base == "http://127.0.0.1:32768"


def test_parse_podman_port_output_keeps_explicit_loopback() -> None:
    port = first_published_port("8080/tcp -> 127.0.0.1:18080\n", container_port=8080)

    assert port is not None
    assert port.host == "127.0.0.1"
    assert port.host_port == 18080


def test_gpu_cdi_args() -> None:
    assert nvidia_cdi_args("all") == [
        "--device",
        "nvidia.com/gpu=all",
        "--security-opt",
        "label=disable",
    ]
    assert nvidia_cdi_args(["0", "1"], disable_selinux_label=False) == [
        "--device",
        "nvidia.com/gpu=0",
        "--device",
        "nvidia.com/gpu=1",
    ]


def test_podman_socket_env_uses_unix_socket(monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/123")

    env = podman_socket_env()

    assert env == {"DOCKER_HOST": "unix:///run/user/123/podman/podman.sock"}
    assert "tcp://0.0.0.0" not in env["DOCKER_HOST"]
