#!/usr/bin/env python3
"""Detect available local ports for ROCK services and print shell exports."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from alphadiana.utils.rock_ports import (
    DEFAULT_ADMIN_PORT,
    DEFAULT_PROXY_PORT,
    DEFAULT_REDIS_PORT,
    DEFAULT_RAY_PORT,
    DEFAULT_RAY_CLIENT_SERVER_PORT,
    DEFAULT_RAY_DASHBOARD_PORT,
    LOCALHOST,
    RockPorts,
    is_port_available,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ray-port", type=int, default=DEFAULT_RAY_PORT, help="Preferred Ray port.")
    parser.add_argument(
        "--ray-dashboard-port",
        type=int,
        default=DEFAULT_RAY_DASHBOARD_PORT,
        help="Preferred Ray dashboard port.",
    )
    parser.add_argument(
        "--ray-client-server-port",
        type=int,
        default=DEFAULT_RAY_CLIENT_SERVER_PORT,
        help="Preferred Ray client server port.",
    )
    parser.add_argument(
        "--admin-port",
        type=int,
        default=DEFAULT_ADMIN_PORT,
        help="Preferred ROCK admin port.",
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=DEFAULT_REDIS_PORT,
        help="Preferred Redis host port.",
    )
    parser.add_argument(
        "--redis-container",
        default=f"redis-alphadiana-{os.environ.get('USER', 'default')}",
        help="Redis container name to reuse if already created (default: redis-alphadiana-$USER).",
    )
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=DEFAULT_PROXY_PORT,
        help="Preferred ROCK proxy port.",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=200,
        help="How many candidate ports to try for each service.",
    )
    parser.add_argument(
        "--write-env",
        type=Path,
        help="Optional output file to store shell exports.",
    )
    return parser.parse_args()


def _is_shared_redis(port: int) -> bool:
    """Return True if the Redis instance on *port* has other connected clients.

    A shared Redis (e.g. system-wide ``redis-stack``) will typically have
    multiple client connections.  We consider it "shared" if there are more
    than 2 connected clients (our own ``redis-cli`` plus one headroom).
    """
    try:
        result = subprocess.run(
            ["redis-cli", "-p", str(port), "client", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        # Each connected client produces one line in CLIENT LIST output.
        client_count = sum(1 for line in result.stdout.splitlines() if line.strip())
        return client_count > 2
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _detect_existing_redis_port(container_name: str) -> int | None:
    """Detect a running Redis container's host port, but refuse shared instances.

    If the container is detected but appears to be shared (other clients
    connected), return ``None`` so that a fresh isolated port is allocated
    instead.
    """
    try:
        result = subprocess.run(
            ["docker", "port", container_name, "6379/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    port: int | None = None
    for line in result.stdout.splitlines():
        match = re.search(r":(\d+)\s*$", line.strip())
        if match:
            port = int(match.group(1))
            break

    if port is None:
        return None

    # Refuse to reuse a Redis instance that has other clients connected.
    if _is_shared_redis(port):
        print(
            f"WARNING: Redis container '{container_name}' on port {port} appears "
            f"shared (multiple clients connected). Allocating an isolated port instead.",
            file=sys.stderr,
        )
        return None

    return port


def _detect_distinct_port(start_port: int, used: set[int], limit: int) -> int:
    candidate = start_port
    end_port = start_port + limit
    while candidate < end_port:
        if candidate not in used and is_port_available(candidate, host=LOCALHOST):
            used.add(candidate)
            return candidate
        candidate += 1
    raise RuntimeError(
        f"Could not find a distinct available port in range [{start_port}, {end_port - 1}]"
    )


def detect_ports(args: argparse.Namespace) -> RockPorts:
    used: set[int] = set()
    ray_port = _detect_distinct_port(args.ray_port, used, args.search_limit)
    ray_dashboard_port = _detect_distinct_port(args.ray_dashboard_port, used, args.search_limit)
    ray_client_server_port = _detect_distinct_port(
        args.ray_client_server_port, used, args.search_limit
    )
    redis_port = _detect_existing_redis_port(args.redis_container)
    if redis_port is None:
        redis_port = _detect_distinct_port(args.redis_port, used, args.search_limit)
    else:
        used.add(redis_port)
    admin_port = _detect_distinct_port(args.admin_port, used, args.search_limit)
    proxy_port = _detect_distinct_port(args.proxy_port, used, args.search_limit)
    return RockPorts(
        ray_port=ray_port,
        ray_dashboard_port=ray_dashboard_port,
        ray_client_server_port=ray_client_server_port,
        redis_port=redis_port,
        admin_port=admin_port,
        proxy_port=proxy_port,
    )


def render_exports(ports: RockPorts) -> str:
    lines = [
        f"export ROCK_RAY_PORT={ports.ray_port}",
        f"export ROCK_RAY_DASHBOARD_PORT={ports.ray_dashboard_port}",
        f"export ROCK_RAY_CLIENT_SERVER_PORT={ports.ray_client_server_port}",
        f"export ROCK_REDIS_PORT={ports.redis_port}",
        f'export ROCK_REDIS_CONTAINER="${{ROCK_REDIS_CONTAINER:-redis-alphadiana-${{USER:-default}}}}"',
        f"export ROCK_ADMIN_PORT={ports.admin_port}",
        f"export ROCK_PROXY_PORT={ports.proxy_port}",
        'export ROCK_BIND_HOST="${ROCK_BIND_HOST:-127.0.0.1}"',
        'export ROCK_BASE_URL="http://${ROCK_BIND_HOST}:${ROCK_ADMIN_PORT}"',
        'export ROCK_PROXY_ROOT_URL="http://${ROCK_BIND_HOST}:${ROCK_PROXY_PORT}"',
        'export ROCK_PROXY_URL="${ROCK_PROXY_ROOT_URL}/apis/envs/sandbox/v1"',
    ]
    return "\n".join(lines) + "\n"


def render_dynamic_rock_config(ports: RockPorts) -> str:
    return (
        "ray:\n"
        "    runtime_env:\n"
        "        working_dir: ./\n"
        '    namespace: "rock-sandbox-local"\n'
        "\n"
        "warmup:\n"
        "    images:\n"
        '      - "python:3.11"\n'
        "\n"
        "# generated by dev/find_rock_ports.py\n"
        "redis:\n"
        "    host: localhost\n"
        f"    port: {ports.redis_port}\n"
    )


def main() -> int:
    args = parse_args()
    ports = detect_ports(args)
    content = render_exports(ports)
    print(content, end="")

    if args.write_env:
        args.write_env.parent.mkdir(parents=True, exist_ok=True)
        args.write_env.write_text(content, encoding="utf-8")
        dynamic_config_path = REPO_ROOT / "dev/generated/rock-local-proxy.dynamic.yml"
        dynamic_config_path.parent.mkdir(parents=True, exist_ok=True)
        dynamic_config_path.write_text(
            render_dynamic_rock_config(ports),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
