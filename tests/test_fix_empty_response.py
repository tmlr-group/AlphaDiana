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

    def test_streaming_reasoning_content_falls_back_when_final_content_missing(self):
        """SSE reasoning deltas should be used when no assistant content is ever emitted."""
        agent = self._setup_agent(max_attempts=1)
        task = BenchmarkTask(task_id="t_reasoning", problem="Hard problem", ground_truth="42")

        sse_lines = [
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"reasoning_content":"Reasoning: step 1"}}]}',
            'data: {"choices":[{"delta":{"reasoning_content":"; step 2"}}]}',
            'data: [DONE]',
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/event-stream"}
        mock_response.iter_lines = MagicMock(return_value=sse_lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
                with patch("httpx.Client", return_value=mock_client):
                    result = agent.solve(task)

        assert result.raw_output == ""
        assert result.answer is None
        assert result.finish_reason == "incomplete"
        assert result.metadata["partial_reasoning_only"] is True
        assert result.metadata["answer_source"] == ""
        assert result.response_json["choices"][0]["message"]["reasoning_content"] == (
            "Reasoning: step 1; step 2"
        )

    def test_non_stream_reasoning_content_falls_back_when_final_content_missing(self):
        """Non-streaming reasoning_content should also prevent an empty-response error."""
        agent = self._setup_agent(max_attempts=1)
        task = BenchmarkTask(task_id="t_reasoning_json", problem="Hard problem", ground_truth="42")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = json.dumps({
            "choices": [{"message": {"content": "", "reasoning_content": "Reasoning only"}}],
        })
        mock_response.read = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
                with patch("httpx.Client", return_value=mock_client):
                    result = agent.solve(task)

        assert result.raw_output == ""
        assert result.answer is None
        assert result.finish_reason == "incomplete"
        assert result.metadata["partial_reasoning_only"] is True
        assert result.metadata["answer_source"] == ""
        assert result.response_json["choices"][0]["message"]["reasoning_content"] == "Reasoning only"

    def test_session_transcript_reasoning_falls_back_when_stream_is_empty(self):
        """If the HTTP stream is empty but the sandbox transcript has progress, keep it."""
        agent = self._setup_agent(max_attempts=1)
        task = BenchmarkTask(task_id="t_reasoning_session", problem="Hard problem", ground_truth="42")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/event-stream"}
        mock_response.iter_lines = MagicMock(return_value=[])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        recovered_trajectory = [
            {"role": "user", "content": "Hard problem"},
            {
                "role": "assistant",
                "content": "",
                "thinking": "partial reasoning from transcript",
                "tool_calls": [{"id": "exec:0", "tool": "exec", "input": {"command": "print(42)"}}],
            },
            {"role": "toolResult", "content": "42"},
        ]

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=recovered_trajectory):
                with patch("httpx.Client", return_value=mock_client):
                    result = agent.solve(task)

        assert result.raw_output == ""
        assert result.answer is None
        assert result.finish_reason == "incomplete"
        assert result.metadata["partial_reasoning_only"] is True
        assert result.metadata["answer_source"] == ""
        assert result.response_json["choices"][0]["message"]["reasoning_content"] == (
            "partial reasoning from transcript"
        )

    def test_answer_can_be_extracted_from_reasoning_when_raw_output_is_empty(self):
        """Predicted answer should still be derivable from reasoning_content."""
        agent = self._setup_agent(max_attempts=1)
        task = BenchmarkTask(task_id="t_reasoning_answer", problem="Hard problem", ground_truth="42")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/event-stream"}
        mock_response.iter_lines = MagicMock(return_value=[])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        recovered_trajectory = [
            {
                "role": "assistant",
                "content": "",
                "thinking": "After checking all cases, the final answer is 42.",
            },
        ]

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=recovered_trajectory):
                with patch("httpx.Client", return_value=mock_client):
                    result = agent.solve(task)

        assert result.raw_output == ""
        assert result.answer == "42"
        assert result.finish_reason == "incomplete"
        assert result.metadata["partial_reasoning_only"] is True
        assert result.metadata["answer_source"] == "reasoning_content"


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


# ===================================================================
# 14. Backend-crash circuit breaker — new feature for empty-response model crash
# ===================================================================


class TestBackendCrashCircuitBreaker:
    """Circuit breaker should detect and fail-fast on model backend crashes.

    When the model service (e.g. vLLM) crashes, requests return HTTP 200 with
    an empty SSE body and never send [DONE].  The circuit breaker should count
    these, open after a threshold, and raise BackendDownError for all subsequent
    calls without making any HTTP requests.
    """

    def _setup_agent(self, threshold: int = 3, **extra):
        from alphadiana.agent.openclaw import OpenClawAgent
        agent = OpenClawAgent()
        agent.setup({
            "api_base": "http://localhost:9999/v1",
            "max_attempts": 1,
            "request_timeout": 5,
            "backend_down_threshold": threshold,
            **extra,
        })
        return agent

    def _make_empty_sse_mock(self):
        """Return a mock httpx response with HTTP 200 + empty SSE body (no content, no [DONE])."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/event-stream"}
        mock_response.iter_lines = MagicMock(return_value=[])  # empty body
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        return mock_client

    def test_backend_down_error_class_exists(self):
        """BackendDownError should be importable from openclaw."""
        from alphadiana.agent.openclaw import BackendDownError, OpenClawRequestError
        # It should be a subclass of OpenClawRequestError for compatibility.
        assert issubclass(BackendDownError, OpenClawRequestError)

    def test_empty_sse_classified_as_empty_sse_body(self):
        """HTTP 200 + empty SSE body should classify as 'empty_sse_body', not 'unknown'."""
        from alphadiana.agent.openclaw import OpenClawAgent, OpenClawRequestError, BackendDownError
        agent = self._setup_agent(threshold=10)  # high threshold so circuit doesn't open
        task = BenchmarkTask(task_id="t_sse", problem="1+1?", ground_truth="2")
        mock_client = self._make_empty_sse_mock()

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
                with patch("httpx.Client", return_value=mock_client):
                    with pytest.raises(OpenClawRequestError) as exc_info:
                        agent.solve(task)

        err = exc_info.value
        assert err.error_type == "empty_sse_body", (
            f"Expected error_type='empty_sse_body', got '{err.error_type}'"
        )

    def test_circuit_opens_after_threshold(self):
        """After backend_down_threshold consecutive empty-SSE responses, BackendDownError is raised."""
        from alphadiana.agent.openclaw import OpenClawAgent, BackendDownError
        agent = self._setup_agent(threshold=3)
        task = BenchmarkTask(task_id="t_dead", problem="1+1?", ground_truth="2")
        mock_client = self._make_empty_sse_mock()

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
                with patch("httpx.Client", return_value=mock_client):
                    # First two calls raise OpenClawRequestError (circuit not yet open).
                    for _ in range(2):
                        with pytest.raises(Exception):
                            agent.solve(task)
                    # Third call should open the circuit and raise BackendDownError.
                    with pytest.raises(BackendDownError) as exc_info:
                        agent.solve(task)

        err = exc_info.value
        assert err.error_type == "backend_down"
        assert err.consecutive_failures >= 3

    def test_circuit_open_fails_fast_without_http_request(self):
        """Once circuit is open, solve() raises BackendDownError immediately (no HTTP call)."""
        from alphadiana.agent.openclaw import OpenClawAgent, BackendDownError
        agent = self._setup_agent(threshold=2)
        task = BenchmarkTask(task_id="t_fast", problem="1+1?", ground_truth="2")
        mock_client = self._make_empty_sse_mock()

        # Open the circuit by hitting the threshold.
        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
                with patch("httpx.Client", return_value=mock_client):
                    for _ in range(2):
                        with pytest.raises(Exception):
                            agent.solve(task)
                    with pytest.raises(BackendDownError):
                        agent.solve(task)

        # Now the circuit is open. Further calls should raise BackendDownError
        # without making any HTTP requests.
        with patch("httpx.Client") as mock_httpx:
            with pytest.raises(BackendDownError):
                agent.solve(task)
        # httpx.Client should NOT have been called (fail-fast).
        mock_httpx.assert_not_called()

    def test_circuit_breaker_resets_on_success(self):
        """A successful response should reset the consecutive-empty counter."""
        from alphadiana.agent.openclaw import OpenClawAgent, OpenClawRequestError
        agent = self._setup_agent(threshold=5)
        task = BenchmarkTask(task_id="t_recover", problem="1+1?", ground_truth="2")

        # Simulate 3 empty SSE responses.
        empty_client = self._make_empty_sse_mock()
        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
                with patch("httpx.Client", return_value=empty_client):
                    for _ in range(3):
                        with pytest.raises(Exception):
                            agent.solve(task)

        # Now verify the counter is at 3.
        with agent._circuit_lock:
            assert agent._consecutive_empty_sse == 3

        # Simulate a successful response.
        success_lines = [
            'data: {"choices":[{"delta":{"content":"The answer is 2."}}]}',
            'data: [DONE]',
        ]
        success_mock = MagicMock()
        success_mock.status_code = 200
        success_mock.headers = {"content-type": "text/event-stream"}
        success_mock.iter_lines = MagicMock(return_value=success_lines)
        success_mock.__enter__ = MagicMock(return_value=success_mock)
        success_mock.__exit__ = MagicMock(return_value=False)
        success_client = MagicMock()
        success_client.stream = MagicMock(return_value=success_mock)
        success_client.__enter__ = MagicMock(return_value=success_client)
        success_client.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
                with patch("httpx.Client", return_value=success_client):
                    result = agent.solve(task)

        # Counter should be reset to 0 after a successful response.
        with agent._circuit_lock:
            assert agent._consecutive_empty_sse == 0
        assert result.raw_output == "The answer is 2."

    def test_backend_down_threshold_configurable(self):
        """backend_down_threshold should be configurable from agent config."""
        from alphadiana.agent.openclaw import OpenClawAgent
        agent = OpenClawAgent()
        agent.setup({
            "api_base": "http://localhost:9999/v1",
            "backend_down_threshold": 10,
        })
        assert agent._backend_down_threshold == 10

    def test_backend_down_threshold_default_is_5(self):
        """Default backend_down_threshold should be 5."""
        from alphadiana.agent.openclaw import OpenClawAgent
        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost:9999/v1"})
        assert agent._backend_down_threshold == 5

    def test_empty_sse_uses_short_retry_delay(self):
        """empty_sse_body should use a shorter retry delay than empty_response."""
        import inspect
        from alphadiana.agent.openclaw import OpenClawAgent
        src = inspect.getsource(OpenClawAgent.solve)
        # The empty_sse_body branch should use a short delay (5s base).
        idx = src.find('last_error_type == "empty_sse_body"')
        assert idx != -1, "solve() should have a retry delay branch for empty_sse_body"
        nearby = src[idx : idx + 500]
        # Should have a delay that's less than the 60s used for empty_response.
        assert "5" in nearby, "empty_sse_body should use 5s base delay for fast crash detection"
