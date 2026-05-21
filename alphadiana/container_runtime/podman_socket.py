"""Podman Unix socket compatibility helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PODMAN_DOCKER_API_VERSION = "1.41"


@dataclass(frozen=True)
class PodmanSocketInfo:
    """Docker API compatibility information for a Podman Unix socket."""

    path: str

    @property
    def docker_host(self) -> str:
        return f"unix://{self.path}"

    def env(self) -> dict[str, str]:
        return {"DOCKER_HOST": self.docker_host}


def default_podman_socket_path() -> str:
    """Return the conventional rootless Podman socket path."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "podman" / "podman.sock")
    return f"/run/user/{os.getuid()}/podman/podman.sock"


def podman_socket_env(socket_path: str | None = None) -> dict[str, str]:
    """Return environment variables for Docker-compatible Podman Unix socket use."""
    return PodmanSocketInfo(socket_path or default_podman_socket_path()).env()


def resolve_podman_docker_api_version(configured: object = "") -> str:
    """Return the Docker API version used for docker-py calls to Podman."""
    value = str(configured or "").strip()
    if value:
        return value
    return (
        os.environ.get("ALPHADIANA_PODMAN_DOCKER_API_VERSION", "").strip()
        or DEFAULT_PODMAN_DOCKER_API_VERSION
    )
