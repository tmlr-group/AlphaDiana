import math

import pytest

from alphadiana.results.store import ResultStore
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.runner import _measure_observational_metrics
from alphadiana.harness.base import AgentResponse
from alphadiana.metrics.base import EvaluationMetric, MetricResult
from alphadiana.scorer.base import ScoreResult


@pytest.mark.parametrize('bad_result', [MetricResult(math.nan), MetricResult(1, metadata={'bad': {1}})])
def test_unpersistable_metric_does_not_lose_primary_score(tmp_path, bad_result):
    class InvalidMetric(EvaluationMetric):
        name = 'invalid'

        def measure(self, task, response, score):
            return bad_result

    task = BenchmarkTask('task', 'Return 1', '1')
    response = AgentResponse(answer='1')
    score = ScoreResult(True, 1.0, '1', '1')
    _measure_observational_metrics([InvalidMetric()], task, response, score)
    store = ResultStore(str(tmp_path), 'metric-boundary')
    store.append(task, response, score)
    record = store.load()[0]
    assert record['score'] == 1.0
    assert record['correct'] is True
    assert record['evaluation_metrics'] == {}
    assert 'invalid' in record['evaluation_metric_errors']
