"""Numeric scorer with configurable tolerance."""

from __future__ import annotations

import math

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer
from alphadiana.utils.math_answer import normalize_math_text, parse_numeric_answer


@register_scorer("numeric")
class NumericScorer(Scorer):
    """Scorer that compares numeric values within a configurable tolerance."""

    def __init__(self) -> None:
        self._tolerance: float = 1e-6

    @property
    def name(self) -> str:
        return "numeric"

    def setup(self, config: dict) -> None:
        self._tolerance = float(config.get("tolerance", 1e-6))

    def _is_close(self, a: float, b: float) -> bool:
        """Check if *a* and *b* are close within the configured tolerance,
        using both absolute and relative comparisons."""
        if math.isnan(a) or math.isnan(b):
            return False
        if math.isinf(a) or math.isinf(b):
            # inf == inf is True (handled by a == b below), but inf vs finite is always False.
            return a == b
        if a == b:
            return True
        if abs(a - b) <= self._tolerance:
            return True
        # Relative tolerance.
        denom = max(abs(a), abs(b))
        if denom > 0 and abs(a - b) / denom <= self._tolerance:
            return True
        return False

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
        expected_num = parse_numeric_answer(expected_raw)
        predicted_num = parse_numeric_answer(predicted_raw)

        if expected_num is None:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale=(
                    "Could not parse expected value as a number after normalization: "
                    f"{expected_norm!r}"
                ),
            )

        if predicted_num is None:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale=(
                    "Could not parse predicted value as a number after normalization: "
                    f"{predicted_norm!r}"
                ),
            )

        match = self._is_close(expected_num, predicted_num)
        return ScoreResult(
            correct=match,
            score=1.0 if match else 0.0,
            expected=expected_raw,
            predicted=predicted_raw,
            rationale=(
                f"Numeric comparison: expected={expected_num}, "
                f"predicted={predicted_num}, tolerance={self._tolerance}."
            ),
            metadata={
                "expected_num": expected_num,
                "predicted_num": predicted_num,
                "expected_normalized": expected_norm,
                "predicted_normalized": predicted_norm,
            },
        )
