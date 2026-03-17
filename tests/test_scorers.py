"""Tests for scorer implementations (NumericScorer, ExactMatchScorer)."""

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


class TestExactMatchScorer:
    """Verify ExactMatchScorer scoring logic."""

    def test_correct(self):
        """Exact match: '42' vs '42'."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
        assert result.correct is True
        assert result.score == 1.0

    def test_incorrect(self):
        """Mismatch: '42' vs '43'."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("43"))
        assert result.correct is False
        assert result.score == 0.0

    def test_whitespace_normalization(self):
        """Whitespace normalization: ' 42 ' vs '42'."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse(" 42 "))
        assert result.correct is True
        assert result.score == 1.0

    def test_case_insensitive(self):
        """Case insensitive: 'Hello' vs 'hello'."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("Hello"), _FakeResponse("hello"))
        assert result.correct is True

    def test_trailing_period(self):
        """Trailing period removed: '42.' vs '42'."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("42."))
        assert result.correct is True

    def test_boxed_extraction(self):
        r"""Extract answer from \boxed{42}."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("\\boxed{42}"))
        assert result.correct is True
        assert result.score == 1.0

    def test_fraction_normalization(self):
        r"""Simple LaTeX fraction treated as equivalent to plain fraction literal."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("1/2"), _FakeResponse(r"\frac{1}{2}"))
        assert result.correct is True

    def test_answer_prefix_normalization(self):
        """Extract Final Answer line."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("Final Answer: 42"))
        assert result.correct is True

    def test_scorer_name(self):
        """scorer name is 'exact_match'."""
        scorer = ExactMatchScorer()
        assert scorer.name == "exact_match"

    def test_result_has_expected_and_predicted(self):
        """ScoreResult contains expected and predicted."""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
        assert result.expected == "42"
        assert result.predicted == "42"


class TestNumericScorer:
    """Verify NumericScorer scoring logic."""

    def test_exact_integer(self):
        """Exact integer match."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
        assert result.correct is True
        assert result.score == 1.0

    def test_tolerance(self):
        """Match within tolerance: 3.14159 vs 3.14160 (tolerance=1e-4)."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-4})
        result = scorer.score(_FakeTask("3.14159"), _FakeResponse("3.14160"))
        assert result.correct is True
        assert result.score == 1.0

    def test_outside_tolerance(self):
        """Outside tolerance: 3.14 vs 3.15 (tolerance=1e-6)."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("3.14"), _FakeResponse("3.15"))
        assert result.correct is False

    def test_boxed_extraction(self):
        r"""Extract number from \boxed{42}."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("\\boxed{42}"))
        assert result.correct is True
        assert result.score == 1.0

    def test_float_as_integer(self):
        """Float representation of integer: '42.0' vs '42'."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("42.0"))
        assert result.correct is True

    def test_leading_zeros(self):
        """Leading zeros: '042' vs '42'."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("042"))
        assert result.correct is True

    def test_unparseable_returns_incorrect(self):
        """Unparseable as number returns incorrect."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("abc"))
        assert result.correct is False
        assert result.score == 0.0

    def test_fraction(self):
        """Fraction: '3/4' vs '0.75'."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("0.75"), _FakeResponse("3/4"))
        assert result.correct is True

    def test_latex_fraction(self):
        r"""LaTeX fraction: '\frac{3}{4}' vs '0.75'."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("0.75"), _FakeResponse(r"\frac{3}{4}"))
        assert result.correct is True

    def test_percentage(self):
        """Percentage: '75%' vs '0.75'."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("0.75"), _FakeResponse("75%"))
        assert result.correct is True

    def test_answer_prefix_numeric(self):
        """Extract number from Final Answer prefix."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("Final Answer: 42."))
        assert result.correct is True

    def test_thousands_separator(self):
        """Thousands separator: '1,000' vs '1000'."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("1000"), _FakeResponse("1,000"))
        assert result.correct is True

    def test_scorer_name(self):
        """scorer name is 'numeric'."""
        scorer = NumericScorer()
        assert scorer.name == "numeric"

    def test_default_tolerance(self):
        """Default tolerance is 1e-6."""
        scorer = NumericScorer()
        assert scorer._tolerance == 1e-6

    def test_zero_values(self):
        """0 vs 0 match."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("0"), _FakeResponse("0"))
        assert result.correct is True

    def test_negative_numbers(self):
        """Negative number comparison."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("-42"), _FakeResponse("-42"))
        assert result.correct is True

    def test_result_metadata(self):
        """ScoreResult metadata contains expected_num and predicted_num."""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
        assert result.metadata["expected_num"] == 42.0
        assert result.metadata["predicted_num"] == 42.0
        assert result.metadata["expected_normalized"] == "42"
        assert result.metadata["predicted_normalized"] == "42"
