from __future__ import annotations

import subprocess

import pytest

from alphadiana.container_runtime.gpu import nvidia_cdi_args
from alphadiana.container_runtime.podman_cli import (
    PodmanCLI,
    PodmanError,
    normalize_podman_image_ref,
)
from alphadiana.container_runtime.proxy_env import podman_proxy_env
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
    podman.build(
        ".",
        tag="ad-image:latest",
        file="Containerfile",
        input_text="FROM scratch\n",
        extra_args=["--pull=false"],
    )

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


def test_normalize_podman_image_ref_makes_dockerhub_namespace_explicit() -> None:
    assert normalize_podman_image_ref("alexgshaw/tb2-smoke-test:tag") == (
        "docker.io/alexgshaw/tb2-smoke-test:tag"
    )
    assert normalize_podman_image_ref("docker.io/alexgshaw/tb2-smoke-test:tag") == (
        "docker.io/alexgshaw/tb2-smoke-test:tag"
    )
    assert normalize_podman_image_ref("localhost/tb2:latest") == "localhost/tb2:latest"
    assert normalize_podman_image_ref("python:3.11") == "python:3.11"


def test_podman_cli_run_exec_cp_logs_port_stop_rm_argv() -> None:
    runner = FakeRunner(stdout="cid\n")
    podman = PodmanCLI(runner=runner)

    podman.run("alpine:3.20", detach=True, remove=True, command=["sleep", "1"])
    podman.exec("cid", ["echo", "hi"], env={"X": "1"}, workdir="/tmp")
    podman.cp("local.txt", "cid:/tmp/local.txt")
    podman.logs("cid", tail=20)
    podman.port("cid", 8080)
    podman.wait("cid")
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
    assert runner.calls[5] == ["podman", "wait", "cid"]
    assert runner.calls[6] == ["podman", "stop", "-t", "3", "cid"]
    assert runner.calls[7] == ["podman", "rm", "-f", "-v", "cid"]


def test_podman_cli_run_accepts_task_container_options() -> None:
    runner = FakeRunner(stdout="cid\n")
    podman = PodmanCLI(runner=runner)

    podman.run(
        "task:latest",
        detach=True,
        remove=True,
        user="1000:1000",
        entrypoint="/bin/bash",
        workdir="/workspace",
        volumes=["/tmp/logs:/logs", "/tmp/task:/task:ro"],
        extra_args=["--init"],
        command=["-lc", "echo ok"],
    )

    assert runner.calls == [[
        "podman",
        "run",
        "-d",
        "--rm",
        "--workdir",
        "/workspace",
        "--user",
        "1000:1000",
        "--entrypoint",
        "/bin/bash",
        "-v",
        "/tmp/logs:/logs",
        "-v",
        "/tmp/task:/task:ro",
        "--init",
        "task:latest",
        "-lc",
        "echo ok",
    ]]


def test_podman_cli_nonzero_exit_raises() -> None:
    runner = FakeRunner(returncode=125, stderr="bad image")
    podman = PodmanCLI(runner=runner)

    with pytest.raises(PodmanError) as exc:
        podman.run("missing")

    assert exc.value.result.returncode == 125
    assert "bad image" in str(exc.value)


def test_podman_cli_error_redacts_secret_env_values() -> None:
    runner = FakeRunner(returncode=125, stderr="bad env")
    podman = PodmanCLI(runner=runner)

    with pytest.raises(PodmanError) as exc:
        podman.run(
            "image",
            env={
                "OPENAI_API_KEY": "sk-secret",
                "OPENAI_BASE_URL": "https://example.invalid/v1",
                "OPENCLAW_GATEWAY_TOKEN": "token-secret",
            },
        )

    message = str(exc.value)
    assert "OPENAI_API_KEY=<redacted>" in message
    assert "OPENCLAW_GATEWAY_TOKEN=<redacted>" in message
    assert "OPENAI_BASE_URL=https://example.invalid/v1" in message
    assert "sk-secret" not in message
    assert "token-secret" not in message


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


def test_parse_private_port_output_without_container_prefix() -> None:
    port = first_published_port("0.0.0.0:32768\n", container_port=8080)

    assert port is not None
    assert port.container_port is None
    assert port.api_base == "http://127.0.0.1:32768"


def test_podman_proxy_env_rewrites_loopback_proxy_for_container() -> None:
    env = podman_proxy_env(
        {
            "http_proxy": "http://127.0.0.1:17890",
            "HTTPS_PROXY": "http://localhost:17890",
            "NO_PROXY": "127.0.0.1,localhost",
        },
        host_alias="host.containers.internal",
    )

    assert env["http_proxy"] == "http://host.containers.internal:17890"
    assert env["HTTPS_PROXY"] == "http://host.containers.internal:17890"
    assert env["NO_PROXY"] == "127.0.0.1,localhost"


def test_podman_proxy_env_adds_provider_host_to_no_proxy() -> None:
    env = podman_proxy_env(
        {
            "HTTP_PROXY": "http://127.0.0.1:17890",
            "NO_PROXY": "127.0.0.1,localhost",
        },
        host_alias="host.containers.internal",
        no_proxy_hosts=["172.17.0.1", "172.17.0.1"],
    )

    assert env["HTTP_PROXY"] == "http://host.containers.internal:17890"
    assert env["NO_PROXY"] == "127.0.0.1,localhost,172.17.0.1"


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
