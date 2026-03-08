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
    """验证 ExactMatchScorer 的评分逻辑。"""

    def test_correct(self):
        """完全匹配: '42' vs '42'。"""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
        assert result.correct is True
        assert result.score == 1.0

    def test_incorrect(self):
        """不匹配: '42' vs '43'。"""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("43"))
        assert result.correct is False
        assert result.score == 0.0

    def test_whitespace_normalization(self):
        """空白规范化: ' 42 ' vs '42'。"""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse(" 42 "))
        assert result.correct is True
        assert result.score == 1.0

    def test_case_insensitive(self):
        """大小写不敏感: 'Hello' vs 'hello'。"""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("Hello"), _FakeResponse("hello"))
        assert result.correct is True

    def test_trailing_period(self):
        """末尾句号移除: '42.' vs '42'。"""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("42."))
        assert result.correct is True

    def test_boxed_extraction(self):
        r"""从 \boxed{42} 提取答案。"""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("\\boxed{42}"))
        assert result.correct is True
        assert result.score == 1.0

    def test_scorer_name(self):
        """scorer name 为 'exact_match'。"""
        scorer = ExactMatchScorer()
        assert scorer.name == "exact_match"

    def test_result_has_expected_and_predicted(self):
        """ScoreResult 包含 expected 和 predicted。"""
        scorer = ExactMatchScorer()
        result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
        assert result.expected == "42"
        assert result.predicted == "42"


class TestNumericScorer:
    """验证 NumericScorer 的评分逻辑。"""

    def test_exact_integer(self):
        """整数精确匹配。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
        assert result.correct is True
        assert result.score == 1.0

    def test_tolerance(self):
        """容差范围内匹配: 3.14159 vs 3.14160 (tolerance=1e-4)。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-4})
        result = scorer.score(_FakeTask("3.14159"), _FakeResponse("3.14160"))
        assert result.correct is True
        assert result.score == 1.0

    def test_outside_tolerance(self):
        """超出容差范围: 3.14 vs 3.15 (tolerance=1e-6)。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("3.14"), _FakeResponse("3.15"))
        assert result.correct is False

    def test_boxed_extraction(self):
        r"""从 \boxed{42} 提取数字。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("\\boxed{42}"))
        assert result.correct is True
        assert result.score == 1.0

    def test_float_as_integer(self):
        """浮点表示的整数: '42.0' vs '42'。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("42.0"))
        assert result.correct is True

    def test_leading_zeros(self):
        """前导零: '042' vs '42'。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("042"))
        assert result.correct is True

    def test_unparseable_returns_incorrect(self):
        """无法解析为数字返回 incorrect。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("abc"))
        assert result.correct is False
        assert result.score == 0.0

    def test_fraction(self):
        """分数: '3/4' vs '0.75'。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("0.75"), _FakeResponse("3/4"))
        assert result.correct is True

    def test_percentage(self):
        """百分比: '75%' vs '0.75'。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("0.75"), _FakeResponse("75%"))
        assert result.correct is True

    def test_thousands_separator(self):
        """千位分隔符: '1,000' vs '1000'。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("1000"), _FakeResponse("1,000"))
        assert result.correct is True

    def test_scorer_name(self):
        """scorer name 为 'numeric'。"""
        scorer = NumericScorer()
        assert scorer.name == "numeric"

    def test_default_tolerance(self):
        """默认 tolerance 为 1e-6。"""
        scorer = NumericScorer()
        assert scorer._tolerance == 1e-6

    def test_zero_values(self):
        """0 vs 0 匹配。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("0"), _FakeResponse("0"))
        assert result.correct is True

    def test_negative_numbers(self):
        """负数比较。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("-42"), _FakeResponse("-42"))
        assert result.correct is True

    def test_result_metadata(self):
        """ScoreResult metadata 包含 expected_num 和 predicted_num。"""
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(_FakeTask("42"), _FakeResponse("42"))
        assert result.metadata["expected_num"] == 42.0
        assert result.metadata["predicted_num"] == 42.0
