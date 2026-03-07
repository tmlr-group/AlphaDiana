"""Tests for scorer implementations."""

import pytest

from alphadiana.scorer.exact_match import ExactMatchScorer
from alphadiana.scorer.numeric import NumericScorer


class _FakeTask:
    """Minimal task-like object for scorer testing."""

    def __init__(self, expected_answer):
        self.expected_answer = expected_answer
        self.ground_truth = expected_answer


class _FakeResponse:
    """Minimal response-like object for scorer testing."""

    def __init__(self, answer):
        self.answer = answer


def test_exact_match_correct():
    """Exact match: '42' vs '42' should be correct."""
    scorer = ExactMatchScorer()
    result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
    assert result.correct is True
    assert result.score == 1.0


def test_exact_match_incorrect():
    """Exact match: '42' vs '43' should be incorrect."""
    scorer = ExactMatchScorer()
    result = scorer.score(_FakeTask("42"), _FakeResponse("43"))
    assert result.correct is False
    assert result.score == 0.0


def test_exact_match_normalization():
    """Exact match with whitespace normalization: ' 42 ' vs '42'."""
    scorer = ExactMatchScorer()
    result = scorer.score(_FakeTask("42"), _FakeResponse(" 42 "))
    assert result.correct is True
    assert result.score == 1.0


def test_numeric_scorer_tolerance():
    """Numeric comparison within tolerance: 3.14159 vs 3.14160."""
    scorer = NumericScorer()
    scorer.setup({"tolerance": 1e-4})
    result = scorer.score(_FakeTask("3.14159"), _FakeResponse("3.14160"))
    assert result.correct is True
    assert result.score == 1.0


def test_numeric_scorer_boxed():
    r"""Numeric comparison with \\boxed{} extraction: \\boxed{42} vs 42."""
    scorer = NumericScorer()
    scorer.setup({"tolerance": 1e-6})
    result = scorer.score(_FakeTask("42"), _FakeResponse("\\boxed{42}"))
    assert result.correct is True
    assert result.score == 1.0
