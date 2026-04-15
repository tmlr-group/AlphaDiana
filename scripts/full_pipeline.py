"""Minimal external_benchmark AutoKernel pipeline helpers used by regression tests."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from alphadiana.benchmark.external_benchmark_utils import fix_super_bug


AUTOKERNEL_DIR = Path("/workspace/autokernel")
_IGNORE_NAMES = (".git", ".venv", "__pycache__", ".pytest_cache", "workspace")


def run_cmd(
    cmd: Sequence[str],
    cwd: str | Path | None = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command and raise on failure."""
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd is not None else None,
        timeout=timeout,
        check=True,
        capture_output=True,
        text=True,
    )


def stage_autokernel_tree(src: str | Path, dst: str | Path) -> Path:
    """Copy an AutoKernel workspace while dropping runtime-only noise."""
    src_path = Path(src)
    dst_path = Path(dst)
    if dst_path.exists():
        shutil.rmtree(dst_path)
    shutil.copytree(
        src_path,
        dst_path,
        ignore=shutil.ignore_patterns(*_IGNORE_NAMES, "*.pyc"),
    )
    return dst_path


def step2_setup_workspace(task) -> tuple[Path, Path]:
    """Stage an isolated AutoKernel workspace for a external_benchmark task."""
    task_json = getattr(task, "metadata", {}).get("task_json", {})
    level = int(task_json["level"])
    problem_id = int(task_json["problem_id"])
    backend = str(task_json.get("backend", "triton"))
    source = str(task_json.get("source", "hf"))

    work_dir = Path(tempfile.mkdtemp(prefix="alphadiana-external_benchmark-"))
    ak_dir = stage_autokernel_tree(AUTOKERNEL_DIR, work_dir / "autokernel")

    run_cmd(
        [
            "python3",
            "kernelbench/bridge.py",
            "setup",
            "--level",
            str(level),
            "--problem",
            str(problem_id),
            "--backend",
            backend,
            "--source",
            source,
        ],
        cwd=ak_dir,
    )

    kernel_path = ak_dir / "kernel.py"
    if kernel_path.exists():
        kernel_path.write_text(
            fix_super_bug(kernel_path.read_text(encoding="utf-8")),
            encoding="utf-8",
        )

    run_cmd(["git", "init"], cwd=ak_dir)
    run_cmd(["git", "config", "user.email", "external_benchmark@eval"], cwd=ak_dir)
    run_cmd(["git", "config", "user.name", "external_benchmark"], cwd=ak_dir)
    run_cmd(["git", "add", "."], cwd=ak_dir)
    run_cmd(["git", "commit", "-m", "Initial staged workspace"], cwd=ak_dir)
    return work_dir, ak_dir
