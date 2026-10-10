"""Capture immutable pre-verifier state without importing a solver runtime."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


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


def _snapshot_image_id(value: str, *, engine: str) -> str:
    """Read an immutable ID, including Docker 29's known stdout notice."""
    notice = (
        "Flag --pause has been deprecated, and enabled by default. "
        "Use --no-pause to disable pausing during commit."
    )
    if not isinstance(value, str):
        raise ValueError("Invalid snapshot image identity")
    identity = value.strip()
    if engine == "docker" and identity.startswith(notice + "\n"):
        identity = identity[len(notice) + 1:]
    if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", identity):
        raise ValueError("Invalid snapshot image identity")
    return identity


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
    image_id = _snapshot_image_id(result.stdout, engine=runtime.container_engine)
    return {
        "schema_version": 1,
        "image_id": image_id,
        "engine": runtime.container_engine,
        "verifier_sha256": tests_hash,
        "task_id": runtime.task.task_id,
        "captured_before_verification": True,
    }
