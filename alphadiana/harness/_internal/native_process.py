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
            # The leader can exit while a tool process remains alive. Always
            # clean the owned group, including after normal/native-error exits.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            finally:
                # Waiting for the leader does not wait for its descendants.
                # A child may ignore SIGTERM even after the leader has exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
