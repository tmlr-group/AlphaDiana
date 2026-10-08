"""Base contract for extensible, observational evaluation metrics."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MetricResult:
    """One metric observation.

    Metrics are deliberately separate from ``ScoreResult``: they describe a
    rollout but cannot change benchmark correctness or the primary score.
    """

    value: int | float | str | bool | None
    unit: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvaluationMetric(ABC):
    """Read-only post-run measurement over a task, response, and primary score."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    def setup(self, config: dict) -> None:
        pass

    @abstractmethod
    def measure(self, task, response, score) -> MetricResult:
        ...
