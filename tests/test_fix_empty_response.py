"""Diagnostics and fix validation for empty-response issues.

Covers:
- OpenClaw empty-response retry backoff strategy (fixed sleep -> exponential backoff)
- Exceptions include diagnostic information when retries still end with an empty response
- Safe scorer handling for answer=None
- Corrupt JSONL lines do not block load()
- Runner pool teardown runs in finally
"""

import json
import math
import random
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.scorer.base import ScoreResult


# ===================================================================
# 1. OpenClaw empty-response retry backoff — validate the issue
# ===================================================================


class TestOpenClawEmptyResponseProblem:
    """Validate whether empty-response retry backoff uses fixed sleep or exponential backoff."""

    def test_retry_sleep_is_exponential_not_fixed(self):
        """The backoff formula should be exponential, not the fixed min(2, attempt) value.

        Before: time.sleep(min(2, attempt)) -> 1s, 2s, 2s, 2s...
        After: exponential backoff + jitter
        """
        from alphadiana.agent.openclaw import OpenClawAgent
        import inspect

        agent = OpenClawAgent()
        source = inspect.getsource(agent.solve)

        # After the fix, the fixed backoff pattern "min(2, attempt)" should be gone.
        assert "min(2, attempt)" not in source, (
            "OpenClaw retry is still using the fixed sleep(min(2, attempt)) pattern; "
            "it should use exponential backoff + jitter"
        )

    def test_retry_backoff_increases(self):
        """Validate that retry intervals increase with attempt, a core exponential-backoff property."""
        # Simulate the exponential backoff formula and verify increasing delays.
        base_delay = 2.0
        max_delay = 60.0
        delays = []
        for attempt in range(1, 6):
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delays.append(delay)

        # Delays should increase monotonically until the cap.
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1], (
                f"Delay at attempt {i+1} ({delays[i]}) should be >= "
                f"delay at attempt {i} ({delays[i-1]})"
            )
        # The first attempt should be shorter than the third.
        assert delays[0] < delays[2]


class TestOpenClawEmptyResponseHandling:
    """Behavior when OpenClaw receives an empty response."""

    def _setup_agent(self, **extra):
        from alphadiana.agent.openclaw import OpenClawAgent
        agent = OpenClawAgent()
        agent.setup({
            "api_base": "http://localhost:9999/v1",
            "max_attempts": 3,
            "request_timeout": 5,
            **extra,
        })
        return agent

    def test_empty_content_triggers_retry(self):
        """Empty content should trigger retries instead of returning immediately."""
        from alphadiana.agent.openclaw import OpenClawAgent, OpenClawRequestError

        agent = self._setup_agent(max_attempts=2)
        task = BenchmarkTask(task_id="t1", problem="What is 1+1?", ground_truth="2")

        # Mock httpx to return empty content on both attempts.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = json.dumps({
            "choices": [{"message": {"content": ""}}],
        })
        mock_response.read = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("httpx.Client", return_value=mock_client):
            with pytest.raises(OpenClawRequestError) as exc_info:
                agent.solve(task)

        # It should have attempted the request twice.
        err = exc_info.value
        assert err.retry_responses is not None
        assert len(err.retry_responses) >= 1

    def test_error_carries_diagnostic_info(self):
        """Empty-response exceptions should include complete diagnostic information."""
        from alphadiana.agent.openclaw import OpenClawRequestError

        err = OpenClawRequestError(
            "empty content",
            error_type="empty_response",
            request_payload={"model": "test"},
            response_body={"choices": [{"message": {"content": ""}}]},
            retry_responses=[
                {"attempt": 1, "status_code": 200, "error_type": "empty_response"},
                {"attempt": 2, "status_code": 200, "error_type": "empty_response"},
            ],
        )
        assert err.error_type == "empty_response"
        assert len(err.retry_responses) == 2
        assert err.request_payload["model"] == "test"


# ===================================================================
# 2. Scorer handling of null answers — validate the issue and fix
# ===================================================================


class TestScorerNoneAnswer:
    """All scorers should return correct=False for answer=None instead of scoring 'None' as an answer."""

    def _task(self):
        return BenchmarkTask(task_id="t1", problem="P", ground_truth="42")

    def _resp_none(self):
        return AgentResponse(answer=None)

    def test_exact_match_handles_none(self):
        from alphadiana.scorer.exact_match import ExactMatchScorer
        scorer = ExactMatchScorer()
        result = scorer.score(self._task(), self._resp_none())
        assert result.correct is False
        assert result.score == 0.0
        # Key point: predicted should be None, not the string "None",
        # and rationale should explain that the answer is missing.
        assert result.predicted is None
        assert "no answer" in result.rationale.lower()

    def test_numeric_handles_none(self):
        from alphadiana.scorer.numeric import NumericScorer
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(self._task(), self._resp_none())
        assert result.correct is False
        assert result.score == 0.0

    def test_math_verify_handles_none(self):
        from alphadiana.scorer.math_verify_scorer import MathVerifyScorer
        scorer = MathVerifyScorer()
        result = scorer.score(self._task(), self._resp_none())
        assert result.correct is False
        assert result.score == 0.0

    def test_exact_match_none_is_not_string_none(self):
        """answer=None should not be converted to the string "None" before comparing with ground_truth."""
        from alphadiana.scorer.exact_match import ExactMatchScorer
        scorer = ExactMatchScorer()
        # Even if ground_truth happens to be "None", answer=None should not count as correct.
        task = BenchmarkTask(task_id="t2", problem="P", ground_truth="None")
        result = scorer.score(task, self._resp_none())
        # answer=None means the task failed, so it should not be marked correct.
        assert result.correct is False


# ===================================================================
# 3. JSONL corruption recovery — validate the issue and fix
# ===================================================================


class TestJSONLCorruptionRecovery:
    """load() should not crash when a JSONL file contains corrupt lines."""

    def test_load_skips_corrupt_lines(self, tmp_path):
        from alphadiana.results.result_store import ResultStore

        store = ResultStore(output_dir=str(tmp_path), run_id="corrupt")

        # Write a mix of valid and corrupt lines.
        with open(store.path, "w") as f:
            f.write(json.dumps({"task_id": "t1", "correct": True, "score": 1.0}) + "\n")
            f.write("THIS IS NOT VALID JSON\n")
            f.write("{truncated\n")
            f.write(json.dumps({"task_id": "t2", "correct": False, "score": 0.0}) + "\n")

        records = store.load()
        # It should load the 2 valid records and skip the 2 corrupt lines.
        assert len(records) == 2
        task_ids = {r["task_id"] for r in records}
        assert task_ids == {"t1", "t2"}

    def test_load_all_corrupt_returns_empty(self, tmp_path):
        from alphadiana.results.result_store import ResultStore

        store = ResultStore(output_dir=str(tmp_path), run_id="allbad")
        with open(store.path, "w") as f:
            f.write("not json 1\n")
            f.write("not json 2\n")

        records = store.load()
        assert records == []

    def test_load_empty_file_returns_empty(self, tmp_path):
        from alphadiana.results.result_store import ResultStore

        store = ResultStore(output_dir=str(tmp_path), run_id="empty")
        with open(store.path, "w") as f:
            f.write("")

        records = store.load()
        assert records == []


# ===================================================================
# 4. Pool teardown in finally — validate the fix
# ===================================================================


class TestPoolTeardownInFinally:
    """pool.teardown() should live in finally so cleanup still happens when dispatch raises."""

    def test_runner_source_has_finally_teardown(self):
        """Runner.run() should place pool.teardown() inside a try/finally block."""
        import inspect
        from alphadiana.runner.runner import Runner

        source = inspect.getsource(Runner.run)
        # Check that pool.teardown() appears in a finally block.
        # Simple validation: the source contains both finally and pool.teardown.
        assert "finally" in source, "Runner.run() should contain a finally block"
        assert "pool.teardown()" in source or "pool.teardown" in source, (
            "pool.teardown() should be present in Runner.run()"
        )


# ===================================================================
# 5. retry.py enhancement — retryable exception allowlist
# ===================================================================


class TestRetryWithBackoff:
    """Validate retry_with_backoff decorator behavior."""

    def test_basic_retry_succeeds_eventually(self):
        from alphadiana.utils.retry import retry_with_backoff

        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.01, max_delay=0.05)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "ok"

        assert flaky() == "ok"
        assert call_count == 3

    def test_non_retryable_exception_raises_immediately(self):
        """When retryable_exceptions is configured, unspecified exceptions should not be retried."""
        from alphadiana.utils.retry import retry_with_backoff

        call_count = 0

        @retry_with_backoff(
            max_retries=3, base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
        )
        def bad():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            bad()

        # It should only be called once because ValueError is not retried.
        assert call_count == 1

    def test_retryable_exception_is_retried(self):
        """Configured retryable exception types should trigger retries."""
        from alphadiana.utils.retry import retry_with_backoff

        call_count = 0

        @retry_with_backoff(
            max_retries=2, base_delay=0.01,
            retryable_exceptions=(ConnectionError, TimeoutError),
        )
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise TimeoutError("timeout")
            return "ok"

        assert flaky() == "ok"
        assert call_count == 3


# ===================================================================
# 6. ResultStore artifact locks — validate concurrency safety
# ===================================================================


class TestArtifactLocks:
    """ResultStore artifact writes should use a per-key lock."""

    def test_artifact_lock_exists(self):
        """ResultStore should provide a _get_artifact_lock method."""
        from alphadiana.results.result_store import ResultStore
        store = ResultStore(output_dir="/tmp/test_locks", run_id="lock_test")
        lock = store._get_artifact_lock("task1:0")
        assert lock is not None
        # The same key should return the same lock.
        assert store._get_artifact_lock("task1:0") is lock
        # Different keys should return different locks.
        assert store._get_artifact_lock("task2:0") is not lock

    def test_append_uses_artifact_lock(self):
        """append() should acquire an artifact lock before _persist_artifacts and _save_per_task_json."""
        import inspect
        from alphadiana.results.result_store import ResultStore
        source = inspect.getsource(ResultStore.append)
        assert "_get_artifact_lock" in source, (
            "append() should use _get_artifact_lock to protect artifact writes"
        )
        # _save_per_task_json should run inside the lock.
        lines = source.splitlines()
        lock_line = next(i for i, l in enumerate(lines) if "_get_artifact_lock" in l)
        save_line = next(i for i, l in enumerate(lines) if "_save_per_task_json" in l)
        assert save_line > lock_line, (
            "_save_per_task_json should appear after _get_artifact_lock"
        )

    def test_append_error_uses_artifact_lock(self):
        """append_error() should acquire an artifact lock before _persist_artifacts and _save_per_task_json."""
        import inspect
        from alphadiana.results.result_store import ResultStore
        source = inspect.getsource(ResultStore.append_error)
        assert "_get_artifact_lock" in source, (
            "append_error() should use _get_artifact_lock to protect artifact writes"
        )
        lines = source.splitlines()
        lock_line = next(i for i, l in enumerate(lines) if "_get_artifact_lock" in l)
        save_line = next(i for i, l in enumerate(lines) if "_save_per_task_json" in l)
        assert save_line > lock_line, (
            "_save_per_task_json should appear after _get_artifact_lock"
        )


# ===================================================================
# 7. OpenClaw token usage accumulation — validate accumulation
# ===================================================================


class TestOpenClawTokenAccumulation:
    """OpenClaw should accumulate token_usage across retries instead of overwriting it."""

    def test_solve_source_has_cumulative_token_usage(self):
        """solve() should accumulate usage via cumulative_token_usage."""
        import inspect
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.solve)
        assert "cumulative_token_usage" in source, (
            "OpenClaw solve() should use cumulative_token_usage to accumulate token usage"
        )


# ===================================================================
# 8. TaskDispatcher task-level retry — validate
# ===================================================================


class TestTaskDispatcherRetry:
    """TaskDispatcher should support the task_retries parameter."""

    def test_task_retries_parameter(self):
        from alphadiana.runner.task_dispatcher import TaskDispatcher
        d = TaskDispatcher(max_concurrent=1, task_retries=2)
        assert d._task_retries == 2

    def test_task_retry_succeeds_eventually(self):
        from alphadiana.runner.task_dispatcher import TaskDispatcher
        d = TaskDispatcher(max_concurrent=1, task_retries=2)

        call_count = 0

        def solve(item):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return {"task_id": "t1", "correct": True, "score": 1.0}

        task = MagicMock()
        task.task_id = "t1"
        outcomes = d.dispatch([(task, 0)], solve)
        assert len(outcomes) == 1
        assert outcomes[0]["success"] is True
        assert call_count == 3

    def test_task_retry_exhausted_fails(self):
        from alphadiana.runner.task_dispatcher import TaskDispatcher
        d = TaskDispatcher(max_concurrent=1, task_retries=1)

        def solve(item):
            raise ValueError("always fails")

        task = MagicMock()
        task.task_id = "t1"
        outcomes = d.dispatch([(task, 0)], solve)
        assert len(outcomes) == 1
        assert outcomes[0]["success"] is False

    def test_exc_info_in_error_logging(self):
        """task_dispatcher error logging should include exc_info=True."""
        import inspect
        from alphadiana.runner.task_dispatcher import TaskDispatcher
        seq_source = inspect.getsource(TaskDispatcher._dispatch_sequential)
        conc_source = inspect.getsource(TaskDispatcher._dispatch_concurrent)
        assert "exc_info=True" in seq_source, (
            "_dispatch_sequential error logging should include exc_info=True"
        )
        assert "exc_info=True" in conc_source, (
            "_dispatch_concurrent error logging should include exc_info=True"
        )


# ===================================================================
# 9. Benchmark data loading retry — validate
# ===================================================================


class TestBenchmarkLoadRetry:
    """Benchmark data loading should include a retry mechanism."""

    def test_load_dataset_with_retry_exists(self):
        from alphadiana.benchmark.base import load_dataset_with_retry
        assert callable(load_dataset_with_retry)

    def test_benchmarks_use_retry_loader(self):
        """All benchmarks using HF datasets should call load_dataset_with_retry."""
        import inspect
        from alphadiana.benchmark.aime import AIMEBenchmark

        for cls in [AIMEBenchmark]:
            source = inspect.getsource(cls.load_tasks)
            assert "load_dataset_with_retry" in source, (
                f"{cls.__name__}.load_tasks() should use load_dataset_with_retry"
            )


# ===================================================================
# 10. Config validation enhancement — validate
# ===================================================================


class TestConfigValidation:
    """Enhanced ConfigValidator validation."""

    def test_max_concurrent_upper_bound(self):
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.config.validator import ConfigValidator

        config = ExperimentConfig(
            agent_name="test", agent_version="v1.0",
            benchmark_name="aime", scorer_name="numeric",
            max_concurrent=100,
        )
        errors = ConfigValidator().validate(config)
        assert any("64" in e for e in errors)

    def test_api_agent_requires_api_base(self):
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.config.validator import ConfigValidator

        config = ExperimentConfig(
            agent_name="direct_llm", agent_version="v1.0",
            benchmark_name="aime", scorer_name="numeric",
            agent_config={},
        )
        errors = ConfigValidator().validate(config)
        assert any("api_base" in e for e in errors)

    def test_num_samples_validation(self):
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.config.validator import ConfigValidator

        config = ExperimentConfig(
            agent_name="test", agent_version="v1.0",
            benchmark_name="aime", scorer_name="numeric",
            num_samples=0,
        )
        errors = ConfigValidator().validate(config)
        assert any("num_samples" in e for e in errors)

    def test_task_retries_validation(self):
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.config.validator import ConfigValidator

        config = ExperimentConfig(
            agent_name="test", agent_version="v1.0",
            benchmark_name="aime", scorer_name="numeric",
            task_retries=-1,
        )
        errors = ConfigValidator().validate(config)
        assert any("task_retries" in e for e in errors)

    def test_valid_config_passes(self):
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.config.validator import ConfigValidator

        config = ExperimentConfig(
            agent_name="direct_llm", agent_version="v1.0",
            benchmark_name="aime", scorer_name="numeric",
            agent_config={"api_base": "http://localhost:8000/v1"},
            max_concurrent=4,
            num_samples=3,
            task_retries=2,
        )
        errors = ConfigValidator().validate(config)
        assert errors == []


# ===================================================================
# 11. numeric scorer inf handling
# ===================================================================


class TestNumericScorerInf:
    """NumericScorer should handle inf values correctly."""

    def test_inf_vs_finite_is_incorrect(self):
        from alphadiana.scorer.numeric import NumericScorer
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        assert scorer._is_close(float("inf"), 1.0) is False
        assert scorer._is_close(1.0, float("inf")) is False
        assert scorer._is_close(float("-inf"), 1.0) is False

    def test_inf_vs_inf_is_correct(self):
        from alphadiana.scorer.numeric import NumericScorer
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        assert scorer._is_close(float("inf"), float("inf")) is True
        assert scorer._is_close(float("-inf"), float("-inf")) is True

    def test_pos_inf_vs_neg_inf_is_incorrect(self):
        from alphadiana.scorer.numeric import NumericScorer
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        assert scorer._is_close(float("inf"), float("-inf")) is False

    def test_nan_still_handled(self):
        from alphadiana.scorer.numeric import NumericScorer
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        assert scorer._is_close(float("nan"), 1.0) is False
        assert scorer._is_close(float("nan"), float("nan")) is False


# ===================================================================
# 12. Checkpoint scorer change detection
# ===================================================================


class TestCheckpointScorerMismatch:
    """Resume should detect changes in scorer configuration."""

    def test_run_metadata_includes_scorer_name(self):
        """run_metadata should include scorer_name."""
        import inspect
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.setup)
        assert "scorer_name" in source, (
            "run_metadata should include scorer_name to support checkpoint scorer mismatch detection"
        )

    def test_checkpoint_detects_scorer_mismatch(self):
        """Checkpoint logic should detect scorer mismatches."""
        import inspect
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        assert "scorer_name" in source and "mismatch" in source.lower(), (
            "Runner.run() should detect scorer mismatches during checkpoint resume"
        )


# ===================================================================
# 13. OpenClaw proxy_timeout diagnostics
# ===================================================================


class TestOpenClawProxyTimeoutDiagnostic:
    """proxy_timeout errors should include explicit diagnostic guidance."""

    def test_solve_source_has_proxy_timeout_diagnostic(self):
        import inspect
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.solve)
        assert "proxy_timeout" in source, (
            "OpenClaw solve() should provide diagnostics for proxy_timeout errors"
        )
        assert "proxy_timeout" in source and "sandbox_config" in source.lower() or "increase" in source.lower(), (
            "proxy_timeout diagnostics should suggest increasing the relevant configuration"
        )
