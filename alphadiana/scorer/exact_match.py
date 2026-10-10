"""Exact-match scorer with math-aware normalization."""

from __future__ import annotations

import re

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.registry import register_scorer
from alphadiana.utils.math_answer import normalize_math_text


_SINGLE_CHOICE_RE = re.compile(r"^\(?([a-j])\)?$")


def _declared_choice_answer(task, predicted: str) -> str:
    """Read one label, optionally with its exact declared option text."""
    count = task.metadata.get("num_options")
    if (
        task.metadata.get("question_type") != "multiple-choice"
        or type(count) is not int
        or not 1 <= count <= 10
        or re.fullmatch(r"[A-J]", str(task.ground_truth).strip()) is None
    ):
        return predicted

    candidate = predicted.strip()
    wrapper = re.fullmatch(r"\\text\{([^{}]*)\}", candidate)
    if wrapper:
        candidate = wrapper.group(1).strip()
    label = re.fullmatch(r"([A-J])|\(([A-J])\)", candidate)
    if label:
        choice = label.group(1) or label.group(2)
        return choice if choice in "ABCDEFGHIJ"[:count] else predicted

    answer = re.fullmatch(r"\(([A-J])\)\s+(.+)", candidate)
    if answer is None:
        return predicted
    options = re.findall(r"(?m)^\(([A-J])\) (.+)$", task.problem)
    if [label for label, _ in options] != list("ABCDEFGHIJ"[:count]):
        return predicted
    choice, text = answer.groups()
    option = dict(options).get(choice)
    if option is not None and normalize_math_text(text) == normalize_math_text(option):
        return choice
    return predicted


def _normalize_single_choice_label(expected_norm: str, predicted_norm: str) -> tuple[str, str]:
    """Accept '(A)' when the expected answer is the single option label 'A'."""
    expected_match = _SINGLE_CHOICE_RE.fullmatch(expected_norm)
    if not expected_match or expected_norm.startswith("("):
        return expected_norm, predicted_norm

    predicted_match = _SINGLE_CHOICE_RE.fullmatch(predicted_norm)
    if not predicted_match:
        return expected_norm, predicted_norm

    return expected_match.group(1), predicted_match.group(1)


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
        predicted_norm = normalize_math_text(_declared_choice_answer(task, predicted_raw))
        expected_norm, predicted_norm = _normalize_single_choice_label(
            expected_norm,
            predicted_norm,
        )

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
