"""Run an upstream Harbor job with an explicit pre-verifier snapshot hook.

Executed by the separately configured Harbor Python environment. Harbor owns
agent execution, verification and environment teardown; this hook only captures
an already-created task container before verification starts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

from alphadiana.benchmarks.terminal_bench2.snapshot import capture_snapshot


def snapshot_trial(event, *, project: str, task_id: str, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    task_dir = Path(event.config.task.path).resolve()
    if event.task_name != task_dir.name:
        raise ValueError(
            "Harbor hook task identity differs from the configured directory"
        )
    containers = subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.service=main",
        ],
        text=True,
        timeout=60,
    ).split()
    if len(containers) != 1:
        raise RuntimeError(
            f"Expected one owned Harbor task container, got {len(containers)}"
        )
    runtime = SimpleNamespace(
        task=SimpleNamespace(task_id=task_id, metadata={"task_dir": str(task_dir)}),
        container_engine="docker",
        container_id=containers[0],
    )
    receipt = capture_snapshot(None, runtime)
    receipt["native_trial_id"] = event.trial_id
    with destination.open("x") as stream:
        json.dump(receipt, stream)


async def run_job(config_path: Path, snapshot_path: Path, task_id: str) -> None:
    from harbor.environments.docker.docker import _sanitize_docker_compose_project_name
    from harbor.job import Job
    from harbor.models.job.config import JobConfig

    config = JobConfig.model_validate_json(config_path.read_text())
    if config.environment.type.value != "docker":
        raise ValueError("Retained Harbor verifier snapshots require Docker")
    job = await Job.create(config)
    if len(job) != 1:
        raise ValueError("Snapshot adapter requires exactly one native trial")

    async def before_verification(event):
        project = _sanitize_docker_compose_project_name(event.config.trial_name)
        await asyncio.to_thread(
            snapshot_trial,
            event,
            project=project,
            task_id=task_id,
            destination=snapshot_path,
        )

    job.on_verification_started(before_verification)
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, current.cancel)
    try:
        await job.run()
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
    if not snapshot_path.is_file():
        raise RuntimeError(
            "Harbor did not reach a successful pre-verifier snapshot hook"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("task_id")
    args = parser.parse_args()
    asyncio.run(run_job(args.config, args.snapshot, args.task_id))


if __name__ == "__main__":
    main()
