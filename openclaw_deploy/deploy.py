"""Deploy OpenClaw gateway in a ROCK sandbox."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from rock.sdk.sandbox.client import Sandbox
from rock.sdk.sandbox.config import SandboxConfig


DEFAULT_BASE_URL = "http://127.0.0.1:9000"
DEFAULT_PROXY_URL = "http://127.0.0.1:9001/apis/envs/sandbox/v1"
DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parent
INVOKE_CWD = Path.cwd().resolve()
ROCK_ROOT = REPO_ROOT / "ref/ROCK"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent-config",
        default="rock_agent_config.yaml",
        help="Path to the ROCK agent config YAML.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="ROCK admin base URL.",
    )
    parser.add_argument(
        "--proxy-url",
        default=DEFAULT_PROXY_URL,
        help="ROCK proxy API base URL.",
    )
    parser.add_argument(
        "--image",
        default="python:3.11",
        help="Sandbox image.",
    )
    parser.add_argument(
        "--memory",
        default="4g",
        help="Sandbox memory limit.",
    )
    parser.add_argument(
        "--cpus",
        type=float,
        default=1,
        help="Sandbox CPU allocation.",
    )
    parser.add_argument(
        "--startup-timeout",
        type=int,
        default=180,
        help="Sandbox startup timeout in seconds.",
    )
    return parser.parse_args()


async def _check_endpoint(name: str, url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"{name} is not reachable at {url}: {exc}\n"
            "Start these services in separate terminals first:\n"
            "  cd ref/ROCK\n"
            "  ray start --head --port=6380 --disable-usage-stats --block\n"
            "  python -m rock.admin.main --env local-proxy --role admin --port 9000\n"
            "  python -m rock.admin.main --env local-proxy --role proxy --port 9001"
        ) from exc


def _ensure_rock_local_runtime() -> None:
    local_runtime_link = ROCK_ROOT / ".venv"
    expected_target = Path(sys.prefix).resolve()

    if local_runtime_link.is_symlink() and local_runtime_link.resolve() == expected_target:
        return

    if local_runtime_link.exists() and not local_runtime_link.is_symlink():
        raise RuntimeError(
            f"{local_runtime_link} exists but is not a symlink.\n"
            "Local ROCK runtime expects ref/ROCK/.venv to point at the active Python environment."
        )

    if local_runtime_link.is_symlink() or not local_runtime_link.exists():
        local_runtime_link.unlink(missing_ok=True)
        local_runtime_link.symlink_to(expected_target, target_is_directory=True)


async def _preflight(base_url: str, proxy_url: str, agent_config: Path) -> None:
    if not agent_config.exists():
        raise FileNotFoundError(f"Agent config file not found: {agent_config}")
    _ensure_rock_local_runtime()
    await _check_endpoint("ROCK admin", base_url)
    proxy_root = proxy_url.removesuffix("/apis/envs/sandbox/v1")
    await _check_endpoint("ROCK proxy", proxy_root)


def _format_status(status: object) -> str:
    try:
        return json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError:
        return str(status)


def _resolve_agent_config(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    candidates = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append((INVOKE_CWD / path).resolve())
        candidates.append((REPO_ROOT / path).resolve())
        candidates.append((DEPLOY_DIR / path).resolve())

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


async def deploy(args: argparse.Namespace) -> None:
    agent_config = _resolve_agent_config(args.agent_config)
    await _preflight(args.base_url, args.proxy_url, agent_config)

    config = SandboxConfig(
        base_url=args.base_url,
        image=args.image,
        memory=args.memory,
        cpus=args.cpus,
        auto_clear_seconds=3600,
        startup_timeout=args.startup_timeout,
    )
    sandbox = Sandbox(config)
    print("Creating sandbox...")
    try:
        await sandbox.start()
    except Exception as exc:
        details = ""
        if getattr(sandbox, "sandbox_id", None):
            try:
                status = await sandbox.get_status()
                details = f"\nLatest sandbox status:\n{_format_status(status.status)}"
            except Exception as status_exc:
                details = f"\nFailed to fetch sandbox status after start failure: {status_exc}"
        raise RuntimeError(
            f"Failed to create sandbox via {args.base_url}: {exc}{details}\n"
            "Common causes:\n"
            "  - Docker is not running or the current user cannot access Docker\n"
            "  - redis-stack is not running on localhost:6379\n"
            "  - the sandbox image pull is slow or blocked\n"
            "  - ref/ROCK/.venv does not point to the active conda environment\n"
            "  - ROCK admin/proxy was started in a shell without `source dev/rock_env.sh`"
        ) from exc
    print(f"Sandbox ID: {sandbox.sandbox_id}")

    sandbox.url = args.proxy_url

    print(f"Installing OpenClaw agent from {agent_config}...")
    await sandbox.agent.install(config=str(agent_config))
    print("Running OpenClaw gateway...")
    await sandbox.agent.run("")

    print("\nOpenClaw deployed successfully!")
    print(f"Sandbox ID: {sandbox.sandbox_id}")
    print(f"API base: {args.proxy_url}/sandboxes/{sandbox.sandbox_id}/proxy/v1")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    asyncio.run(deploy(parse_args()))
