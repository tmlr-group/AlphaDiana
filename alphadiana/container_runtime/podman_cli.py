"""Thin Podman CLI wrapper.

This module is the approved boundary for new Podman subprocess calls. Keep it
small and free of agent, benchmark, scorer, or runner-specific behavior.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PodmanResult:
    """Structured result returned from a Podman CLI invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def redacted_argv(self) -> tuple[str, ...]:
        return tuple(_redact_argv(self.argv))


class PodmanError(RuntimeError):
    """Raised when a Podman command fails and ``check`` is enabled."""

    def __init__(self, result: PodmanResult) -> None:
        self.result = result
        msg = (
            f"podman command failed with exit {result.returncode}: "
            f"{' '.join(result.redacted_argv)}"
        )
        if result.stderr.strip():
            msg = f"{msg}: {result.stderr.strip()}"
        super().__init__(msg)


_SECRET_ENV_MARKERS = (
    "API_KEY",
    "TOKEN",
    "AUTHORIZATION",
    "PASSWORD",
    "SECRET",
)


def normalize_podman_image_ref(image: str) -> str:
    """Make Docker Hub namespace refs explicit for Podman hosts without search registries."""
    ref = str(image or "").strip()
    if not ref or "://" in ref or "/" not in ref:
        return ref
    first_component = ref.split("/", 1)[0]
    if first_component == "localhost" or "." in first_component or ":" in first_component:
        return ref
    return f"docker.io/{ref}"


def _redact_argv(argv: tuple[str, ...]) -> list[str]:
    redacted: list[str] = []
    redact_next_env = False
    for part in argv:
        text = str(part)
        if redact_next_env:
            redacted.append(_redact_env_assignment(text))
            redact_next_env = False
            continue
        redacted.append(_redact_env_assignment(text))
        if text in {"--env", "-e"}:
            redact_next_env = True
    return redacted


def _redact_env_assignment(value: str) -> str:
    if "=" not in value:
        return value
    key, _raw = value.split("=", 1)
    normalized = key.strip().upper().replace("-", "_")
    if any(marker in normalized for marker in _SECRET_ENV_MARKERS):
        return f"{key}=<redacted>"
    return value


class PodmanCLI:
    """Small argv-based wrapper around the Podman CLI."""

    def __init__(
        self,
        *,
        binary: str = "podman",
        default_timeout: float = 60.0,
        runner: Runner | None = None,
    ) -> None:
        self.binary = binary
        self.default_timeout = default_timeout
        self._runner = runner or subprocess.run

    def _run(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        check: bool = True,
        input_text: str | None = None,
    ) -> PodmanResult:
        argv = (self.binary, *[str(part) for part in args])
        try:
            completed = self._runner(
                list(argv),
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.default_timeout if timeout is None else timeout,
                input=input_text,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            result = PodmanResult(
                argv=argv,
                returncode=124,
                stdout=stdout,
                stderr=stderr or f"podman command timed out after {exc.timeout}s",
            )
            raise PodmanError(result) from exc

        result = PodmanResult(
            argv=argv,
            returncode=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        if check and not result.ok:
            raise PodmanError(result)
        return result

    def version(self, *, timeout: float | None = None) -> PodmanResult:
        return self._run(["--version"], timeout=timeout)

    def pull(self, image: str, *, timeout: float | None = None) -> PodmanResult:
        return self._run(["pull", normalize_podman_image_ref(image)], timeout=timeout)

    def build(
        self,
        context: str,
        *,
        tag: str | None = None,
        file: str | None = None,
        input_text: str | None = None,
        extra_args: Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> PodmanResult:
        args = ["build"]
        if tag:
            args.extend(["-t", tag])
        if file:
            args.extend(["-f", file])
        if extra_args:
            args.extend(str(part) for part in extra_args)
        args.append(context)
        return self._run(args, timeout=timeout, input_text=input_text)

    def create(
        self,
        image: str,
        *,
        name: str | None = None,
        command: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        user: str | None = None,
        entrypoint: str | None = None,
        volumes: Mapping[str, str] | Sequence[str] | None = None,
        ports: Mapping[int | str, int | str | None] | None = None,
        network: str | None = None,
        remove: bool = False,
        extra_args: Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> str:
        args = ["create"]
        args.extend(self._container_options(
            name=name,
            env=env,
            workdir=workdir,
            user=user,
            entrypoint=entrypoint,
            volumes=volumes,
            ports=ports,
            network=network,
            remove=remove,
            extra_args=extra_args,
        ))
        args.append(normalize_podman_image_ref(image))
        if command:
            args.extend(str(part) for part in command)
        return self._run(args, timeout=timeout).stdout.strip()

    def run(
        self,
        image: str,
        *,
        name: str | None = None,
        command: Sequence[str] | None = None,
        detach: bool = False,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        user: str | None = None,
        entrypoint: str | None = None,
        volumes: Mapping[str, str] | Sequence[str] | None = None,
        ports: Mapping[int | str, int | str | None] | None = None,
        network: str | None = None,
        remove: bool = False,
        extra_args: Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> PodmanResult:
        args = ["run"]
        if detach:
            args.append("-d")
        args.extend(self._container_options(
            name=name,
            env=env,
            workdir=workdir,
            user=user,
            entrypoint=entrypoint,
            volumes=volumes,
            ports=ports,
            network=network,
            remove=remove,
            extra_args=extra_args,
        ))
        args.append(normalize_podman_image_ref(image))
        if command:
            args.extend(str(part) for part in command)
        return self._run(args, timeout=timeout)

    def start(self, container_id: str, *, timeout: float | None = None) -> PodmanResult:
        return self._run(["start", container_id], timeout=timeout)

    def exec(
        self,
        container_id: str,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        user: str | None = None,
        timeout: float | None = None,
        check: bool = True,
    ) -> PodmanResult:
        args = ["exec"]
        if workdir:
            args.extend(["--workdir", workdir])
        if user:
            args.extend(["--user", user])
        for key, value in sorted((env or {}).items()):
            args.extend(["--env", f"{key}={value}"])
        args.append(container_id)
        args.extend(str(part) for part in command)
        return self._run(args, timeout=timeout, check=check)

    def cp(
        self,
        src: str,
        dst: str,
        *,
        timeout: float | None = None,
        check: bool = True,
    ) -> PodmanResult:
        return self._run(["cp", src, dst], timeout=timeout, check=check)

    def logs(
        self,
        container_id: str,
        *,
        tail: int | None = None,
        timeout: float | None = None,
    ) -> PodmanResult:
        args = ["logs"]
        if tail is not None:
            args.extend(["--tail", str(tail)])
        args.append(container_id)
        return self._run(args, timeout=timeout)

    def port(
        self,
        container_id: str,
        private_port: int | str | None = None,
        *,
        timeout: float | None = None,
    ) -> PodmanResult:
        args = ["port", container_id]
        if private_port is not None:
            args.append(str(private_port))
        return self._run(args, timeout=timeout)

    def inspect(
        self,
        target: str,
        *,
        timeout: float | None = None,
        check: bool = True,
    ) -> PodmanResult:
        return self._run(["inspect", target], timeout=timeout, check=check)

    def image_exists(self, image: str, *, timeout: float | None = None) -> bool:
        if self._run(["image", "inspect", image], timeout=timeout, check=False).ok:
            return True
        normalized = normalize_podman_image_ref(image)
        if normalized == image:
            return False
        return self._run(["image", "inspect", normalized], timeout=timeout, check=False).ok

    def wait(
        self,
        container_id: str,
        *,
        timeout: float | None = None,
        check: bool = True,
    ) -> PodmanResult:
        return self._run(["wait", container_id], timeout=timeout, check=check)

    def stop(
        self,
        container_id: str,
        *,
        stop_timeout: int | None = None,
        timeout: float | None = None,
        check: bool = False,
    ) -> PodmanResult:
        args = ["stop"]
        if stop_timeout is not None:
            args.extend(["-t", str(stop_timeout)])
        args.append(container_id)
        return self._run(args, timeout=timeout, check=check)

    def rm(
        self,
        container_id: str,
        *,
        force: bool = False,
        volumes: bool = False,
        timeout: float | None = None,
        check: bool = False,
    ) -> PodmanResult:
        args = ["rm"]
        if force:
            args.append("-f")
        if volumes:
            args.append("-v")
        args.append(container_id)
        return self._run(args, timeout=timeout, check=check)

    @staticmethod
    def _container_options(
        *,
        name: str | None,
        env: Mapping[str, str] | None,
        workdir: str | None,
        user: str | None,
        entrypoint: str | None,
        volumes: Mapping[str, str] | Sequence[str] | None,
        ports: Mapping[int | str, int | str | None] | None,
        network: str | None,
        remove: bool,
        extra_args: Sequence[str] | None,
    ) -> list[str]:
        args: list[str] = []
        if name:
            args.extend(["--name", name])
        if remove:
            args.append("--rm")
        if workdir:
            args.extend(["--workdir", workdir])
        if user:
            args.extend(["--user", user])
        if entrypoint:
            args.extend(["--entrypoint", entrypoint])
        if network:
            args.extend(["--network", network])
        for key, value in sorted((env or {}).items()):
            args.extend(["--env", f"{key}={value}"])
        if isinstance(volumes, Mapping):
            volume_specs = [f"{host}:{container}" for host, container in sorted(volumes.items())]
        else:
            volume_specs = [str(spec) for spec in (volumes or [])]
        for spec in volume_specs:
            if spec:
                args.extend(["-v", spec])
        for container_port, host_port in sorted((ports or {}).items(), key=lambda item: str(item[0])):
            spec = str(container_port)
            if host_port not in (None, ""):
                spec = f"{host_port}:{container_port}"
            args.extend(["-p", spec])
        if extra_args:
            args.extend(str(part) for part in extra_args)
        return args
