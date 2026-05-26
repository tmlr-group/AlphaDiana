"""Official SWE-bench instance-container sandbox backend."""

from __future__ import annotations

import io
import json
import logging
import os
import shlex
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from alphadiana.sandbox.base import ExecutionResult, Sandbox, SandboxSession
from alphadiana.sandbox.registry import register_sandbox
from alphadiana.container_runtime.podman_socket import podman_socket_env
from alphadiana.utils.swebench import (
    build_swebench_instance,
    ensure_swebench_build_network_mode,
    harden_test_spec_repo_clone,
    infer_instance_id,
)

if TYPE_CHECKING:
    from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)

TESTBED_PYTHON_BIN_CANDIDATES = (
    "/opt/miniconda3/envs/testbed/bin",
    "/opt/conda/envs/testbed/bin",
    "/root/miniconda3/envs/testbed/bin",
)
FORWARDED_CONTAINER_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
LOOPBACK_PROXY_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _build_gateway_port_bindings(container_port: int) -> dict[str, list[dict[str, str]]]:
    return {
        f"{container_port}/tcp": [
            {
                "HostIp": "127.0.0.1",
                "HostPort": "",
            }
        ]
    }


def _load_swebench_runtime() -> dict[str, Any]:
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError(
            "The 'docker' package is required for the swebench_container backend. "
            "Install the SWE-bench optional dependencies with: pip install -e '.[swebench]'"
        ) from exc

    try:
        from swebench.harness.constants import DOCKER_USER, DOCKER_WORKDIR
        from swebench.harness.docker_build import (
            build_env_images,
            build_instance_image,
            close_logger,
            setup_logger,
        )
        from swebench.harness.test_spec.test_spec import make_test_spec
    except ImportError as exc:
        raise RuntimeError(
            "The 'swebench' package is required for the swebench_container backend. "
            "Install the SWE-bench optional dependencies with: pip install -e '.[swebench]'"
        ) from exc

    return {
        "docker": docker,
        "DOCKER_USER": DOCKER_USER,
        "DOCKER_WORKDIR": DOCKER_WORKDIR,
        "build_env_images": build_env_images,
        "build_instance_image": build_instance_image,
        "close_logger": close_logger,
        "setup_logger": setup_logger,
        "make_test_spec": make_test_spec,
    }


def _sanitize_container_name(value: str) -> str:
    cleaned = [
        ch.lower() if ch.isalnum() else "-"
        for ch in value
    ]
    collapsed = "".join(cleaned).strip("-")
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    return (collapsed or "swebench").strip("-")[:80]


def _make_tar_archive(target: PurePosixPath, content: bytes) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        info = tarfile.TarInfo(name=target.name)
        info.size = len(content)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(content))
    return data.getvalue()


def _extract_file_from_archive(chunks: Any, expected_name: str) -> bytes:
    payload = b"".join(chunks)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as tar:
        for member in tar.getmembers():
            candidate = Path(member.name).name
            if not member.isfile() or candidate != expected_name:
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            return extracted.read()
    raise FileNotFoundError(expected_name)


def _with_testbed_python_path(command: str) -> str:
    candidates = " ".join(TESTBED_PYTHON_BIN_CANDIDATES)
    return "\n".join(
        [
            "OPENCLAW_TESTBED_BIN=",
            f"for _candidate in {candidates}; do",
            "  if [ -d \"$_candidate\" ]; then",
            "    OPENCLAW_TESTBED_BIN=\"$_candidate\"",
            "    break",
            "  fi",
            "done",
            "if [ -n \"$OPENCLAW_TESTBED_BIN\" ]; then",
            "  export PATH=\"$OPENCLAW_TESTBED_BIN:$PATH\"",
            "fi",
            command,
        ]
    )


def _collect_forwarded_container_environment(config: dict[str, Any]) -> dict[str, str]:
    """Forward host network env vars needed by task-local installs.

    SWE-bench task containers are started from the harness images directly, so
    they do not inherit the host shell environment unless we pass it explicitly.
    For agents that install extra tooling inside the task container, missing
    proxy env vars can make host-vs-container network behavior diverge badly.
    """
    environment: dict[str, str] = {}
    for key in FORWARDED_CONTAINER_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            # Host-local proxies are not reachable from bridge-networked task containers.
            if key.lower().endswith("_proxy"):
                host = urlparse(value).hostname
                if host in LOOPBACK_PROXY_HOSTS:
                    continue
            environment[key] = value

    extra_env = config.get("environment", {})
    if isinstance(extra_env, dict):
        for raw_key, raw_value in extra_env.items():
            key = str(raw_key).strip()
            value = str(raw_value).strip()
            if key and value:
                environment[key] = value
    return environment


class SWEBenchContainerSession(SandboxSession):
    """Sandbox session backed by one official SWE-bench instance container."""

    def __init__(
        self,
        *,
        client: Any,
        container: Any,
        workdir: str,
        user: str,
        keep_container: bool,
        keep_logs: bool,
        log_dir: Path,
        metadata: dict[str, Any],
    ) -> None:
        self._client = client
        self._container = container
        self._workdir = workdir
        self._user = user
        self._keep_container = keep_container
        self._keep_logs = keep_logs
        self._log_dir = log_dir
        self._metadata = dict(metadata)

    @property
    def session_id(self) -> str:
        return self._metadata["container_id"]

    @property
    def sandbox_id(self) -> str:
        return self._metadata["container_id"]

    def gateway_api_base(self) -> str:
        host = self._metadata.get("gateway_host", "127.0.0.1")
        port = self._metadata.get("gateway_host_port")
        if not port:
            raise RuntimeError("Gateway port is not published on this container session")
        return f"http://{host}:{port}/v1"

    def _resolve_remote_path(self, filename: str) -> PurePosixPath:
        path = PurePosixPath(filename)
        if path.is_absolute():
            return path
        return PurePosixPath(self._workdir) / path

    def execute(self, command: str) -> ExecutionResult:
        start = time.monotonic()
        result = self._container.exec_run(
            ["/bin/bash", "-lc", _with_testbed_python_path(command)],
            workdir=self._workdir,
            user=self._user,
            demux=True,
        )
        elapsed = time.monotonic() - start
        stdout, stderr = result.output if isinstance(result.output, tuple) else (result.output, b"")
        return ExecutionResult(
            exit_code=int(result.exit_code),
            stdout=(stdout or b"").decode("utf-8", errors="replace"),
            stderr=(stderr or b"").decode("utf-8", errors="replace"),
            wall_time_sec=elapsed,
        )

    def upload(self, filename: str, content: bytes) -> None:
        target = self._resolve_remote_path(filename)
        parent = str(target.parent)
        self._container.exec_run(
            ["/bin/bash", "-lc", f"mkdir -p {shlex.quote(parent)}"],
            workdir="/",
            user=self._user,
        )
        archive = _make_tar_archive(target, content)
        ok = self._container.put_archive(parent, archive)
        if not ok:
            raise RuntimeError(f"Failed to upload file into container: {target}")

    def download(self, filename: str) -> bytes:
        target = self._resolve_remote_path(filename)
        chunks, _ = self._container.get_archive(str(target))
        return _extract_file_from_archive(chunks, target.name)

    def read_text(self, filename: str) -> str:
        target = self._resolve_remote_path(filename)
        result = self._container.exec_run(
            ["/bin/bash", "-lc", f"cat {shlex.quote(str(target))}"],
            workdir="/",
            user=self._user,
            demux=True,
        )
        stdout, stderr = result.output if isinstance(result.output, tuple) else (result.output, b"")
        if int(result.exit_code) != 0:
            raise FileNotFoundError(
                f"Failed to read {target}: {(stderr or b'').decode('utf-8', errors='replace')}"
            )
        return (stdout or b"").decode("utf-8", errors="replace")

    def metadata(self) -> dict:
        self._container.reload()
        metadata = dict(self._metadata)
        metadata["container_status"] = self._container.status
        return metadata

    def close(self) -> None:
        self._container.reload()
        self._log_dir.mkdir(parents=True, exist_ok=True)

        if self._keep_logs:
            try:
                (self._log_dir / "container.logs.txt").write_text(
                    self._container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace"),
                    encoding="utf-8",
                )
            except Exception:
                logger.debug("Failed to persist container logs", exc_info=True)
            try:
                inspect_data = self._client.api.inspect_container(self._container.id)
                (self._log_dir / "container.inspect.json").write_text(
                    json.dumps(inspect_data, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            except Exception:
                logger.debug("Failed to persist container inspect output", exc_info=True)

        if self._keep_container:
            return

        try:
            self._container.remove(force=True, v=True)
        except Exception:
            logger.warning("Failed to remove SWE-bench container %s", self._container.id, exc_info=True)


@register_sandbox("swebench_container")
class SWEBenchContainerSandbox(Sandbox):
    """Sandbox provider that runs one official SWE-bench instance container per task."""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._runtime: dict[str, Any] | None = None
        self._log_dir = Path("./logs/swebench_container")
        self._container_engine = "docker"
        self._podman_socket_path = ""

    @property
    def name(self) -> str:
        return "swebench_container"

    def setup(self, config: dict) -> None:
        self._config = dict(config)
        self._log_dir = Path(config.get("log_dir", "./logs/swebench_container"))
        self._container_engine = str(config.get("container_engine", "docker") or "docker").strip().lower()
        if self._container_engine not in {"docker", "podman"}:
            raise ValueError(
                "swebench_container sandbox.config.container_engine must be one of docker, podman"
            )
        self._podman_socket_path = str(config.get("podman_socket", "") or "").strip()

    def requires_task_on_create(self) -> bool:
        return True

    def supports_shared_session(self) -> bool:
        return False

    def supports_pooling(self) -> bool:
        return False

    def _runtime_api(self) -> dict[str, Any]:
        if self._runtime is None:
            self._runtime = _load_swebench_runtime()
        ensure_swebench_build_network_mode(self._config.get("docker_build_network", "host"))
        return self._runtime

    @contextmanager
    def _container_runtime_env(self):
        if self._container_engine != "podman":
            yield
            return

        env = podman_socket_env(self._podman_socket_path or None)
        previous = {key: os.environ.get(key) for key in env}
        os.environ.update(env)
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _build_test_spec(self, task: "BenchmarkTask") -> tuple[dict[str, Any], Any]:
        runtime = self._runtime_api()
        instance = build_swebench_instance(task)
        test_spec = runtime["make_test_spec"](
            instance,
            namespace=self._config.get("namespace", "swebench"),
            env_image_tag=self._config.get("env_image_tag", "latest"),
            instance_image_tag=self._config.get("instance_image_tag", "latest"),
            arch=self._config.get("arch", "x86_64"),
        )
        harden_test_spec_repo_clone(
            test_spec,
            clone_retries=int(self._config.get("git_clone_retries", 3)),
            retry_sleep_sec=int(self._config.get("git_clone_retry_sleep_sec", 5)),
        )
        return instance, test_spec

    def _build_image(self, task: "BenchmarkTask", test_spec: Any) -> Path:
        runtime = self._runtime_api()
        instance_id = infer_instance_id(task)
        instance = build_swebench_instance(task)
        build_dir = self._log_dir / instance_id / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        build_log = build_dir / "build_instance_image.log"
        build_logger = runtime["setup_logger"](instance_id, build_log)
        image_name = getattr(test_spec, "instance_image_key", "")
        with self._container_runtime_env():
            client = runtime["docker"].from_env()

            try:
                if self._config.get("force_rebuild") and image_name:
                    try:
                        client.images.remove(image_name, force=True)
                    except Exception:
                        logger.debug("Image removal before force rebuild failed", exc_info=True)
                runtime["build_env_images"](
                    client,
                    [instance],
                    force_rebuild=bool(self._config.get("force_rebuild", False)),
                    max_workers=1,
                    namespace=self._config.get("namespace", "swebench"),
                    instance_image_tag=self._config.get("instance_image_tag", "latest"),
                    env_image_tag=self._config.get("env_image_tag", "latest"),
                )
                runtime["build_instance_image"](
                    test_spec,
                    client,
                    build_logger,
                    nocache=bool(self._config.get("force_rebuild", False)),
                )
            finally:
                runtime["close_logger"](build_logger)

        return build_dir

    def _resolve_gateway_host_port(self, container: Any, container_port: int) -> str:
        container.reload()
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
        bindings = ports.get(f"{container_port}/tcp") or []
        if not bindings:
            raise RuntimeError(
                f"Container {container.id} does not publish {container_port}/tcp"
            )
        return str(bindings[0]["HostPort"])

    def create_session(self, task: "BenchmarkTask | None" = None) -> SWEBenchContainerSession:
        if task is None:
            raise ValueError("swebench_container.create_session() requires a benchmark task")

        runtime = self._runtime_api()
        docker = runtime["docker"]
        with self._container_runtime_env():
            client = docker.from_env()
        instance, test_spec = self._build_test_spec(task)
        instance_id = infer_instance_id(task)
        build_dir = self._build_image(task, test_spec)

        workdir = str(getattr(test_spec, "repo_directory", runtime["DOCKER_WORKDIR"]))
        user = str(getattr(test_spec, "docker_user", runtime["DOCKER_USER"]))
        image_name = str(getattr(test_spec, "instance_image_key", ""))
        container_port = int(self._config.get("gateway_port", 8080))
        gateway_host = str(self._config.get("gateway_host", "127.0.0.1"))
        container_name = _sanitize_container_name(
            f"alphadiana-swebench-{instance_id}-{uuid.uuid4().hex[:8]}"
        )

        labels = {
            "alphadiana.backend": self.name,
            "alphadiana.instance_id": instance_id,
            "alphadiana.task_id": task.task_id,
            "alphadiana.repo": str(instance.get("repo", "")),
        }
        environment = _collect_forwarded_container_environment(self._config)

        try:
            with self._container_runtime_env():
                container = client.containers.run(
                    image_name,
                    command=["/bin/bash", "-lc", "trap : TERM INT; sleep infinity & wait"],
                    detach=True,
                    stdin_open=True,
                    tty=True,
                    name=container_name,
                    working_dir=workdir,
                    user=user,
                    ports=_build_gateway_port_bindings(container_port),
                    labels=labels,
                    environment=environment or None,
                )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to start SWE-bench instance container for {instance_id}: {exc}"
            ) from exc

        container.reload()
        published_ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
        gateway_publish = published_ports.get(f"{container_port}/tcp") or []
        if not gateway_publish or gateway_publish[0].get("HostIp") != "127.0.0.1":
            raise RuntimeError(
                f"SWE-bench gateway port {container_port} did not bind to 127.0.0.1: {gateway_publish!r}"
            )

        host_port = self._resolve_gateway_host_port(container, container_port)
        metadata = {
            "session_id": container.id,
            "sandbox_id": container.id,
            "instance_id": instance_id,
            "repo": instance.get("repo", ""),
            "base_commit": instance.get("base_commit", ""),
            "container_id": container.id,
            "container_name": container.name,
            "image_name": image_name,
            "sandbox_backend": "podman" if self._container_engine == "podman" else self.name,
            "container_engine": self._container_engine,
            "podman_socket": podman_socket_env(self._podman_socket_path or None)["DOCKER_HOST"]
            if self._container_engine == "podman"
            else "",
            "repo_workdir": workdir,
            "docker_user": user,
            "build_log_dir": str(build_dir),
            "gateway_host": gateway_host,
            "gateway_container_port": container_port,
            "gateway_host_port": host_port,
            "forwarded_env_keys": sorted(environment),
        }
        return SWEBenchContainerSession(
            client=client,
            container=container,
            workdir=workdir,
            user=user,
            keep_container=bool(self._config.get("keep_container", False)),
            keep_logs=bool(self._config.get("keep_logs", True)),
            log_dir=self._log_dir / instance_id / "session",
            metadata=metadata,
        )
