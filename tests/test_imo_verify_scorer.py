from __future__ import annotations

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.scorer.imo_verify_scorer import ImoVerifyScorer


def _task(expected: str) -> BenchmarkTask:
    return BenchmarkTask(task_id="imo_case", problem="P", ground_truth=expected)


def _response(predicted: str) -> AgentResponse:
    return AgentResponse(answer=predicted)


def _scorer() -> ImoVerifyScorer:
    scorer = ImoVerifyScorer()
    scorer.setup({})
    return scorer


def test_blocks_symbolic_numeric_false_positive_sqrt():
    scorer = _scorer()
    expected = r"\lfloor \log_2 a \rfloor + 1"
    predicted = r"\lfloor \sqrt{2a - 3} \rfloor + 1"

    result = scorer.score(_task(expected), _response(predicted))

    assert result.correct is False
    assert result.metadata["method"] in {"blocked_free_var", "no_match"}


def test_blocks_symbolic_numeric_false_positive_a_over_2():
    scorer = _scorer()
    expected = r"\lfloor \log_2 a \rfloor + 1"
    predicted = r"\lfloor a/2 \rfloor + 1"

    result = scorer.score(_task(expected), _response(predicted))

    assert result.correct is False
    assert result.metadata["method"] in {"blocked_free_var", "no_match"}


def test_multivalue_or_equivalence():
    scorer = _scorer()
    expected = r"g(x)=2x^3+c, g(x)=-2x^3+c"
    predicted = r"g(x)=2x^3+c or g(x)=-2x^3+c"

    result = scorer.score(_task(expected), _response(predicted))

    assert result.correct is True
    assert result.metadata["matched"] == 2
    assert result.metadata["predicted_total"] == 2


def test_multivalue_partial_is_false():
    scorer = _scorer()
    expected = r"g(x)=2x^3+c, g(x)=-2x^3+c"
    predicted = r"g(x)=2x^3+c"

    result = scorer.score(_task(expected), _response(predicted))

    assert result.correct is False
    assert result.metadata["predicted_total"] == 1


def test_multivalue_same_part_cannot_match_twice():
    scorer = _scorer()
    expected = r"-2/3, 2/3"
    predicted = r"2/3, 2/3"

    result = scorer.score(_task(expected), _response(predicted))

    assert result.correct is False
    assert result.metadata["matched"] == 1


def test_numeric_literals_still_work():
    scorer = _scorer()

    result_1 = scorer.score(_task("045"), _response("45"))
    result_2 = scorer.score(_task("3/4"), _response("0.75"))

    assert result_1.correct is True
    assert result_2.correct is True
