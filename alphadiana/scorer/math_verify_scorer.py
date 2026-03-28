"""Math-verify scorer using symbolic equivalence checking via SymPy.

Uses the ``math-verify`` library (pip install math-verify) to compare
mathematical expressions symbolically, correctly handling cases that
string normalization cannot, such as:
  - 2*sqrt(3) == sqrt(12)
  - 1/2 == 0.5
  - 720 == 6!
  - sqrt(2)/2 == 1/sqrt(2)

Falls back to normalized string comparison if math-verify is unavailable
or fails to parse either expression.
"""

from __future__ import annotations

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer
from alphadiana.utils.math_answer import normalize_math_text, parse_numeric_answer


def _wrap_boxed(text: str) -> str:
    """Wrap text in \\boxed{} if not already wrapped, to trigger LaTeX extraction."""
    stripped = text.strip()
    if stripped.startswith(r"\boxed{"):
        return stripped
    return r"\boxed{" + stripped + "}"


def _math_verify_equal(expected: str, predicted: str) -> bool | None:
    """Return True/False via math-verify, or None if unavailable/parse failure."""
    try:
        from math_verify import verify, parse
        from math_verify.parser import LatexExtractionConfig
    except ImportError:
        return None

    cfg = [LatexExtractionConfig()]
    try:
        gold = parse(_wrap_boxed(expected), extraction_config=cfg)
        pred = parse(_wrap_boxed(predicted), extraction_config=cfg)
        if not gold or not pred:
            return None
        return verify(gold, pred)
    except Exception:
        return None


@register_scorer("math_verify")
class MathVerifyScorer(Scorer):
    """Scorer that uses symbolic math equivalence (math-verify / SymPy).

    Falls back to normalized string comparison when math-verify cannot
    parse one or both expressions.
    """

    @property
    def name(self) -> str:
        return "math_verify"

    def score(self, task, response) -> ScoreResult:
        if response.answer is None:
            return ScoreResult(
                correct=False, score=0.0,
                expected=str(task.ground_truth), predicted=None,
                rationale="No answer produced (answer is None).",
            )
        expected_raw = str(task.ground_truth)
        predicted_raw = str(response.answer)

        symbolic_result = _math_verify_equal(expected_raw, predicted_raw)

        if symbolic_result is not None:
            return ScoreResult(
                correct=symbolic_result,
                score=1.0 if symbolic_result else 0.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale=(
                    "Symbolic match (math-verify)."
                    if symbolic_result
                    else "No symbolic match (math-verify)."
                ),
                metadata={"method": "math_verify"},
            )

        # Fallback 1: normalized string comparison
        expected_norm = normalize_math_text(expected_raw)
        predicted_norm = normalize_math_text(predicted_raw)
        if expected_norm == predicted_norm:
            return ScoreResult(
                correct=True,
                score=1.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale="Exact match after normalization (math-verify fallback).",
                metadata={"method": "normalized_string"},
            )

        # Fallback 2: numeric comparison (handles leading zeros, e.g. "045" vs "45")
        expected_num = parse_numeric_answer(expected_raw)
        predicted_num = parse_numeric_answer(predicted_raw)
        if expected_num is not None and predicted_num is not None:
            numeric_match = abs(expected_num - predicted_num) < 1e-9
            if numeric_match:
                return ScoreResult(
                    correct=True,
                    score=1.0,
                    expected=expected_raw,
                    predicted=predicted_raw,
                    rationale="Numeric match after parsing (math-verify fallback).",
                    metadata={"method": "numeric"},
                )

        return ScoreResult(
            correct=False,
            score=0.0,
            expected=expected_raw,
            predicted=predicted_raw,
            rationale=(
                "No match after normalization (math-verify fallback): "
                f"expected={expected_norm!r}, predicted={predicted_norm!r}."
            ),
            metadata={"method": "normalized_string"},
        )
