"""Base classes for sandbox execution environments."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExecutionResult:
    """Result of executing a command in a sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    wall_time_sec: float


class SandboxSession(ABC):
    """Abstract base class for a sandbox session."""

    @property
    @abstractmethod
    def session_id(self) -> str:
        """Unique identifier for this session."""
        ...

    @abstractmethod
    def execute(self, command: str) -> ExecutionResult:
        """Execute a command in the sandbox and return the result."""
        ...

    @abstractmethod
    def upload(self, filename: str, content: bytes) -> None:
        """Upload a file into the sandbox."""
        ...

    @abstractmethod
    def download(self, filename: str) -> bytes:
        """Download a file from the sandbox."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close and clean up the session."""
        ...

    def reset(self) -> None:
        """Reset session state between tasks without tearing it down."""
        return None

    def metadata(self) -> dict:
        """Return metadata about this session. Override for richer info."""
        return {"session_id": self.session_id}

    def read_text(self, filename: str) -> str:
        """Read a text file from the sandbox. Default: download + decode."""
        return self.download(filename).decode("utf-8", errors="replace")


class Sandbox(ABC):
    """Abstract base class for a sandbox provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the sandbox provider."""
        ...

    @abstractmethod
    def setup(self, config: dict) -> None:
        """Initialize the sandbox with the given configuration."""
        ...

    @abstractmethod
    def create_session(self) -> SandboxSession:
        """Create a new sandbox session."""
        ...

    def teardown(self) -> None:
        """Tear down the sandbox provider. Override if cleanup is needed."""
        pass
