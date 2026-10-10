"""Native adapter termination must cover tools after their leader exits."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from alphadiana.harness._internal.native_process import run_native


pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="requires Linux process groups and /proc"
)


def _running(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state not in {"Z", "X"}


@pytest.mark.parametrize("exit_mode", ["timeout", "success", "error"])
def test_native_cleanup_kills_descendant_ignoring_sigterm(tmp_path, exit_mode):
    pid_file = tmp_path / "child.pid"
    child = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; from pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "deadline = time.monotonic() + 5\n"
        f"while not Path({str(pid_file)!r}).exists():\n"
        "    if time.monotonic() > deadline: raise RuntimeError('child not ready')\n"
        "    time.sleep(0.01)\n"
        + (
            "time.sleep(60)\n"
            if exit_mode == "timeout"
            else f"sys.exit({2 if exit_mode == 'error' else 0})\n"
        )
    )
    try:
        kwargs = dict(
            cwd=tmp_path, env=dict(os.environ), log=tmp_path / "native.log", timeout=2
        )
        if exit_mode == "success":
            run_native([sys.executable, "-c", parent], **kwargs)
        else:
            error = (
                subprocess.TimeoutExpired if exit_mode == "timeout" else RuntimeError
            )
            with pytest.raises(error):
                run_native([sys.executable, "-c", parent], **kwargs)
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 3
        while _running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _running(pid), "Native tool remained alive after adapter returned"
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            if _running(pid):
                os.kill(pid, signal.SIGKILL)
