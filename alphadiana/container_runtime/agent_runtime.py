"""Shared Podman-managed lifecycle for standard reasoning agent runtimes.

This module stays inside ``alphadiana.container_runtime`` because it is the
approved boundary for Podman lifecycle operations. Agent modules supply narrow
specs; this runtime owns container startup, file injection, process launch,
published-port resolution, readiness polling, artifact collection, and cleanup.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alphadiana.container_runtime.podman_cli import PodmanCLI, PodmanError
from alphadiana.container_runtime.ports import first_published_port


_SECRET_FIELD_NAMES = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "token",
})


@dataclass(frozen=True)
class RuntimeFile:
    """File to copy into the runtime container."""

    container_path: str
    content: bytes | str | None = None
    local_path: str | None = None


@dataclass(frozen=True)
class HTTPHealthcheck:
    """HTTP readiness probe for an OpenAI-compatible gateway/controller."""

    path: str = "/models"
    method: str = "GET"
    token: str = ""
    expected_statuses: tuple[int, ...] = (200, 404, 405)
    interval_sec: float = 2.0
    request_timeout_sec: float = 5.0


HealthcheckProbe = Callable[[str], bool]


@dataclass(frozen=True)
class PodmanAgentSpec:
    """Agent-specific runtime spec consumed by ``PodmanAgentRuntime``."""

    adapter_name: str
    image: str
    run_command: str
    exposed_port: int | None
    workdir: str = "/workspace"
    env: Mapping[str, str] = field(default_factory=dict)
    ports: Mapping[int | str, int | str | None] | None = None
    network: str | None = None
    extra_args: Sequence[str] = field(default_factory=tuple)
    container_command: Sequence[str] = ("sleep", "infinity")
    install_commands: Sequence[str] = field(default_factory=tuple)
    files: Sequence[RuntimeFile] = field(default_factory=tuple)
    safe_config_paths: Sequence[str] = field(default_factory=tuple)
    artifact_paths: Sequence[str] = field(default_factory=tuple)
    api_base_suffix: str = "/v1"
    healthcheck: HTTPHealthcheck | HealthcheckProbe | None = None
    process_log_path: str = "/tmp/alphadiana-agent-runtime.log"
    startup_timeout: float = 180.0
    request_timeout: float = 600.0
    cleanup_timeout: float = 30.0
    name_prefix: str = "alphadiana-agent"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def port_mapping(self) -> Mapping[int | str, int | str | None]:
        if self.exposed_port is None and self.ports is None:
            return {}
        if self.ports is not None:
            return self.ports
        return {self.exposed_port: None}


@dataclass(frozen=True)
class PodmanAgentRuntimeResult:
    """Successful runtime startup result."""

    container_id: str
    api_base: str
    metadata: dict[str, Any]
    logs: str = ""
    config_snapshots: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    process_id: str = ""


class PodmanAgentRuntimeError(RuntimeError):
    """Structured Podman agent runtime failure."""

    def __init__(self, message: str, *, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)
        super().__init__(message)


class PodmanAgentRuntime:
    """Podman lifecycle engine for agent gateway/controller/bridge containers."""

    def __init__(self, *, runtime: PodmanCLI | None = None) -> None:
        self._runtime = runtime or PodmanCLI()
        self._container_id = ""
        self._spec: PodmanAgentSpec | None = None
        self._api_base = ""
        self._process_id = ""

    @property
    def container_id(self) -> str:
        return self._container_id

    @property
    def api_base(self) -> str:
        return self._api_base

    def start(self, spec: PodmanAgentSpec) -> PodmanAgentRuntimeResult:
        """Start the runtime container and wait for readiness."""

        self._spec = spec
        container_id = ""
        try:
            name = f"{spec.name_prefix}-{spec.adapter_name}-{uuid.uuid4().hex[:10]}"
            result = self._runtime.run(
                spec.image,
                name=name,
                command=list(spec.container_command),
                detach=True,
                env=dict(spec.env),
                workdir=spec.workdir,
                ports=spec.port_mapping(),
                network=spec.network,
                remove=False,
                extra_args=list(spec.extra_args),
                timeout=spec.startup_timeout,
            )
            container_id = result.stdout.strip()
            self._container_id = container_id

            self._copy_files(spec, container_id)
            self._run_install_commands(spec, container_id)
            self._process_id = self._launch_process(spec, container_id)
            self._api_base = self._resolve_api_base(spec, container_id)
            self._wait_until_ready(spec, self._api_base)

            logs = self._logs(container_id)
            config_snapshots = self._collect_text_paths(
                container_id,
                spec.safe_config_paths,
                sanitize=True,
            )
            artifacts = self._collect_text_paths(
                container_id,
                spec.artifact_paths,
                sanitize=False,
            )
            return PodmanAgentRuntimeResult(
                container_id=container_id,
                api_base=self._api_base,
                metadata=self._metadata(spec, container_id, self._api_base),
                logs=logs,
                config_snapshots=config_snapshots,
                artifacts=artifacts,
                process_id=self._process_id,
            )
        except Exception as exc:
            if isinstance(exc, PodmanAgentRuntimeError):
                error = exc
            else:
                error = self._runtime_error(
                    spec,
                    stage="startup",
                    message=f"{spec.adapter_name} Podman runtime startup failed: {exc}",
                    exc=exc,
                    container_id=container_id,
                )
            if container_id:
                self.cleanup(check=False)
            raise error from exc

    def collect_artifacts(self) -> PodmanAgentRuntimeResult:
        """Collect current logs/config snapshots/artifacts for a running runtime."""

        spec = self._require_spec()
        container_id = self._container_id
        return PodmanAgentRuntimeResult(
            container_id=container_id,
            api_base=self._api_base,
            metadata=self._metadata(spec, container_id, self._api_base),
            logs=self._logs(container_id),
            config_snapshots=self._collect_text_paths(
                container_id,
                spec.safe_config_paths,
                sanitize=True,
            ),
            artifacts=self._collect_text_paths(
                container_id,
                spec.artifact_paths,
                sanitize=False,
            ),
            process_id=self._process_id,
        )

    def cleanup(self, *, check: bool = False) -> None:
        """Stop and remove the owned container."""

        spec = self._spec
        container_id = self._container_id
        if not container_id:
            return
        timeout = spec.cleanup_timeout if spec else 30.0
        try:
            self._runtime.stop(container_id, stop_timeout=int(timeout), timeout=timeout, check=check)
        finally:
            self._runtime.rm(container_id, force=True, volumes=True, timeout=timeout, check=check)
            self._container_id = ""
            self._api_base = ""
            self._process_id = ""

    def exec(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        timeout: float | None = None,
        check: bool = True,
    ) -> Any:
        """Run a command inside the owned runtime container."""

        spec = self._require_spec()
        container_id = self._container_id
        if not container_id:
            raise RuntimeError("PodmanAgentRuntime has no active container")
        return self._runtime.exec(
            container_id,
            command,
            env=env,
            workdir=workdir or spec.workdir,
            timeout=timeout if timeout is not None else spec.request_timeout,
            check=check,
        )

    def _copy_files(self, spec: PodmanAgentSpec, container_id: str) -> None:
        for item in spec.files:
            parent = posixpath.dirname(item.container_path)
            if parent:
                self._runtime.exec(
                    container_id,
                    ["mkdir", "-p", parent],
                    timeout=spec.startup_timeout,
                    check=True,
                )
            if item.local_path:
                self._runtime.cp(
                    str(item.local_path),
                    f"{container_id}:{item.container_path}",
                    timeout=spec.startup_timeout,
                )
                continue
            content = item.content if item.content is not None else b""
            if isinstance(content, str):
                content = content.encode("utf-8")
            with tempfile.NamedTemporaryFile() as tmp:
                tmp.write(content)
                tmp.flush()
                self._runtime.cp(
                    tmp.name,
                    f"{container_id}:{item.container_path}",
                    timeout=spec.startup_timeout,
                )

    def _run_install_commands(self, spec: PodmanAgentSpec, container_id: str) -> None:
        for command in spec.install_commands:
            if not str(command).strip():
                continue
            self._runtime.exec(
                container_id,
                ["/bin/sh", "-lc", str(command)],
                workdir=spec.workdir,
                timeout=spec.startup_timeout,
                check=True,
            )

    def _launch_process(self, spec: PodmanAgentSpec, container_id: str) -> str:
        if not str(spec.run_command or "").strip():
            return ""
        command = (
            f"nohup /bin/sh -lc {shlex.quote(spec.run_command)} "
            f">> {shlex.quote(spec.process_log_path)} 2>&1 & echo $!"
        )
        result = self._runtime.exec(
            container_id,
            ["/bin/sh", "-lc", command],
            workdir=spec.workdir,
            timeout=spec.startup_timeout,
            check=True,
        )
        return result.stdout.strip()

    def _resolve_api_base(self, spec: PodmanAgentSpec, container_id: str) -> str:
        if spec.exposed_port is None:
            return ""
        result = self._runtime.port(
            container_id,
            spec.exposed_port,
            timeout=spec.startup_timeout,
        )
        published = first_published_port(
            result.stdout,
            container_port=spec.exposed_port,
            protocol="tcp",
        )
        if published is None:
            raise self._runtime_error(
                spec,
                stage="port_resolution",
                message=f"{spec.adapter_name} Podman runtime could not resolve port {spec.exposed_port}",
                container_id=container_id,
                port_output=result.stdout,
            )
        suffix = str(spec.api_base_suffix or "").strip()
        if suffix and not suffix.startswith("/"):
            suffix = f"/{suffix}"
        return f"{published.api_base.rstrip('/')}{suffix}"

    def _wait_until_ready(self, spec: PodmanAgentSpec, api_base: str) -> None:
        deadline = time.monotonic() + float(spec.startup_timeout)
        last_error: Exception | None = None
        interval = 0.1
        if isinstance(spec.healthcheck, HTTPHealthcheck):
            interval = spec.healthcheck.interval_sec
        while time.monotonic() < deadline:
            try:
                if self._is_ready(spec, api_base):
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(max(0.1, interval))
        message = f"{spec.adapter_name} Podman runtime did not become ready"
        if last_error is not None:
            message = f"{message}: {last_error}"
        raise self._runtime_error(
            spec,
            stage="healthcheck",
            message=message,
            exc=last_error,
            container_id=self._container_id,
        )

    def _is_ready(self, spec: PodmanAgentSpec, api_base: str) -> bool:
        if spec.healthcheck is None:
            return True
        if callable(spec.healthcheck) and not isinstance(spec.healthcheck, HTTPHealthcheck):
            return bool(spec.healthcheck(api_base))

        import httpx

        healthcheck = spec.healthcheck
        assert isinstance(healthcheck, HTTPHealthcheck)
        url = f"{api_base.rstrip('/')}/{healthcheck.path.lstrip('/')}"
        headers: dict[str, str] = {}
        if healthcheck.token:
            headers["Authorization"] = f"bearer {healthcheck.token}"
        method = healthcheck.method.upper()
        with httpx.Client(timeout=healthcheck.request_timeout_sec, trust_env=False) as client:
            response = client.request(method, url, headers=headers)
        return response.status_code in healthcheck.expected_statuses

    def _collect_text_paths(
        self,
        container_id: str,
        paths: Sequence[str],
        *,
        sanitize: bool,
    ) -> dict[str, str]:
        collected: dict[str, str] = {}
        for path in paths:
            text = self._read_text(container_id, path)
            if not text.strip():
                continue
            collected[path] = _sanitize_text(text) if sanitize else text
        return collected

    def _read_text(self, container_id: str, path: str) -> str:
        try:
            result = self._runtime.exec(
                container_id,
                ["/bin/sh", "-lc", f"test -f {shlex.quote(path)} && cat {shlex.quote(path)} || true"],
                timeout=self._require_spec().request_timeout,
                check=False,
            )
        except Exception:
            return ""
        return result.stdout if result.returncode == 0 else ""

    def _logs(self, container_id: str) -> str:
        if not container_id:
            return ""
        try:
            return self._runtime.logs(container_id, tail=400).stdout
        except Exception:
            return ""

    def _runtime_error(
        self,
        spec: PodmanAgentSpec,
        *,
        stage: str,
        message: str,
        exc: Exception | None = None,
        container_id: str = "",
        command: str = "",
        port_output: str = "",
    ) -> PodmanAgentRuntimeError:
        resolved_container = container_id or self._container_id
        if not port_output and resolved_container and spec.exposed_port is not None:
            try:
                port_output = self._runtime.port(
                    resolved_container,
                    spec.exposed_port,
                    timeout=spec.startup_timeout,
                ).stdout
            except Exception:
                port_output = ""
        payload = {
            "stage": stage,
            "adapter_name": spec.adapter_name,
            "container_id": resolved_container,
            "command": command or spec.run_command,
            "logs": self._logs(resolved_container),
            "port_output": port_output,
            "metadata": self._metadata(spec, resolved_container, self._api_base),
        }
        if isinstance(exc, PodmanError):
            payload["podman_result"] = {
                "argv": list(exc.result.argv),
                "returncode": exc.result.returncode,
                "stdout": exc.result.stdout,
                "stderr": exc.result.stderr,
            }
        return PodmanAgentRuntimeError(message, payload=payload)

    def _metadata(self, spec: PodmanAgentSpec, container_id: str, api_base: str) -> dict[str, Any]:
        metadata = {
            "container_engine": "podman",
            "runtime_container_id": container_id,
            "container_id": container_id,
            "runtime_image": spec.image,
            "image": spec.image,
            "runtime_adapter": spec.adapter_name,
            "adapter_name": spec.adapter_name,
            "published_api_base": api_base,
            "api_base": api_base,
            "process_id": self._process_id,
            "runtime_config_digest": _spec_digest(spec),
            "request_timeout": spec.request_timeout,
            "startup_timeout": spec.startup_timeout,
            "cleanup_timeout": spec.cleanup_timeout,
        }
        metadata.update(_redact_secret_fields(dict(spec.metadata)))
        return metadata

    def _require_spec(self) -> PodmanAgentSpec:
        if self._spec is None:
            raise RuntimeError("PodmanAgentRuntime has not been started")
        return self._spec


def _spec_digest(spec: PodmanAgentSpec) -> str:
    safe = {
        "adapter_name": spec.adapter_name,
        "image": spec.image,
        "workdir": spec.workdir,
        "exposed_port": spec.exposed_port,
        "api_base_suffix": spec.api_base_suffix,
        "artifact_paths": list(spec.artifact_paths),
        "safe_config_paths": list(spec.safe_config_paths),
        "metadata": _redact_secret_fields(dict(spec.metadata)),
    }
    encoded = json.dumps(safe, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _redact_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if (
                normalized in _SECRET_FIELD_NAMES
                or normalized.endswith("_api_key")
                or normalized.endswith("_token")
            ):
                redacted[key] = "REDACTED"
            else:
                redacted[key] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


def _sanitize_text(raw_text: str) -> str:
    try:
        parsed = json.loads(raw_text)
    except Exception:
        return raw_text
    return json.dumps(_redact_secret_fields(parsed), indent=2, ensure_ascii=False)
