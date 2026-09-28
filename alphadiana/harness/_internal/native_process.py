"""Owned subprocess lifecycle for optional native harness command adapters."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


def run_native(
    argv: list[str], *, cwd: Path, env: dict[str, str], log: Path, timeout: float
) -> None:
    """Retain diagnostics and terminate only this process group on interruption."""
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
            if code:
                raise RuntimeError(f"Native harness exited {code}; diagnostics: {log}")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
