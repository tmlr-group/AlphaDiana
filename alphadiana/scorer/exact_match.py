"""Exact-match scorer with math-aware normalization."""

from __future__ import annotations

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer
from alphadiana.utils.math_answer import normalize_math_text


@register_scorer("exact_match")
class ExactMatchScorer(Scorer):
    """Scorer that checks for an exact (normalized) match between the
    expected and predicted answers."""

    @property
    def name(self) -> str:
        return "exact_match"

    def score(self, task, response) -> ScoreResult:
        if response.answer is None:
            return ScoreResult(
                correct=False, score=0.0,
                expected=str(task.ground_truth), predicted=None,
                rationale="No answer produced (answer is None).",
            )
        expected_raw = str(task.ground_truth)
        predicted_raw = str(response.answer)

        expected_norm = normalize_math_text(expected_raw)
        predicted_norm = normalize_math_text(predicted_raw)

        if expected_norm == predicted_norm:
            return ScoreResult(
                correct=True,
                score=1.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale="Exact match after math-aware normalization.",
            )

        return ScoreResult(
            correct=False,
            score=0.0,
            expected=expected_raw,
            predicted=predicted_raw,
            rationale=(
                "No exact match found after math-aware normalization: "
                f"expected={expected_norm!r}, predicted={predicted_norm!r}."
            ),
        )
