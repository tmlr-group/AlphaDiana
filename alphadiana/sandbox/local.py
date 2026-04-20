"""Local sandbox that executes commands via subprocess."""

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from alphadiana.sandbox.base import ExecutionResult, Sandbox, SandboxSession
from alphadiana.sandbox.registry import register_sandbox


class LocalSession(SandboxSession):
    """Sandbox session that runs commands locally via subprocess."""

    def __init__(self, workdir: str | None = None) -> None:
        self._id = str(uuid.uuid4())
        if workdir is not None:
            self._workdir = Path(workdir)
            self._workdir.mkdir(parents=True, exist_ok=True)
            self._owns_workdir = False
        else:
            self._workdir = Path(tempfile.mkdtemp(prefix="alphadiana_local_"))
            self._owns_workdir = True

    @property
    def session_id(self) -> str:
        return self._id

    def execute(self, command: str) -> ExecutionResult:
        """Execute a shell command locally and return the result."""
        start = time.monotonic()
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(self._workdir),
        )
        elapsed = time.monotonic() - start
        return ExecutionResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            wall_time_sec=elapsed,
        )

    def upload(self, filename: str, content: bytes) -> None:
        """Write *content* to a file inside the working directory."""
        dest = self._workdir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    def download(self, filename: str) -> bytes:
        """Read a file from the working directory."""
        src = self._workdir / filename
        return src.read_bytes()

    def close(self) -> None:
        """Remove the working directory if we created it."""
        if self._owns_workdir and self._workdir.exists():
            shutil.rmtree(self._workdir, ignore_errors=True)


@register_sandbox("local")
class LocalSandbox(Sandbox):
    """Sandbox provider that runs commands on the local machine."""

    def __init__(self) -> None:
        self._config: dict = {}

    @property
    def name(self) -> str:
        return "local"

    def setup(self, config: dict) -> None:
        self._config = config

    def create_session(self) -> LocalSession:
        workdir = self._config.get("workdir")
        return LocalSession(workdir=workdir)
