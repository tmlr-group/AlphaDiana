"""Helpers for detecting and resolving local ROCK service ports."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
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


def _slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "default"


def default_rock_instance_name(project_root: Path | None = None) -> str:
    root = (project_root or PROJECT_ROOT).resolve()
    user = _slugify(os.environ.get("USER", "user"))
    repo = _slugify(root.name)
    digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
    return f"{user}-{repo}-{digest}"


def default_rock_redis_container(project_root: Path | None = None) -> str:
    return f"redis-alphadiana-{default_rock_instance_name(project_root)}"


def default_rock_ray_tmpdir(project_root: Path | None = None) -> str:
    root = (project_root or PROJECT_ROOT).resolve()
    user = _slugify(os.environ.get("USER", "user"))[:12]
    digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
    return f"/tmp/{user}-ray-{digest}"


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
    raw = os.environ.get(name, "").strip() or file_values.get(name, "").strip()
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


@dataclass(frozen=True)
class RockServiceBinding:
    service: str
    port: int
    pid: int | None
    cmdline: str
    cwd: str
    project_root: str


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


def _read_proc_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _read_proc_environ(pid: int) -> dict[str, str]:
    raw = _read_proc_text(f"/proc/{pid}/environ")
    values: dict[str, str] = {}
    for entry in raw.split("\0"):
        if not entry or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        values[key] = value
    return values


def _read_proc_cmdline(pid: int) -> str:
    return " ".join(part for part in _read_proc_text(f"/proc/{pid}/cmdline").split("\0") if part)


def _read_proc_cwd(pid: int) -> str:
    try:
        return str(Path(f"/proc/{pid}/cwd").resolve())
    except Exception:
        return ""


def _looks_like_project_root(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "alphadiana").exists()
        and (path / "scripts").exists()
    )


def _project_root_from_runtime_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    try:
        path = Path(raw_path).resolve()
    except Exception:
        return None
    for parent in [path, *path.parents]:
        if parent.name == ".cache" and parent.parent.exists():
            candidate = parent.parent.resolve()
            if _looks_like_project_root(candidate):
                return candidate
    return None


def _project_root_from_env(env: dict[str, str], cwd: str) -> Path | None:
    candidates: list[Path] = []

    rock_config_root = _project_root_from_runtime_path(env.get("ROCK_CONFIG", ""))
    if rock_config_root is not None:
        candidates.append(rock_config_root)

    for raw_entry in env.get("PYTHONPATH", "").split(":"):
        entry = raw_entry.strip()
        if not entry:
            continue
        try:
            candidate = Path(entry).resolve()
        except Exception:
            continue
        if candidate.name == "ROCK" and candidate.parent.name == "ref":
            continue
        if _looks_like_project_root(candidate):
            candidates.append(candidate)

    for raw_path in (env.get("PWD", ""), cwd):
        if not raw_path:
            continue
        try:
            candidate = Path(raw_path).resolve()
        except Exception:
            continue
        if candidate.name == "ROCK" and candidate.parent.name == "ref":
            repo_root = candidate.parents[1]
            if _looks_like_project_root(repo_root):
                candidates.append(repo_root)
            continue
        if _looks_like_project_root(candidate):
            candidates.append(candidate)

    for candidate in candidates:
        return candidate
    return None


def _pid_for_port(port: int) -> int | None:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    for line in result.stdout.splitlines():
        raw = line.strip()
        if raw.isdigit():
            return int(raw)
    return None


def inspect_rock_service_binding(service: str, port: int) -> RockServiceBinding:
    pid = _pid_for_port(port)
    if pid is None:
        return RockServiceBinding(
            service=service,
            port=port,
            pid=None,
            cmdline="",
            cwd="",
            project_root="",
        )

    env = _read_proc_environ(pid)
    cwd = _read_proc_cwd(pid)
    project_root = _project_root_from_env(env, cwd)

    return RockServiceBinding(
        service=service,
        port=port,
        pid=pid,
        cmdline=_read_proc_cmdline(pid),
        cwd=cwd,
        project_root=str(project_root) if project_root else "",
    )


def check_rock_service_ownership(
    ports: RockPorts | None = None,
    expected_project_root: Path | None = None,
) -> dict[str, bool | str]:
    """Verify admin/proxy listeners belong to the current AlphaDiana checkout.

    This catches a subtle but important failure mode on shared hosts: the local
    ports may be healthy, but the processes behind them were started from a
    different worktree. In that case, benchmark runs can silently reuse another
    checkout's ROCK cluster and contaminate results.
    """
    if ports is None:
        ports = resolve_rock_ports_from_env()
    expected_root = str((expected_project_root or PROJECT_ROOT).resolve())
    results: dict[str, bool | str] = {}

    for service, port in (
        ("admin", ports.admin_port),
        ("proxy", ports.proxy_port),
    ):
        binding = inspect_rock_service_binding(service, port)
        if binding.pid is None:
            results[service] = f"no listener PID found on port {port}"
        elif binding.project_root:
            if Path(binding.project_root).resolve() == Path(expected_root):
                results[service] = True
            else:
                results[service] = (
                    f"foreign checkout owns port {port}: pid={binding.pid} "
                    f"project_root={binding.project_root}"
                )
        else:
            details = binding.cwd or binding.cmdline or "unknown"
            results[service] = (
                f"could not verify owner for pid={binding.pid} on port {port}: {details}"
            )

    return results


def check_rock_services(ports: RockPorts | None = None, timeout: float = 5.0) -> dict[str, bool | str]:
    """Check connectivity of ROCK services (admin, proxy, Redis).

    Returns a dict with service names as keys and True/error-string as values.
    """
    import subprocess

    if ports is None:
        ports = resolve_rock_ports_from_env()

    results: dict[str, bool | str] = {}

    def _check_http_probe(port: int, path: str, needle: str) -> bool | str:
        try:
            with socket.create_connection((LOCALHOST, port), timeout=timeout):
                pass
            import httpx

            resp = httpx.get(
                f"http://{LOCALHOST}:{port}{path}",
                timeout=timeout,
                trust_env=False,
            )
            if resp.status_code != 200:
                return f"HTTP {resp.status_code}"
            if needle and needle not in resp.text:
                return f"probe missing expected marker {needle!r}"
            return True
        except Exception as exc:
            return f"unreachable on port {port}: {exc}"

    # Check ROCK Admin.
    # Note: the admin root route may block under local-proxy even when the
    # service is healthy, so probe the documented OpenAPI schema instead.
    results["admin"] = _check_http_probe(
        ports.admin_port,
        "/openapi.json",
        "/apis/envs/sandbox/v1/start_async",
    )

    # Check ROCK Proxy.
    results["proxy"] = _check_http_probe(
        ports.proxy_port,
        "/openapi.json",
        "/apis/envs/sandbox/v1/sandboxes",
    )

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
