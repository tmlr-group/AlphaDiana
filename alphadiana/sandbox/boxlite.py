"""Boxlite cloud sandbox that executes commands via the Boxlite SDK."""

import uuid

from alphadiana.sandbox.base import ExecutionResult, Sandbox, SandboxSession
from alphadiana.sandbox.registry import register_sandbox

try:
    from boxlite import Boxlite
except ImportError:
    Boxlite = None  # type: ignore[assignment,misc]


def _require_boxlite():
    if Boxlite is None:
        raise RuntimeError(
            "The 'boxlite' package is required for BoxliteSandbox. "
            "Install it with: pip install boxlite"
        )


class BoxliteSession(SandboxSession):
    """Sandbox session backed by the Boxlite cloud sandbox."""

    def __init__(self, api_key: str, image: str = "python:3.11", **kwargs) -> None:
        _require_boxlite()
        self._id = str(uuid.uuid4())
        self._box = Boxlite(api_key=api_key, image=image, **kwargs)

    @property
    def session_id(self) -> str:
        return self._id

    def execute(self, command: str) -> ExecutionResult:
        """Execute a command in the Boxlite sandbox."""
        result = self._box.execute(command)
        return ExecutionResult(
            exit_code=getattr(result, "exit_code", 0),
            stdout=getattr(result, "stdout", str(result)),
            stderr=getattr(result, "stderr", ""),
            wall_time_sec=getattr(result, "wall_time_sec", 0.0),
        )

    def upload(self, filename: str, content: bytes) -> None:
        """Upload a file to the Boxlite sandbox."""
        self._box.upload(filename, content)

    def download(self, filename: str) -> bytes:
        """Download a file from the Boxlite sandbox."""
        return self._box.download(filename)

    def close(self) -> None:
        """Close the Boxlite sandbox session."""
        try:
            self._box.close()
        except Exception:
            pass


@register_sandbox("boxlite")
class BoxliteSandbox(Sandbox):
    """Sandbox provider that runs commands in Boxlite cloud sandboxes."""

    def __init__(self) -> None:
        self._config: dict = {}

    @property
    def name(self) -> str:
        return "boxlite"

    def setup(self, config: dict) -> None:
        _require_boxlite()
        self._config = config

    def create_session(self) -> BoxliteSession:
        api_key = self._config["api_key"]
        image = self._config.get("image", "python:3.11")
        extra = {
            k: v
            for k, v in self._config.items()
            if k not in ("api_key", "image")
        }
        return BoxliteSession(api_key=api_key, image=image, **extra)
