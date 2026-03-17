"""Sandbox session 泄漏修复验证。

覆盖:
- SandboxPool.release() reset 失败时重建 session（防止池缩水）
- SandboxPool.teardown() 单个 session close 失败不中断其他清理
- ROCKSession.close() 重试 + 日志替代静默吞错
- 顺序模式复用 shared session 而非每题创建新容器
- OpenClaw agent teardown 调用 runtime_manager.teardown()
"""

import inspect
import threading
from unittest.mock import MagicMock, patch, PropertyMock, call

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask


# ===================================================================
# 1. SandboxPool — release 失败时重建 session
# ===================================================================


class TestPoolReleaseRecovery:
    """pool.release() 中 reset() 失败时应销毁并重建 session。"""

    def test_release_replaces_broken_session(self):
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        good_sessions = [MagicMock() for _ in range(2)]
        replacement = MagicMock()
        mock_sandbox.create_session.side_effect = [*good_sessions, replacement]

        pool = SandboxPool(mock_sandbox, 2)
        assert pool.size == 2
        assert pool.available == 2

        # Acquire both sessions
        s1 = pool.acquire()
        s2 = pool.acquire()
        assert pool.available == 0

        # Make reset fail on s1
        s1.reset.side_effect = RuntimeError("reset failed")
        pool.release(s1)

        # Broken session should have been closed
        s1.close.assert_called_once()
        # A replacement session should have been created and made available
        assert pool.available == 1

        # Release s2 normally
        pool.release(s2)
        assert pool.available == 2

        # Pool should still function — acquire includes the replacement
        acquired = []
        acquired.append(pool.acquire())
        acquired.append(pool.acquire())
        assert replacement in acquired

    def test_release_normal_reset_works(self):
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        session = MagicMock()
        mock_sandbox.create_session.return_value = session

        pool = SandboxPool(mock_sandbox, 1)
        s = pool.acquire()
        pool.release(s)
        s.reset.assert_called_once()
        assert pool.available == 1

    def test_release_replacement_failure_unblocks(self):
        """Even if replacement session creation fails, pool should not deadlock."""
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        session = MagicMock()
        mock_sandbox.create_session.side_effect = [session, RuntimeError("no resources")]

        pool = SandboxPool(mock_sandbox, 1)
        s = pool.acquire()
        s.reset.side_effect = RuntimeError("reset failed")

        pool.release(s)
        # Pool is degraded (no sessions available) but _event should be set
        # so other threads don't deadlock waiting forever.
        assert pool.available == 0


# ===================================================================
# 2. SandboxPool — teardown 异常不中断
# ===================================================================


class TestPoolTeardownRobust:
    """pool.teardown() 单个 session close 失败不应中断其他 session 清理。"""

    def test_teardown_closes_all_despite_errors(self):
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        s1 = MagicMock()
        s2 = MagicMock()
        s3 = MagicMock()
        s1.close.side_effect = RuntimeError("stop failed")
        mock_sandbox.create_session.side_effect = [s1, s2, s3]

        pool = SandboxPool(mock_sandbox, 3)
        pool.teardown()

        # All three sessions should have had close() called
        s1.close.assert_called_once()
        s2.close.assert_called_once()
        s3.close.assert_called_once()

    def test_teardown_does_not_hang_on_blocking_close(self):
        """close() 挂起时 teardown 应在超时后返回，不无限阻塞。"""
        import time
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        s1 = MagicMock()
        s2 = MagicMock()

        # s1.close() blocks for 10 seconds (simulates hung network)
        def blocking_close():
            time.sleep(10)

        s1.close.side_effect = blocking_close
        s1.session_id = "hung-session"
        mock_sandbox.create_session.side_effect = [s1, s2]

        pool = SandboxPool(mock_sandbox, 2)

        start = time.monotonic()
        pool.teardown(timeout=1.0)  # 1 second timeout per session
        elapsed = time.monotonic() - start

        # Should complete in ~1s (timeout), not 10s (blocking)
        assert elapsed < 5.0, f"teardown took {elapsed:.1f}s, expected < 5s"
        # s2 should still have been closed normally
        s2.close.assert_called_once()

    def test_teardown_concurrent_close(self):
        """teardown 应并发关闭所有 session。"""
        import inspect
        from alphadiana.sandbox.pool import SandboxPool
        source = inspect.getsource(SandboxPool.teardown)
        assert "Thread" in source or "thread" in source, (
            "teardown() 应使用线程并发关闭 session"
        )


# ===================================================================
# 3. ROCKSession.close() — 重试机制
# ===================================================================


class TestROCKSessionCloseRetry:
    """ROCKSession.close() 应重试 stop() 而非静默吞错。"""

    def test_close_source_has_retry(self):
        """close() 应有重试逻辑。"""
        from alphadiana.sandbox.rock import ROCKSession
        source = inspect.getsource(ROCKSession.close)
        assert "max_stop_attempts" in source or "attempt" in source, (
            "ROCKSession.close() 应有重试逻辑"
        )
        # Should not silently pass on all errors
        assert "except Exception:\n            pass" not in source, (
            "ROCKSession.close() 不应静默吞掉 stop() 异常"
        )

    def test_close_does_not_silently_swallow(self):
        """close() 异常应被 warning 而非 pass。"""
        from alphadiana.sandbox.rock import ROCKSession
        source = inspect.getsource(ROCKSession.close)
        assert "logger.warning" in source or "logger.error" in source


# ===================================================================
# 4. 顺序模式复用 shared session
# ===================================================================


class TestSequentialSharedSession:
    """max_concurrent=1 时应复用单个 session 而非每题创建新容器。"""

    def test_runner_run_uses_shared_session(self):
        """Runner.run() 应在顺序模式创建 shared_session。"""
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        assert "shared_session" in source, (
            "Runner.run() 应使用 shared_session 复用 sandbox session"
        )

    def test_shared_session_reset_between_tasks(self):
        """shared session 在 finally 中应调 reset() 而非 close()。"""
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        assert "sandbox_session.reset()" in source, (
            "shared session 应在 task 之间调 reset() 而非 close()"
        )

    def test_shared_session_closed_after_dispatch(self):
        """dispatch 结束后 shared_session 应被 close()。"""
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        assert "shared_session.close()" in source, (
            "dispatch 结束后 shared_session 应被 close()"
        )


# ===================================================================
# 5. OpenClaw agent teardown 完整清理
# ===================================================================


class TestOpenClawTeardown:
    """OpenClaw agent teardown 应清理 runtime_manager。"""

    def test_teardown_calls_runtime_manager(self):
        """agent.teardown() 应调用 _runtime_manager.teardown()。"""
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.teardown)
        assert "_runtime_manager" in source, (
            "OpenClawAgent.teardown() 应清理 _runtime_manager"
        )
        assert "teardown" in source

    def test_teardown_cleans_sandbox_client(self):
        """agent.teardown() 应将 _sandbox_client 置 None。"""
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.teardown)
        assert "_sandbox_client = None" in source


# ===================================================================
# 6. 集成验证 — session 数量恒定
# ===================================================================


class TestSessionCountStable:
    """验证 pool 在多次 acquire/release 周期后 session 数量恒定。"""

    def test_pool_size_stable_after_many_cycles(self):
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        sessions = [MagicMock() for _ in range(3)]
        mock_sandbox.create_session.side_effect = sessions

        pool = SandboxPool(mock_sandbox, 3)

        # Simulate 10 rounds of acquire/release
        for _ in range(10):
            acquired = []
            for _ in range(3):
                acquired.append(pool.acquire())
            for s in acquired:
                pool.release(s)

        assert pool.available == 3

    def test_pool_size_stable_with_occasional_reset_failures(self):
        """偶尔 reset 失败后，pool 应自动替换保持大小。"""
        from alphadiana.sandbox.pool import SandboxPool

        mock_sandbox = MagicMock()
        initial = [MagicMock() for _ in range(2)]
        replacements = [MagicMock() for _ in range(5)]  # enough for failures
        mock_sandbox.create_session.side_effect = [*initial, *replacements]

        pool = SandboxPool(mock_sandbox, 2)

        # Round 1: normal
        s1 = pool.acquire()
        s2 = pool.acquire()
        pool.release(s1)
        pool.release(s2)
        assert pool.available == 2

        # Round 2: one reset fails
        s1 = pool.acquire()
        s2 = pool.acquire()
        s1.reset.side_effect = RuntimeError("broken")
        pool.release(s1)  # should replace
        pool.release(s2)  # normal
        assert pool.available == 2

        # Round 3: pool should still work
        a = pool.acquire()
        b = pool.acquire()
        pool.release(a)
        pool.release(b)
        assert pool.available == 2
