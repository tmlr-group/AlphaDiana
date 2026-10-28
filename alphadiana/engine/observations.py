"""Per-task observations recorded after scoring, isolated from authoritative results.

The configuration key ``metrics`` and saved metric fields retain their contract.
Batch calculations over saved runs belong to ``analysis.measurements``.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

from alphadiana.engine.components import register_metric

logger = logging.getLogger(__name__)


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


@register_metric("answer_length")
class AnswerLengthMetric(EvaluationMetric):
    """Measure the final agent answer without affecting benchmark scoring."""

    _VALID_SOURCES = {"raw_output", "answer", "final_answer"}

    def __init__(self) -> None:
        self._source = "raw_output"

    @property
    def name(self) -> str:
        return "answer_length"

    def setup(self, config: dict) -> None:
        source = str(config.get("source", "raw_output") or "raw_output").strip()
        if source not in self._VALID_SOURCES:
            choices = ", ".join(sorted(self._VALID_SOURCES))
            raise ValueError(f"answer_length source must be one of {choices}; got {source!r}")
        self._source = source

    def measure(self, task, response, score) -> MetricResult:
        if self._source == "answer":
            value = response.answer
        elif self._source == "final_answer":
            value = (response.metadata or {}).get("agent_final_answer", "")
        else:
            value = response.raw_output
        text = "" if value is None else str(value)
        return MetricResult(
            value=len(text),
            unit="unicode_codepoints",
            metadata={
                "source": self._source,
                "utf8_bytes": len(text.encode("utf-8")),
                "words": len(re.findall(r"\S+", text)),
                "lines": len(text.splitlines()) if text else 0,
            },
        )


def measure_observations(metrics, task, response, score) -> None:
    """Measure optional metrics without exposing authoritative result objects.

    A metric receives snapshots taken after the primary scorer has completed.
    This prevents a buggy plugin from mutating the task, response, or score that
    AlphaDiana persists as the benchmark result. Metric failures remain
    observations on the real response and never become task failures.
    """
    for metric in metrics:
        try:
            observation = metric.measure(
                deepcopy(task),
                deepcopy(response),
                deepcopy(score),
            )
            measured = observation.to_dict()
            # Reject unpersistable observations inside the metric boundary so
            # an optional plugin cannot prevent the primary score being saved.
            json.dumps(measured, allow_nan=False)
            response.evaluation_metrics[metric.name] = measured
        except Exception as metric_exc:  # noqa: BLE001
            logger.exception("Metric %s failed for %s", metric.name, task.task_id)
            response.evaluation_metric_errors[metric.name] = str(metric_exc)
