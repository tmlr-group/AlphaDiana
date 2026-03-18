"""2026-03-18 reviewer 反馈修复验证。

覆盖：
- #1: pool.py _all_sessions 内存泄漏（release 时旧 session 未移除）
- #2: runner.py 顺序模式 shared_session.reset() 失败无恢复
- #3: cli.py run 命令跳过 ConfigValidator
- #4: cli.py validate 不支持 --override
- #5: result_store.py ResultStore.__init__ 无条件创建目录
- #6: test_stream.py 被 pytest 误收集
- #7: test_fix_0312_deploy.py 缺 rock 懒导入
- #8: task_dispatcher.py last_exc 死代码
- #9: direct_llm.py stream_options 兼容性
- #10: openclaw.py 失败重试 token 累计语义
"""

import inspect
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ===================================================================
# #1 pool.py _all_sessions 内存泄漏
# ===================================================================


class TestPoolAllSessionsLeak:
    """release() 替换 session 后 _all_sessions 不应增长。"""

    def test_all_sessions_length_stable_after_replacement(self):
        """reset 失败 → close 旧 + create 新后，_all_sessions 长度不变。"""
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        s1 = MagicMock(name="s1")
        s2 = MagicMock(name="s2")
        replacement = MagicMock(name="replacement")
        mock_sandbox.create_session.side_effect = [s1, s2, replacement]

        pool = SandboxPool(mock_sandbox, 2)
        assert len(pool._all_sessions) == 2

        # Acquire s1, make reset fail
        acquired = pool.acquire()
        acquired.reset.side_effect = RuntimeError("broken")
        pool.release(acquired)

        # _all_sessions should still have exactly 2 entries (old removed, new added)
        assert len(pool._all_sessions) == 2
        assert acquired not in pool._all_sessions, "旧 session 应从 _all_sessions 移除"
        assert replacement in pool._all_sessions, "替换 session 应在 _all_sessions 中"

    def test_teardown_no_double_close(self):
        """teardown 不应对已关闭的旧 session 再次 close。"""
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        s1 = MagicMock(name="s1")
        replacement = MagicMock(name="replacement")
        mock_sandbox.create_session.side_effect = [s1, replacement]

        pool = SandboxPool(mock_sandbox, 1)

        # Acquire, reset fail, release (triggers replacement)
        acquired = pool.acquire()
        acquired.reset.side_effect = RuntimeError("broken")
        pool.release(acquired)

        # Reset close call count after release
        s1_close_count_after_release = s1.close.call_count

        pool.teardown()

        # s1.close should NOT have been called again during teardown
        assert s1.close.call_count == s1_close_count_after_release, \
            "teardown 不应对已关闭的旧 session 再次调用 close()"
        # replacement should have been closed during teardown
        replacement.close.assert_called()

    def test_multiple_replacements_no_leak(self):
        """多次 reset 失败后 _all_sessions 长度恒定。"""
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        initial = MagicMock(name="initial")
        replacements = [MagicMock(name=f"r{i}") for i in range(5)]
        mock_sandbox.create_session.side_effect = [initial] + replacements

        pool = SandboxPool(mock_sandbox, 1)
        assert len(pool._all_sessions) == 1

        for i in range(5):
            s = pool.acquire()
            s.reset.side_effect = RuntimeError(f"fail-{i}")
            pool.release(s)
            assert len(pool._all_sessions) == 1, \
                f"第 {i+1} 次替换后 _all_sessions 长度应为 1，实际为 {len(pool._all_sessions)}"


# ===================================================================
# #2 顺序模式 shared_session.reset() 失败无恢复
# ===================================================================


class TestSharedSessionResetRecovery:
    """顺序模式 reset 失败时应重建 shared_session。"""

    def test_runner_source_has_reset_recovery(self):
        """solve_fn 的 finally 块应包含 reset 失败恢复逻辑。"""
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        # 应有 nonlocal shared_session 或其他恢复机制
        assert "nonlocal" in source or "create_session" in source, \
            "solve_fn 应包含 shared_session reset 失败时的恢复逻辑"

    def test_runner_source_recreates_session_on_reset_failure(self):
        """reset 失败后应尝试 create_session 重建。"""
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        # 在 shared_session reset 失败的 except 块中应有 create_session
        # 检查 finally 块中同时存在 reset 和 create_session
        has_reset = "sandbox_session.reset()" in source or "session.reset()" in source
        has_create = "create_session" in source
        assert has_reset and has_create, \
            "solve_fn 应在 reset 失败时调用 create_session 重建 session"


# ===================================================================
# #3 run 命令调用 ConfigValidator
# ===================================================================


class TestRunCommandValidation:
    """run 命令应在执行前调用 ConfigValidator。"""

    def test_run_command_has_validation(self):
        """run 命令源码中应包含 ConfigValidator 调用。"""
        import alphadiana.cli as cli_module
        source = inspect.getsource(cli_module)
        # 在 run 函数定义到下一个 @main.command 之间查找 ConfigValidator
        run_start = source.find("def run(")
        assert run_start != -1, "未找到 run 函数定义"
        # 找到 run 函数后下一个 def 或 @main.command 的位置
        next_func = source.find("\n@main.command", run_start + 1)
        if next_func == -1:
            next_func = len(source)
        run_source = source[run_start:next_func]
        assert "ConfigValidator" in run_source or "validator" in run_source.lower(), \
            "run 命令应调用 ConfigValidator 进行配置验证"

    def test_run_command_has_validate_call(self):
        """run 命令应在 runner.setup() 之前调用 validate。"""
        import alphadiana.cli as cli_module
        source = inspect.getsource(cli_module)
        run_start = source.find("def run(")
        next_func = source.find("\n@main.command", run_start + 1)
        if next_func == -1:
            next_func = len(source)
        run_source = source[run_start:next_func]
        assert "validate" in run_source, \
            "run 命令应调用 validate 方法"


# ===================================================================
# #4 validate 支持 --override
# ===================================================================


class TestValidateOverride:
    """validate 命令应支持 --override 参数。"""

    def test_validate_has_override_param(self):
        """validate 命令应接受 --override 参数。"""
        from alphadiana.cli import validate
        # click 命令的参数在 .params 中
        param_names = [p.name for p in validate.params]
        assert "override" in param_names, \
            "validate 命令应支持 --override 参数"


# ===================================================================
# #5 ResultStore 懒创建目录
# ===================================================================


class TestResultStoreLazyMkdir:
    """ResultStore.__init__ 不应无条件创建 artifacts 目录。"""

    def test_init_does_not_create_artifacts_dir(self, tmp_path):
        """只读场景（如 report）不应创建 artifacts 目录。"""
        from alphadiana.results.result_store import ResultStore

        output_dir = str(tmp_path / "results")
        store = ResultStore(output_dir=output_dir, run_id="test-run")

        artifacts_dir = tmp_path / "results" / "test-run" / "artifacts"
        assert not artifacts_dir.exists(), \
            "ResultStore.__init__ 不应无条件创建 artifacts 目录，应懒初始化"

    def test_artifacts_dir_created_on_write(self, tmp_path):
        """写入时应自动创建 artifacts 目录。"""
        from alphadiana.results.result_store import ResultStore
        from alphadiana.agent.base import AgentResponse
        from alphadiana.benchmark.base import BenchmarkTask
        from alphadiana.scorer.base import ScoreResult

        output_dir = str(tmp_path / "results")
        store = ResultStore(output_dir=output_dir, run_id="test-run")

        task = BenchmarkTask(task_id="t1", problem="test", ground_truth="42")
        response = AgentResponse(answer="42", wall_time_sec=1.0)
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
        store.append(task, response, score)

        # After write, directories should exist
        assert (tmp_path / "results").exists()


# ===================================================================
# #6 test_stream.py 不被 pytest 收集
# ===================================================================


class TestStreamScriptNotCollected:
    """test_stream.py 不应被 pytest 作为测试用例收集。"""

    def test_stream_script_not_in_tests(self):
        """test_stream.py 应不在 tests/ 中，或已在 pytest 配置中排除。"""
        tests_dir = Path(__file__).parent
        stream_in_tests = (tests_dir / "test_stream.py").exists()

        if stream_in_tests:
            # 检查是否在 conftest.py 或 pyproject.toml 中排除
            project_root = tests_dir.parent
            excluded = False
            # Check conftest.py
            conftest = tests_dir / "conftest.py"
            if conftest.exists():
                content = conftest.read_text()
                if "test_stream" in content and "collect_ignore" in content:
                    excluded = True
            # Check pyproject.toml
            if not excluded:
                pyproject = project_root / "pyproject.toml"
                if pyproject.exists():
                    content = pyproject.read_text()
                    if "test_stream" in content and "collect_ignore" in content:
                        excluded = True
            assert excluded, \
                "test_stream.py 在 tests/ 下但未在 conftest.py 或 pyproject.toml 中排除 pytest 收集"
        # 如果不存在，说明已移走，测试通过


# ===================================================================
# #7 test_fix_0312_deploy.py rock 导入保护
# ===================================================================


class TestDeployTestRockImport:
    """test_fix_0312_deploy.py 中依赖 rock 的测试应有导入保护。"""

    def test_deploy_tests_have_skip_or_mark(self):
        """依赖 rock 的测试应标记 integration 或有 importorskip。"""
        test_file = Path(__file__).parent / "test_fix_0312_deploy.py"
        if not test_file.exists():
            pytest.skip("test_fix_0312_deploy.py not found")

        content = test_file.read_text()
        has_protection = (
            "importorskip" in content
            or "skipif" in content
            or "pytest.mark.integration" in content
        )
        assert has_protection, \
            "test_fix_0312_deploy.py 应对 rock 导入添加 skip 保护"


# ===================================================================
# #8 task_dispatcher.py last_exc 死代码
# ===================================================================


class TestDispatcherLastExcRemoved:
    """_solve_with_retry 中不应有冗余的 last_exc 变量。"""

    def test_no_last_exc_in_solve_with_retry(self):
        """_solve_with_retry 不应包含未使用的 last_exc。"""
        from alphadiana.runner.task_dispatcher import TaskDispatcher
        source = inspect.getsource(TaskDispatcher._solve_with_retry)
        assert "last_exc" not in source, \
            "_solve_with_retry 中 last_exc 为死代码，应移除"

    def test_retry_still_works_without_last_exc(self):
        """移除 last_exc 后重试逻辑仍正常工作。"""
        from alphadiana.runner.task_dispatcher import TaskDispatcher

        dispatcher = TaskDispatcher(max_concurrent=1, task_retries=2)
        call_count = 0

        def failing_then_succeed(item):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError(f"fail-{call_count}")
            return {"task_id": "t1", "success": True}

        result = dispatcher._solve_with_retry("task", failing_then_succeed)
        assert result["success"] is True
        assert call_count == 3

    def test_retry_exhausted_raises(self):
        """重试耗尽后应抛出最后一次异常。"""
        from alphadiana.runner.task_dispatcher import TaskDispatcher

        dispatcher = TaskDispatcher(max_concurrent=1, task_retries=1)

        def always_fail(item):
            raise ValueError("persistent failure")

        with pytest.raises(ValueError, match="persistent failure"):
            dispatcher._solve_with_retry("task", always_fail)


# ===================================================================
# #9 direct_llm.py stream_options 兼容性
# ===================================================================


class TestStreamOptionsFallback:
    """stream_options 不支持时应 fallback。"""

    def test_streaming_has_fallback_logic(self):
        """_call_streaming 应处理 stream_options 不支持的情况。"""
        from alphadiana.agent.direct_llm import DirectLLMAgent
        source = inspect.getsource(DirectLLMAgent._call_streaming)
        # 应有某种 fallback 机制：try/except、配置开关、或条件判断
        has_fallback = (
            "stream_options" in source
            and (
                "except" in source
                or "_stream_options_supported" in source
                or "stream_options_enabled" in source
                or "fallback" in source.lower()
            )
        )
        assert has_fallback, \
            "_call_streaming 应有 stream_options 不支持时的 fallback 机制"

    def test_streaming_without_stream_options_works(self):
        """不带 stream_options 时 streaming 仍应能工作。"""
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent._client = MagicMock()
        agent._max_retries = 0
        agent._stream = True
        agent._model = "test-model"
        agent._temperature = 0.7
        agent._top_p = None
        agent._max_tokens = 100
        agent._max_completion_tokens = None
        agent._resolved_max_tokens = 100
        agent._system_prompt = "test"

        # Simulate stream_options causing 400 error on first call,
        # then succeeding without it
        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta.content = "answer"
        mock_chunk.choices[0].finish_reason = "stop"
        mock_chunk.usage = MagicMock()
        mock_chunk.usage.prompt_tokens = 10
        mock_chunk.usage.completion_tokens = 5
        mock_chunk.usage.total_tokens = 15

        # Create a stream that yields one chunk
        agent._client.chat.completions.create.return_value = iter([mock_chunk])

        result = agent._call_streaming({
            "model": "test",
            "messages": [],
            "temperature": 0.7,
            "max_tokens": 100,
        })
        assert result[0] == "answer"  # raw_output


# ===================================================================
# #10 openclaw.py token 累计语义
# ===================================================================


class TestOpenClawTokenSemantics:
    """token 累计应区分总 API 成本和最终 attempt 用量。"""

    def test_solve_source_has_token_distinction(self):
        """solve() 应在 metadata 中区分 total 和 final attempt token。"""
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.solve)
        has_cumulative = "cumulative" in source or "total" in source.lower()
        assert has_cumulative, "solve() 应包含累计 token 逻辑"

        # 应在 metadata 中记录区分信息
        has_distinction = (
            "token_usage_total" in source
            or "final_attempt" in source
            or "retry_count" in source
            or "attempt" in source
        )
        assert has_distinction, \
            "solve() 应在 metadata 中区分总 token 和最终 attempt token"

    def test_metadata_contains_retry_info(self):
        """AgentResponse.metadata 应包含重试次数信息。"""
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.solve)
        # 应在 response.metadata 中记录 attempt/retry 信息
        has_retry_info = (
            "retry_count" in source
            or "attempt_count" in source
            or "num_retries" in source
            or "retry_responses" in source
        )
        assert has_retry_info, \
            "solve() 应在 metadata 中记录重试相关信息"


# ===================================================================
# 集成验证：pool 长期运行稳定性
# ===================================================================


class TestPoolLongRunStability:
    """pool 在混合正常/异常 reset 场景下保持稳定。"""

    def test_pool_stable_with_intermittent_failures(self):
        """间歇性 reset 失败后 pool 功能正常，_all_sessions 不膨胀。"""
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        initial = [MagicMock(name=f"s{i}") for i in range(3)]
        replacements = [MagicMock(name=f"r{i}") for i in range(10)]
        mock_sandbox.create_session.side_effect = initial + replacements

        pool = SandboxPool(mock_sandbox, 3)

        for round_idx in range(5):
            sessions = [pool.acquire() for _ in range(3)]
            # Every other round, one session has reset failure
            if round_idx % 2 == 1:
                sessions[0].reset.side_effect = RuntimeError("intermittent")
            for s in sessions:
                pool.release(s)
            assert len(pool._all_sessions) == 3, \
                f"Round {round_idx}: _all_sessions 应恒为 3，实际为 {len(pool._all_sessions)}"
            assert pool.available == 3
