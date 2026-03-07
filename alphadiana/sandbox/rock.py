"""ROCK container sandbox that executes commands via a remote ROCK API."""

import uuid

from alphadiana.sandbox.base import ExecutionResult, Sandbox, SandboxSession
from alphadiana.sandbox.registry import register_sandbox

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


def _require_requests():
    if requests is None:
        raise RuntimeError(
            "The 'requests' package is required for ROCKSandbox. "
            "Install it with: pip install requests"
        )


class ROCKSession(SandboxSession):
    """Sandbox session backed by a ROCK container."""

    def __init__(self, endpoint: str, image: str, timeout: int = 300) -> None:
        _require_requests()
        self._endpoint = endpoint.rstrip("/")
        self._image = image
        self._timeout = timeout
        self._id = str(uuid.uuid4())
        self._container_id: str | None = None
        self._start_container()

    def _start_container(self) -> None:
        """Create a container session via the ROCK API."""
        resp = requests.post(
            f"{self._endpoint}/sessions",
            json={"image": self._image, "session_id": self._id},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._container_id = data.get("container_id", self._id)

    @property
    def session_id(self) -> str:
        return self._id

    def execute(self, command: str) -> ExecutionResult:
        """Execute a command inside the ROCK container."""
        resp = requests.post(
            f"{self._endpoint}/sessions/{self._id}/execute",
            json={"command": command},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return ExecutionResult(
            exit_code=data.get("exit_code", -1),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            wall_time_sec=data.get("wall_time_sec", 0.0),
        )

    def upload(self, filename: str, content: bytes) -> None:
        """Upload a file to the ROCK container."""
        resp = requests.post(
            f"{self._endpoint}/sessions/{self._id}/upload",
            files={"file": (filename, content)},
            timeout=self._timeout,
        )
        resp.raise_for_status()

    def download(self, filename: str) -> bytes:
        """Download a file from the ROCK container."""
        resp = requests.get(
            f"{self._endpoint}/sessions/{self._id}/download",
            params={"filename": filename},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.content

    def close(self) -> None:
        """Destroy the ROCK container session."""
        try:
            requests.delete(
                f"{self._endpoint}/sessions/{self._id}",
                timeout=self._timeout,
            )
        except Exception:
            pass


@register_sandbox("rock")
class ROCKSandbox(Sandbox):
    """Sandbox provider that runs commands in ROCK containers."""

    def __init__(self) -> None:
        self._endpoint: str = ""
        self._image: str = "python:3.11-slim"
        self._timeout: int = 300

    @property
    def name(self) -> str:
        return "rock"

    def setup(self, config: dict) -> None:
        _require_requests()
        self._endpoint = config["endpoint"]
        self._image = config.get("image", self._image)
        self._timeout = config.get("timeout", self._timeout)

    def create_session(self) -> ROCKSession:
        return ROCKSession(
            endpoint=self._endpoint,
            image=self._image,
            timeout=self._timeout,
        )
