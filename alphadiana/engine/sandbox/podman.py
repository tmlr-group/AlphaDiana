"""Podman-backed sandbox implementation."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shlex
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from alphadiana.engine.container_runtime.podman_cli import PodmanCLI, PodmanResult
from alphadiana.engine.container_runtime.ports import PublishedPort, first_published_port
from alphadiana.engine.sandbox.base import ExecutionResult, Sandbox, SandboxSession
from alphadiana.engine.sandbox.registry import register_sandbox


_UNSAFE_SHELL_TOKENS = (";", "|", "&", "\n", "`", "$(")


def _command_argv(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        if any(token in command for token in _UNSAFE_SHELL_TOKENS):
            return ["/bin/sh", "-lc", command]
        return shlex.split(command)
    return [str(part) for part in command]


def _safe_relpath(filename: str) -> str:
    if os.path.isabs(filename):
        raise ValueError(f"Path traversal rejected: absolute path {filename!r}")
    normalized = posixpath.normpath(filename)
    if normalized == "." or normalized.startswith("../") or normalized == "..":
        raise ValueError(f"Path traversal rejected: {filename!r}")
    return normalized


def _config_digest(config: Mapping[str, Any]) -> str:
    safe_config = {
        key: value
        for key, value in config.items()
        if key not in {"env", "runtime"} and "key" not in key.lower() and "token" not in key.lower()
    }
    encoded = json.dumps(safe_config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


class PodmanSession(SandboxSession):
    """Sandbox session backed by one Podman container."""

    def __init__(
        self,
        *,
        runtime: PodmanCLI,
        container_id: str,
        image: str,
        config: Mapping[str, Any],
        workspace: str = "/workspace",
        owns_container: bool = True,
    ) -> None:
        self._id = str(uuid.uuid4())
        self._runtime = runtime
        self._container_id = container_id
        self._image = image
        self._config = dict(config)
        self._workspace = workspace.rstrip("/") or "/workspace"
        self._owns_container = owns_container

    @property
    def session_id(self) -> str:
        return self._id

    @property
    def container_id(self) -> str:
        return self._container_id

    def execute(self, command: str | Sequence[str]) -> ExecutionResult:
        start = time.monotonic()
        timeout = self._config.get("exec_timeout")
        result = self._runtime.exec(
            self._container_id,
            _command_argv(command),
            workdir=self._workspace,
            timeout=timeout,
            check=False,
        )
        elapsed = time.monotonic() - start
        return ExecutionResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            wall_time_sec=elapsed,
        )

    def upload(self, filename: str, content: bytes) -> None:
        rel = _safe_relpath(filename)
        parent = posixpath.dirname(rel)
        if parent:
            self._runtime.exec(
                self._container_id,
                ["mkdir", "-p", self._container_path(parent)],
                workdir=self._workspace,
                check=True,
            )
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(content)
            tmp.flush()
            self._runtime.cp(tmp.name, f"{self._container_id}:{self._container_path(rel)}")

    def download(self, filename: str) -> bytes:
        rel = _safe_relpath(filename)
        with tempfile.TemporaryDirectory(prefix="alphadiana_podman_cp_") as tmpdir:
            dest = Path(tmpdir) / Path(rel).name
            self._runtime.cp(f"{self._container_id}:{self._container_path(rel)}", str(dest))
            return dest.read_bytes()

    def close(self) -> None:
        if not self._owns_container:
            return
        stop_timeout = self._config.get("stop_timeout", 10)
        self._runtime.stop(self._container_id, stop_timeout=stop_timeout, check=False)
        self._runtime.rm(self._container_id, force=True, volumes=True, check=False)

    def reset(self) -> None:
        reset_command = self._config.get("reset_command")
        if reset_command:
            self.execute(str(reset_command))

    def metadata(self) -> dict:
        return {
            "session_id": self.session_id,
            "sandbox_backend": "podman",
            "container_engine": "podman",
            "container_id": self._container_id,
            "image": self._image,
            "workspace": self._workspace,
            "runtime_config_digest": _config_digest(self._config),
        }

    def logs(self, *, tail: int | None = None) -> str:
        return self._runtime.logs(self._container_id, tail=tail).stdout

    def published_port(self, container_port: int, protocol: str = "tcp") -> PublishedPort | None:
        output = self._runtime.port(self._container_id, container_port).stdout
        return first_published_port(output, container_port=container_port, protocol=protocol)

    def _container_path(self, rel: str) -> str:
        return f"{self._workspace}/{rel}"


@register_sandbox("podman")
class PodmanSandbox(Sandbox):
    """Sandbox provider using a Podman-managed container."""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._runtime: PodmanCLI | None = None

    @property
    def name(self) -> str:
        return "podman"

    def setup(self, config: dict) -> None:
        self._config = dict(config or {})
        injected_runtime = self._config.get("runtime")
        self._runtime = injected_runtime if injected_runtime is not None else PodmanCLI()

    def create_session(self) -> PodmanSession:
        runtime = self._runtime or PodmanCLI()
        image = str(self._config.get("image") or "python:3.11")
        workspace = str(self._config.get("workdir") or "/workspace")
        name_prefix = str(self._config.get("name_prefix") or "alphadiana-podman")
        name = f"{name_prefix}-{uuid.uuid4().hex[:12]}"
        command = self._config.get("command") or ["sleep", "infinity"]
        env = self._dict_config("env")
        ports = self._dict_config("ports")
        network = self._config.get("network")
        extra_args = list(self._config.get("extra_args") or [])
        create_timeout = self._config.get("startup_timeout")

        result = runtime.run(
            image,
            name=name,
            command=_command_argv(command),
            detach=True,
            env=env,
            workdir=workspace,
            ports=ports,
            network=str(network) if network else None,
            remove=False,
            extra_args=extra_args,
            timeout=create_timeout,
        )
        container_id = result.stdout.strip()
        return PodmanSession(
            runtime=runtime,
            container_id=container_id,
            image=image,
            config=self._config,
            workspace=workspace,
            owns_container=True,
        )

    def _dict_config(self, key: str) -> dict:
        value = self._config.get(key, {})
        return dict(value) if isinstance(value, Mapping) else {}
