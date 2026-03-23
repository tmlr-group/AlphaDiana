"""Helpers for detecting and resolving local ROCK service ports."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path


LOCALHOST = "127.0.0.1"
DEFAULT_RAY_PORT = 6380
DEFAULT_RAY_DASHBOARD_PORT = 8265
DEFAULT_RAY_CLIENT_SERVER_PORT = 30001
DEFAULT_REDIS_PORT = 6379
DEFAULT_ADMIN_PORT = 9000
DEFAULT_PROXY_PORT = 9001
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Search both scripts/ and dev/ for the ports file — the quickstart may write
# to either location depending on how it was invoked.
_PORTS_ENV_CANDIDATES = [
    PROJECT_ROOT / "scripts" / ".rock_ports.env",
    PROJECT_ROOT / "dev" / ".rock_ports.env",
]


def _find_rock_ports_env_file() -> Path | None:
    """Return the first existing .rock_ports.env file, or None."""
    for candidate in _PORTS_ENV_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


ROCK_PORTS_ENV_FILE = _find_rock_ports_env_file() or _PORTS_ENV_CANDIDATES[0]


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


def _load_rock_ports_file() -> dict[str, str]:
    """Load ROCK port exports from .rock_ports.env if present.

    Searches scripts/.rock_ports.env first, then dev/.rock_ports.env.
    """
    ports_file = _find_rock_ports_env_file()
    if ports_file is None:
        return {}
    values: dict[str, str] = {}
    for raw_line in ports_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value or "${" in value:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        values[key] = value
    return values


def _resolve_int(name: str, default: int, file_values: dict[str, str]) -> int:
    raw = file_values.get(name, "").strip() or os.environ.get(name, "").strip()
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
    """Resolve ROCK ports from .rock_ports.env first, then environment."""
    file_values = _load_rock_ports_file()
    return RockPorts(
        ray_port=_resolve_int("ROCK_RAY_PORT", DEFAULT_RAY_PORT, file_values),
        ray_dashboard_port=_resolve_int(
            "ROCK_RAY_DASHBOARD_PORT", DEFAULT_RAY_DASHBOARD_PORT, file_values
        ),
        ray_client_server_port=_resolve_int(
            "ROCK_RAY_CLIENT_SERVER_PORT", DEFAULT_RAY_CLIENT_SERVER_PORT, file_values
        ),
        redis_port=_resolve_int("ROCK_REDIS_PORT", DEFAULT_REDIS_PORT, file_values),
        admin_port=_resolve_int("ROCK_ADMIN_PORT", DEFAULT_ADMIN_PORT, file_values),
        proxy_port=_resolve_int("ROCK_PROXY_PORT", DEFAULT_PROXY_PORT, file_values),
    )


def check_rock_services(ports: RockPorts | None = None, timeout: float = 5.0) -> dict[str, bool | str]:
    """Check connectivity of ROCK services (admin, proxy, Redis).

    Returns a dict with service names as keys and True/error-string as values.
    """
    import subprocess

    if ports is None:
        ports = resolve_rock_ports_from_env()

    results: dict[str, bool | str] = {}

    # Check ROCK Admin
    try:
        with socket.create_connection((LOCALHOST, ports.admin_port), timeout=timeout):
            pass
        import httpx
        resp = httpx.get(ports.base_url + "/", timeout=timeout)
        if resp.status_code == 200:
            results["admin"] = True
        else:
            results["admin"] = f"HTTP {resp.status_code}"
    except Exception as exc:
        results["admin"] = f"unreachable on port {ports.admin_port}: {exc}"

    # Check ROCK Proxy
    try:
        with socket.create_connection((LOCALHOST, ports.proxy_port), timeout=timeout):
            pass
        import httpx
        resp = httpx.get(ports.proxy_root_url + "/", timeout=timeout)
        if resp.status_code == 200:
            results["proxy"] = True
        else:
            results["proxy"] = f"HTTP {resp.status_code}"
    except Exception as exc:
        results["proxy"] = f"unreachable on port {ports.proxy_port}: {exc}"

    # Check Redis
    try:
        with socket.create_connection((LOCALHOST, ports.redis_port), timeout=timeout):
            results["redis"] = True
    except Exception as exc:
        results["redis"] = f"unreachable on port {ports.redis_port}: {exc}"

    # Check Docker availability
    try:
        result = subprocess.run(
            ["docker", "ps"], capture_output=True, timeout=5, text=True,
        )
        results["docker"] = True if result.returncode == 0 else result.stderr.strip()
    except Exception as exc:
        results["docker"] = f"unavailable: {exc}"

    return results
