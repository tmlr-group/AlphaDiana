"""Agent base class and AgentResponse dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from alphadiana.benchmark.base import BenchmarkTask


@dataclass
class AgentResponse:
    """Response from an agent after solving a task."""

    answer: Any
    trajectory: list[dict] = field(default_factory=list)
    raw_output: str = ""
    token_usage: dict = field(default_factory=dict)
    wall_time_sec: float = 0.0
    metadata: dict = field(default_factory=dict)

    # --- Extended fields absorbed from codex-dev for observability ---
    reasoning_trajectory: list[dict] = field(default_factory=list)
    request_messages: list[dict] = field(default_factory=list)
    response_json: dict = field(default_factory=dict)
    sandbox_id: str = ""
    gateway_url: str = ""
    artifact_manifest: dict = field(default_factory=dict)
    gateway_log_excerpt: str = ""
    workspace_snapshot_paths: list[str] = field(default_factory=list)
    workspace_file_contents: dict[str, str] = field(default_factory=dict)
    sandbox_metadata: dict = field(default_factory=dict)
    system_prompt: str = ""
    finish_reason: str = ""


class Agent(ABC):
    """Abstract base class for agent systems."""

    name: str = ""
    version: str = ""

    @abstractmethod
    def setup(self, config: dict) -> None:
        """Initialize the agent with model/config settings."""
        ...

    @abstractmethod
    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        """Solve a benchmark task and return the response."""
        ...

    def teardown(self) -> None:
        """Cleanup resources."""
        pass
