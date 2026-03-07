"""Numeric scorer with configurable tolerance."""

from __future__ import annotations

import math
import re

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer


def _extract_boxed(text: str) -> str | None:
    r"""Extract content from the last \\boxed{...}."""
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


def _parse_number(text: str) -> float | None:
    """Attempt to parse *text* as a number.

    Handles plain numbers, fractions (e.g. ``3/4``), percentages
    (e.g. ``75%``), and common math formatting (commas as thousands
    separators).
    """
    text = text.strip()

    # Try extracting from \boxed{} first.
    boxed = _extract_boxed(text)
    if boxed is not None:
        text = boxed.strip()

    # Remove surrounding dollar signs / latex wrappers.
    text = text.strip("$").strip()

    # Remove thousands-separator commas: "1,000" -> "1000"
    text = text.replace(",", "")

    # Percentage: "75%" -> 0.75
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100.0
        except ValueError:
            pass

    # Fraction: "3/4" -> 0.75
    frac_match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", text)
    if frac_match:
        numer = float(frac_match.group(1))
        denom = float(frac_match.group(2))
        if denom != 0:
            return numer / denom
        return None

    # Plain float / int.
    try:
        return float(text)
    except ValueError:
        return None


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
        expected_raw = str(task.ground_truth)
        predicted_raw = str(response.answer)

        expected_num = _parse_number(expected_raw)
        predicted_num = _parse_number(predicted_raw)

        if expected_num is None:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale=f"Could not parse expected value as a number: {expected_raw!r}",
            )

        if predicted_num is None:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale=f"Could not parse predicted value as a number: {predicted_raw!r}",
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
            metadata={"expected_num": expected_num, "predicted_num": predicted_num},
        )
