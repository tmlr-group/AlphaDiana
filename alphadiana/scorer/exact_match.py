"""Exact-match scorer with normalization."""

from __future__ import annotations

import re

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer


def _normalize(text: str) -> str:
    """Normalize text for comparison: strip, lowercase, remove trailing
    periods, and collapse whitespace."""
    text = text.strip().lower()
    text = text.rstrip(".")
    text = re.sub(r"\s+", " ", text)
    return text


def _extract_boxed(text: str) -> str | None:
    r"""Extract the content of the last \\boxed{...} in *text*, handling
    nested braces."""
    idx = text.rfind(r"\boxed{")
    if idx == -1:
        return None
    start = idx + len(r"\boxed{")
    depth = 1
    pos = start
    while pos < len(text) and depth > 0:
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
        pos += 1
    if depth == 0:
        return text[start : pos - 1]
    return None


@register_scorer("exact_match")
class ExactMatchScorer(Scorer):
    """Scorer that checks for an exact (normalized) match between the
    expected and predicted answers."""

    @property
    def name(self) -> str:
        return "exact_match"

    def score(self, task, response) -> ScoreResult:
        expected_raw = str(task.ground_truth)
        predicted_raw = str(response.answer)

        expected_norm = _normalize(expected_raw)
        predicted_norm = _normalize(predicted_raw)

        # Direct comparison after normalization.
        if expected_norm == predicted_norm:
            return ScoreResult(
                correct=True,
                score=1.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale="Exact match after normalization.",
            )

        # Try extracting from \boxed{} in the predicted text.
        boxed = _extract_boxed(predicted_raw)
        if boxed is not None:
            boxed_norm = _normalize(boxed)
            if expected_norm == boxed_norm:
                return ScoreResult(
                    correct=True,
                    score=1.0,
                    expected=expected_raw,
                    predicted=predicted_raw,
                    rationale=r"Matched via \boxed{} extraction.",
                )

        return ScoreResult(
            correct=False,
            score=0.0,
            expected=expected_raw,
            predicted=predicted_raw,
            rationale="No exact match found.",
        )
