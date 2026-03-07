"""Base classes for scoring agent responses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScoreResult:
    """Result of scoring an agent response against the expected answer."""

    correct: bool
    score: float
    expected: Any
    predicted: Any
    rationale: str = ""
    metadata: dict = field(default_factory=dict)


class Scorer(ABC):
    """Abstract base class for answer scorers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of this scorer."""
        ...

    def setup(self, config: dict) -> None:
        """Initialize the scorer with the given configuration.

        Override in subclasses that require configuration.
        """
        pass

    @abstractmethod
    def score(self, task, response) -> ScoreResult:
        """Score *response* against *task* and return a ScoreResult.

        Parameters
        ----------
        task:
            A ``BenchmarkTask`` (or compatible object) containing the expected
            answer and any other task metadata.
        response:
            An ``AgentResponse`` (or compatible object) containing the
            predicted answer produced by the agent.
        """
        ...
