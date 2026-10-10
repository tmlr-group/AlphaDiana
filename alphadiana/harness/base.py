"""Agent base class and AgentResponse dataclass."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from alphadiana.benchmarks.base import BenchmarkTask

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig


@dataclass
class AgentResponse:
    """Response from an agent after solving a task."""

    answer: Any
    trajectory: list[dict] = field(default_factory=list)
    raw_output: str = ""
    token_usage: dict = field(default_factory=dict)
    token_entropy_stats: dict = field(default_factory=dict)
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
    # Observational post-run metrics. These never determine benchmark score.
    evaluation_metrics: dict[str, dict] = field(default_factory=dict)
    evaluation_metric_errors: dict[str, str] = field(default_factory=dict)


class Agent(ABC):
    """Abstract base class for agent systems."""

    name: str = ""
    version: str = ""
    owns_task_runtime: bool = False
    runtime_isolation_mode: str = ""

    @classmethod
    def validate_experiment_config(cls, config: "ExperimentConfig") -> list[str]:
        """Return adapter-specific config errors without starting the runtime."""
        return []

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


def resolve_runtime_skill_folder(raw: Any) -> str | None:
    """Resolve agent.config.skill_folder.

    - Empty/None  -> None (skill mounting disabled)
    - Absolute path -> use as-is
    - Path with "/" -> resolve relative to cwd
    - Bare name like "advanced-maths" -> alphadiana/harness/skills/<name>/
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if os.path.isabs(text):
        return text
    if "/" in text or os.path.exists(text):
        return str(Path(text).resolve())
    pkg_root = Path(__file__).resolve().parent  # bundled harness skills
    return str(pkg_root / "skills" / text)
