"""Helpers for detecting and resolving local ROCK service ports."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass


LOCALHOST = "127.0.0.1"
DEFAULT_RAY_PORT = 6380
DEFAULT_RAY_DASHBOARD_PORT = 8265
DEFAULT_RAY_CLIENT_SERVER_PORT = 30001
DEFAULT_REDIS_PORT = 6379
DEFAULT_ADMIN_PORT = 9000
DEFAULT_PROXY_PORT = 9001


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if not (1 <= value <= 65535):
        raise ValueError(f"{name} must be between 1 and 65535, got {value}")
    return value


def is_port_available(port: int, host: str = LOCALHOST) -> bool:
    """Return True if the TCP port can be bound locally."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(start_port: int, host: str = LOCALHOST, limit: int = 200) -> int:
    """Find the first available TCP port starting from start_port."""
    for candidate in range(start_port, start_port + limit):
        if is_port_available(candidate, host=host):
            return candidate
    raise RuntimeError(
        f"No available port found in range [{start_port}, {start_port + limit - 1}] on {host}"
    )


@dataclass(frozen=True)
class RockPorts:
    ray_port: int
    ray_dashboard_port: int
    ray_client_server_port: int
    redis_port: int
    admin_port: int
    proxy_port: int

    @property
    def base_url(self) -> str:
        return f"http://{LOCALHOST}:{self.admin_port}"

    @property
    def proxy_root_url(self) -> str:
        return f"http://{LOCALHOST}:{self.proxy_port}"

    @property
    def proxy_api_url(self) -> str:
        return f"{self.proxy_root_url}/apis/envs/sandbox/v1"


def resolve_rock_ports_from_env() -> RockPorts:
    """Resolve ROCK ports from environment variables with sane defaults."""
    return RockPorts(
        ray_port=_env_int("ROCK_RAY_PORT", DEFAULT_RAY_PORT),
        ray_dashboard_port=_env_int("ROCK_RAY_DASHBOARD_PORT", DEFAULT_RAY_DASHBOARD_PORT),
        ray_client_server_port=_env_int(
            "ROCK_RAY_CLIENT_SERVER_PORT", DEFAULT_RAY_CLIENT_SERVER_PORT
        ),
        redis_port=_env_int("ROCK_REDIS_PORT", DEFAULT_REDIS_PORT),
        admin_port=_env_int("ROCK_ADMIN_PORT", DEFAULT_ADMIN_PORT),
        proxy_port=_env_int("ROCK_PROXY_PORT", DEFAULT_PROXY_PORT),
    )
