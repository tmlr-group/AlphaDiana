"""Answer-length observation metric."""

from __future__ import annotations

import re

from alphadiana.metrics.base import EvaluationMetric, MetricResult
from alphadiana.registry import register_metric


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
