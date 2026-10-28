from __future__ import annotations

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.observations import measure_observations
from alphadiana.harness.base import AgentResponse
from alphadiana.engine.observations import AnswerLengthMetric
from alphadiana.engine.observations import EvaluationMetric, MetricResult
from alphadiana.scorer.base import ScoreResult


def _result_objects() -> tuple[BenchmarkTask, AgentResponse, ScoreResult]:
    return (
        BenchmarkTask(task_id="demo", problem="do it", ground_truth="1"),
        AgentResponse(
            answer="1",
            raw_output="short answer",
            metadata={"agent_final_answer": "\u7b54\u6848 ok"},
        ),
        ScoreResult(correct=True, score=1.0, expected="1", predicted="1"),
    )


def test_answer_length_records_text_shape_without_changing_score() -> None:
    task, response, score = _result_objects()
    metric = AnswerLengthMetric()
    metric.setup({"source": "final_answer"})

    measure_observations([metric], task, response, score)

    observation = response.evaluation_metrics["answer_length"]
    assert observation["value"] == 5
    assert observation["unit"] == "unicode_codepoints"
    assert observation["metadata"] == {
        "source": "final_answer",
        "utf8_bytes": 9,
        "words": 2,
        "lines": 1,
    }
    assert score.score == 1.0


def test_metric_failure_cannot_mutate_primary_result() -> None:
    class MutatingBrokenMetric(EvaluationMetric):
        @property
        def name(self) -> str:
            return "broken"

        def measure(self, task, response, score) -> MetricResult:
            task.problem = "mutated"
            response.answer = "0"
            score.score = 0.0
            raise RuntimeError("metric exploded")

    task, response, score = _result_objects()
    measure_observations([MutatingBrokenMetric()], task, response, score)

    assert task.problem == "do it"
    assert response.answer == "1"
    assert score.score == 1.0
    assert response.evaluation_metrics == {}
    assert response.evaluation_metric_errors == {"broken": "metric exploded"}
