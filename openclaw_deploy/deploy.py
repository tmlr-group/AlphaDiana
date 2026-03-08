"""Deploy OpenClaw gateway in a ROCK sandbox."""
import asyncio
import os
from pathlib import Path

from rock.sdk.sandbox.client import Sandbox
from rock.sdk.sandbox.config import SandboxConfig


async def deploy():
    base_url = "http://127.0.0.1:9000"
    proxy_url = "http://127.0.0.1:9001/apis/envs/sandbox/v1"

    config = SandboxConfig(
        base_url=base_url,
        image="python:3.11",
        memory="4g",
        cpus=1,
        auto_clear_seconds=3600,
    )
    sandbox = Sandbox(config)
    print("Creating sandbox...")
    await sandbox.start()
    print(f"Sandbox ID: {sandbox.sandbox_id}")

    # Switch to proxy for runtime operations
    sandbox.url = proxy_url

    # Install and run OpenClaw agent
    print("Installing OpenClaw agent...")
    await sandbox.agent.install()
    print("Running OpenClaw gateway...")
    await sandbox.agent.run("")

    print(f"\nOpenClaw deployed successfully!")
    print(f"Sandbox ID: {sandbox.sandbox_id}")
    print(f"API base: {proxy_url}/sandboxes/{sandbox.sandbox_id}/proxy/v1")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    asyncio.run(deploy())
