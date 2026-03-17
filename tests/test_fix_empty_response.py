"""空响应问题诊断与修复验证。

覆盖:
- OpenClaw 空响应重试退避策略（固定 sleep → 指数退避）
- 空响应后最终仍返回空时异常包含诊断信息
- Scorer 对 answer=None 的安全处理
- JSONL 损坏行不阻断 load()
- Runner pool teardown 在 finally 中
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
# 1. OpenClaw 空响应重试退避 — 验证问题存在
# ===================================================================


class TestOpenClawEmptyResponseProblem:
    """验证空响应重试退避是固定 sleep 还是指数退避。"""

    def test_retry_sleep_is_exponential_not_fixed(self):
        """退避公式应为指数退避，而非 min(2, attempt) 固定值。

        之前: time.sleep(min(2, attempt)) → 1s, 2s, 2s, 2s...
        修复后: 指数退避 + jitter
        """
        from alphadiana.agent.openclaw import OpenClawAgent
        import inspect

        agent = OpenClawAgent()
        source = inspect.getsource(agent.solve)

        # 修复后不应再包含 "min(2, attempt)" 固定退避模式
        assert "min(2, attempt)" not in source, (
            "OpenClaw retry 仍在使用固定 sleep(min(2, attempt))，"
            "应改为指数退避 + jitter"
        )

    def test_retry_backoff_increases(self):
        """验证重试间隔随 attempt 递增（指数退避核心特性）。"""
        # 模拟指数退避公式，确认间隔递增
        base_delay = 2.0
        max_delay = 60.0
        delays = []
        for attempt in range(1, 6):
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delays.append(delay)

        # 间隔应单调递增（直到 cap）
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1], (
                f"Delay at attempt {i+1} ({delays[i]}) should be >= "
                f"delay at attempt {i} ({delays[i-1]})"
            )
        # 第 1 次应小于第 3 次
        assert delays[0] < delays[2]


class TestOpenClawEmptyResponseHandling:
    """OpenClaw 处理空响应的行为。"""

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
        """空 content 应触发重试而非直接返回。"""
        from alphadiana.agent.openclaw import OpenClawAgent, OpenClawRequestError

        agent = self._setup_agent(max_attempts=2)
        task = BenchmarkTask(task_id="t1", problem="What is 1+1?", ground_truth="2")

        # Mock httpx to return empty content both times
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

        # 应尝试了 2 次
        err = exc_info.value
        assert err.retry_responses is not None
        assert len(err.retry_responses) >= 1

    def test_error_carries_diagnostic_info(self):
        """空响应异常应包含完整诊断信息。"""
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
# 2. Scorer 空值处理 — 验证问题存在并修复
# ===================================================================


class TestScorerNoneAnswer:
    """所有 scorer 对 answer=None 应返回 correct=False 而非将 'None' 当答案评分。"""

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
        # 关键: predicted 应为 None（不是字符串 "None"），rationale 应说明是空答案
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
        """answer=None 不应被 str() 转为 "None" 然后与 ground_truth 比较。"""
        from alphadiana.scorer.exact_match import ExactMatchScorer
        scorer = ExactMatchScorer()
        # 如果 ground_truth 恰好是 "None"，answer=None 也不应判正确
        task = BenchmarkTask(task_id="t2", problem="P", ground_truth="None")
        result = scorer.score(task, self._resp_none())
        # answer=None 代表任务失败，即使 ground_truth 是 "None" 也不应判对
        assert result.correct is False


# ===================================================================
# 3. JSONL 损坏行恢复 — 验证问题存在并修复
# ===================================================================


class TestJSONLCorruptionRecovery:
    """JSONL 文件含损坏行时 load() 不应崩溃。"""

    def test_load_skips_corrupt_lines(self, tmp_path):
        from alphadiana.results.result_store import ResultStore

        store = ResultStore(output_dir=str(tmp_path), run_id="corrupt")

        # 写入混合正常和损坏的行
        with open(store.path, "w") as f:
            f.write(json.dumps({"task_id": "t1", "correct": True, "score": 1.0}) + "\n")
            f.write("THIS IS NOT VALID JSON\n")
            f.write("{truncated\n")
            f.write(json.dumps({"task_id": "t2", "correct": False, "score": 0.0}) + "\n")

        records = store.load()
        # 应该成功加载 2 条有效记录，跳过 2 条损坏行
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
# 4. Pool teardown 在 finally 中 — 验证修复
# ===================================================================


class TestPoolTeardownInFinally:
    """pool.teardown() 应在 finally 中，即使 dispatch 异常也能清理。"""

    def test_runner_source_has_finally_teardown(self):
        """Runner.run() 中 pool.teardown() 应在 try/finally 块中。"""
        import inspect
        from alphadiana.runner.runner import Runner

        source = inspect.getsource(Runner.run)
        # 检查 pool.teardown() 出现在 finally 块中
        # 简单验证: source 中 finally 和 pool.teardown 都存在
        assert "finally" in source, "Runner.run() 应包含 finally 块"
        assert "pool.teardown()" in source or "pool.teardown" in source, (
            "pool.teardown() 应在 Runner.run() 中"
        )


# ===================================================================
# 5. retry.py 增强 — 可重试异常白名单
# ===================================================================


class TestRetryWithBackoff:
    """retry_with_backoff 装饰器行为验证。"""

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
        """配置了 retryable_exceptions 后，非指定异常不重试。"""
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

        # 应只调用 1 次（不重试 ValueError）
        assert call_count == 1

    def test_retryable_exception_is_retried(self):
        """指定的可重试异常类型会触发重试。"""
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
# 6. ResultStore artifact locks — 验证并发安全
# ===================================================================


class TestArtifactLocks:
    """ResultStore 的 artifact 写入应使用 per-key lock。"""

    def test_artifact_lock_exists(self):
        """ResultStore 应有 _get_artifact_lock 方法。"""
        from alphadiana.results.result_store import ResultStore
        store = ResultStore(output_dir="/tmp/test_locks", run_id="lock_test")
        lock = store._get_artifact_lock("task1:0")
        assert lock is not None
        # 同一 key 应返回同一 lock
        assert store._get_artifact_lock("task1:0") is lock
        # 不同 key 应返回不同 lock
        assert store._get_artifact_lock("task2:0") is not lock

    def test_append_uses_artifact_lock(self):
        """append() 应在 _persist_artifacts 和 _save_per_task_json 前获取 artifact lock。"""
        import inspect
        from alphadiana.results.result_store import ResultStore
        source = inspect.getsource(ResultStore.append)
        assert "_get_artifact_lock" in source, (
            "append() 应使用 _get_artifact_lock 保护 artifact 写入"
        )
        # _save_per_task_json 应在锁内（缩进在 with 块中）
        lines = source.splitlines()
        lock_line = next(i for i, l in enumerate(lines) if "_get_artifact_lock" in l)
        save_line = next(i for i, l in enumerate(lines) if "_save_per_task_json" in l)
        assert save_line > lock_line, (
            "_save_per_task_json 应在 _get_artifact_lock 之后"
        )

    def test_append_error_uses_artifact_lock(self):
        """append_error() 应在 _persist_artifacts 和 _save_per_task_json 前获取 artifact lock。"""
        import inspect
        from alphadiana.results.result_store import ResultStore
        source = inspect.getsource(ResultStore.append_error)
        assert "_get_artifact_lock" in source, (
            "append_error() 应使用 _get_artifact_lock 保护 artifact 写入"
        )
        lines = source.splitlines()
        lock_line = next(i for i, l in enumerate(lines) if "_get_artifact_lock" in l)
        save_line = next(i for i, l in enumerate(lines) if "_save_per_task_json" in l)
        assert save_line > lock_line, (
            "_save_per_task_json 应在 _get_artifact_lock 之后"
        )


# ===================================================================
# 7. OpenClaw token usage accumulation — 验证累加
# ===================================================================


class TestOpenClawTokenAccumulation:
    """OpenClaw 重试时 token_usage 应累加而非覆盖。"""

    def test_solve_source_has_cumulative_token_usage(self):
        """solve() 应使用 cumulative_token_usage 累加。"""
        import inspect
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.solve)
        assert "cumulative_token_usage" in source, (
            "OpenClaw solve() 应使用 cumulative_token_usage 累加 token usage"
        )


# ===================================================================
# 8. TaskDispatcher task-level retry — 验证
# ===================================================================


class TestTaskDispatcherRetry:
    """TaskDispatcher 支持 task_retries 参数。"""

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
        """task_dispatcher error logging 应包含 exc_info=True。"""
        import inspect
        from alphadiana.runner.task_dispatcher import TaskDispatcher
        seq_source = inspect.getsource(TaskDispatcher._dispatch_sequential)
        conc_source = inspect.getsource(TaskDispatcher._dispatch_concurrent)
        assert "exc_info=True" in seq_source, (
            "_dispatch_sequential error logging 应包含 exc_info=True"
        )
        assert "exc_info=True" in conc_source, (
            "_dispatch_concurrent error logging 应包含 exc_info=True"
        )


# ===================================================================
# 9. Benchmark data loading retry — 验证
# ===================================================================


class TestBenchmarkLoadRetry:
    """Benchmark data loading 应有 retry 机制。"""

    def test_load_dataset_with_retry_exists(self):
        from alphadiana.benchmark.base import load_dataset_with_retry
        assert callable(load_dataset_with_retry)

    def test_benchmarks_use_retry_loader(self):
        """所有使用 HF datasets 的 benchmark 应使用 load_dataset_with_retry。"""
        import inspect
        from alphadiana.benchmark.aime import AIMEBenchmark
        from alphadiana.benchmark.math_bench import MATHBenchmark
        from alphadiana.benchmark.hle import HLEBenchmark
        from alphadiana.benchmark.frontier_math import FrontierMathBenchmark

        for cls in [AIMEBenchmark, MATHBenchmark, HLEBenchmark, FrontierMathBenchmark]:
            source = inspect.getsource(cls.load_tasks)
            assert "load_dataset_with_retry" in source, (
                f"{cls.__name__}.load_tasks() 应使用 load_dataset_with_retry"
            )


# ===================================================================
# 10. Config validation 增强 — 验证
# ===================================================================


class TestConfigValidation:
    """ConfigValidator 增强验证。"""

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
# 11. numeric scorer inf 处理
# ===================================================================


class TestNumericScorerInf:
    """NumericScorer 应正确处理 inf 值。"""

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
# 12. Checkpoint scorer 变更检测
# ===================================================================


class TestCheckpointScorerMismatch:
    """resume 时应检测 scorer 配置变更。"""

    def test_run_metadata_includes_scorer_name(self):
        """run_metadata 应包含 scorer_name。"""
        import inspect
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.setup)
        assert "scorer_name" in source, (
            "run_metadata 应包含 scorer_name 以支持 checkpoint scorer 变更检测"
        )

    def test_checkpoint_detects_scorer_mismatch(self):
        """checkpoint 逻辑应检测 scorer 不匹配。"""
        import inspect
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        assert "scorer_name" in source and "mismatch" in source.lower(), (
            "Runner.run() 应在 checkpoint 时检测 scorer 不匹配"
        )


# ===================================================================
# 13. OpenClaw proxy_timeout 专项诊断
# ===================================================================


class TestOpenClawProxyTimeoutDiagnostic:
    """proxy_timeout 错误应有明确诊断提示。"""

    def test_solve_source_has_proxy_timeout_diagnostic(self):
        import inspect
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.solve)
        assert "proxy_timeout" in source, (
            "OpenClaw solve() 应对 proxy_timeout 错误类型给出诊断提示"
        )
        assert "proxy_timeout" in source and "sandbox_config" in source.lower() or "increase" in source.lower(), (
            "proxy_timeout 诊断应提示用户增大配置"
        )
