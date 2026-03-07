"""Benchmark base class and BenchmarkTask dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BenchmarkTask:
    """A single task/problem from a benchmark."""

    task_id: str
    problem: str
    ground_truth: Any
    metadata: dict = field(default_factory=dict)
    attachments: dict[str, bytes] = field(default_factory=dict)


class Benchmark(ABC):
    """Abstract base class for benchmarks."""

    name: str = ""

    @abstractmethod
    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        """Fetch/parse the dataset and return a list of tasks."""
        ...

    def default_scorer(self) -> str:
        """Suggest a default scorer name (can be overridden by config)."""
        return "exact_match"
