"""Explicit verifier replay from immutable, locally retained task images.

Snapshots are captured before verification mutates the task. They belong to the
artifact owner and must be retained while rescoring is needed. Original runtime
containers still follow normal cleanup. No solver is called on the replay path.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from alphadiana.benchmarks.terminal_bench2.harness.common import (
    TerminalBench2ContainerMixin,
)
from alphadiana.benchmarks.terminal_bench2.snapshot import (
    _snapshot_image_id,
    capture_snapshot,
    verifier_digest,
)

__all__ = ["VerifierReplay", "capture_snapshot", "verifier_digest"]

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
        image_id = _snapshot_image_id(snapshot["image_id"], engine=engine)
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
