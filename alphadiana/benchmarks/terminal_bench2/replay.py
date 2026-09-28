"""Explicit verifier replay from immutable, locally retained task images.

Snapshots are captured before verification mutates the task. They belong to the
artifact owner and must be retained while rescoring is needed. Original runtime
containers still follow normal cleanup. No solver is called on the replay path.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from alphadiana.benchmarks.terminal_bench2.harness.common import (
    TerminalBench2ContainerMixin,
)


def verifier_digest(task_dir: str) -> str:
    tests = Path(task_dir) / "tests"
    if not tests.is_dir():
        raise ValueError(f"Missing verifier tests: {tests}")
    digest = hashlib.sha256()
    for path in sorted(tests.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Verifier symlinks are not supported for replay: {path}")
        if path.is_file():
            digest.update(str(path.relative_to(tests)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def capture_snapshot(owner, runtime) -> dict:
    tests_hash = verifier_digest(runtime.task.metadata.get("task_dir", ""))
    if runtime.container_engine == "podman":
        result = owner._podman.commit(runtime.container_id)
    else:
        result = subprocess.run(
            ["docker", "commit", "--pause=true", runtime.container_id],
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
    image_id = result.stdout.strip()
    if not image_id:
        raise RuntimeError("Snapshot capture returned no image identity")
    return {
        "schema_version": 1,
        "image_id": image_id,
        "engine": runtime.container_engine,
        "verifier_sha256": tests_hash,
        "task_id": runtime.task.task_id,
        "captured_before_verification": True,
    }


class VerifierReplay(TerminalBench2ContainerMixin):
    """Reuse existing startup/verifier/cleanup contracts, with a fresh log scope."""

    def run(self, task, *, output_dir: Path, timeout: int = 1200):
        snapshot = task.metadata.get("verifier_snapshot")
        if not snapshot or snapshot.get("schema_version") != 1:
            raise ValueError(
                "Missing retained verifier snapshot; rerun with retain_verifier_snapshot=True"
            )
        if (
            snapshot.get("task_id") != task.task_id
            or snapshot.get("captured_before_verification") is not True
        ):
            raise ValueError("Snapshot task identity or capture phase does not match")
        if verifier_digest(task.metadata.get("task_dir", "")) != snapshot.get(
            "verifier_sha256"
        ):
            raise ValueError("Verifier inputs changed since snapshot capture")
        engine = snapshot["engine"]
        self._setup_container_config(
            {
                "container_engine": engine,
                "logs_base_dir": str(output_dir),
                "test_timeout_sec": timeout,
            }
        )
        image_id = snapshot["image_id"]
        if engine == "podman":
            inspected = self._podman.inspect(image_id)
        else:
            inspected = subprocess.run(
                ["docker", "image", "inspect", image_id],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
        identity = json.loads(inspected.stdout)[0]["Id"]
        if identity.removeprefix("sha256:") != image_id.removeprefix("sha256:"):
            raise ValueError("Retained image identity changed")
        metadata = dict(task.metadata, docker_image=image_id, execution_id=uuid4().hex)
        replay_task = replace(task, metadata=metadata)
        runtime = self._prepare_runtime(replay_task, temp_prefix="tb2-verifier-replay-")
        try:
            result = self._run_verifier_and_read_reward(runtime, timeout_sec=timeout)
            if result.status != "ok" or result.reward is None:
                raise RuntimeError(
                    f"Verifier replay failed: {result.status}; logs: {runtime.logs_dir}"
                )
            return result, snapshot
        finally:
            self._cleanup_runtime(runtime)
