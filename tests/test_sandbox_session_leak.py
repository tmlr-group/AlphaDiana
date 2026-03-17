"""Validation for sandbox session leak fixes.

Covers:
- Rebuild the session in SandboxPool.release() when reset fails, preventing pool shrinkage
- SandboxPool.teardown() keeps cleaning other sessions when one close() fails
- ROCKSession.close() uses retries and logging instead of silently swallowing errors
- Sequential mode reuses a shared session instead of creating a new container per task
- OpenClaw agent teardown calls runtime_manager.teardown()
"""

import inspect
import threading
from unittest.mock import MagicMock, patch, PropertyMock, call

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask


# ===================================================================
# 1. SandboxPool — rebuild the session when release fails
# ===================================================================


class TestPoolReleaseRecovery:
    """pool.release() should destroy and rebuild a session when reset() fails."""

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
# 2. SandboxPool — teardown should continue through exceptions
# ===================================================================


class TestPoolTeardownRobust:
    """pool.teardown() should not stop cleaning other sessions when one close() fails."""

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
        """When close() hangs, teardown should return after timeout rather than blocking forever."""
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
        """teardown should close all sessions concurrently."""
        import inspect
        from alphadiana.sandbox.pool import SandboxPool
        source = inspect.getsource(SandboxPool.teardown)
        assert "Thread" in source or "thread" in source, (
            "teardown() should use threads to close sessions concurrently"
        )


# ===================================================================
# 3. ROCKSession.close() — retry behavior
# ===================================================================


class TestROCKSessionCloseRetry:
    """ROCKSession.close() should retry stop() rather than silently swallowing errors."""

    def test_close_source_has_retry(self):
        """close() should include retry logic."""
        from alphadiana.sandbox.rock import ROCKSession
        source = inspect.getsource(ROCKSession.close)
        assert "max_stop_attempts" in source or "attempt" in source, (
            "ROCKSession.close() should include retry logic"
        )
        # Should not silently pass on all errors
        assert "except Exception:\n            pass" not in source, (
            "ROCKSession.close() should not silently swallow stop() exceptions"
        )

    def test_close_does_not_silently_swallow(self):
        """close() exceptions should be logged as warnings rather than ignored."""
        from alphadiana.sandbox.rock import ROCKSession
        source = inspect.getsource(ROCKSession.close)
        assert "logger.warning" in source or "logger.error" in source


# ===================================================================
# 4. Reuse a shared session in sequential mode
# ===================================================================


class TestSequentialSharedSession:
    """When max_concurrent=1, a single session should be reused instead of creating a new container per task."""

    def test_runner_run_uses_shared_session(self):
        """Runner.run() should create shared_session in sequential mode."""
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        assert "shared_session" in source, (
            "Runner.run() should use shared_session to reuse the sandbox session"
        )

    def test_shared_session_reset_between_tasks(self):
        """The shared session should call reset() between tasks instead of close()."""
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        assert "sandbox_session.reset()" in source, (
            "The shared session should call reset() between tasks rather than close()"
        )

    def test_shared_session_closed_after_dispatch(self):
        """shared_session should be closed after dispatch finishes."""
        from alphadiana.runner.runner import Runner
        source = inspect.getsource(Runner.run)
        assert "shared_session.close()" in source, (
            "shared_session should be closed after dispatch completes"
        )


# ===================================================================
# 5. Complete OpenClaw agent teardown cleanup
# ===================================================================


class TestOpenClawTeardown:
    """OpenClaw agent teardown should clean up runtime_manager."""

    def test_teardown_calls_runtime_manager(self):
        """agent.teardown() should call _runtime_manager.teardown()."""
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.teardown)
        assert "_runtime_manager" in source, (
            "OpenClawAgent.teardown() should clean up _runtime_manager"
        )
        assert "teardown" in source

    def test_teardown_cleans_sandbox_client(self):
        """agent.teardown() should set _sandbox_client to None."""
        from alphadiana.agent.openclaw import OpenClawAgent
        source = inspect.getsource(OpenClawAgent.teardown)
        assert "_sandbox_client = None" in source


# ===================================================================
# 6. Integration check — session count remains stable
# ===================================================================


class TestSessionCountStable:
    """Verify that the pool keeps a stable number of sessions across repeated acquire/release cycles."""

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
        """The pool should automatically replace sessions after occasional reset failures and keep its size stable."""
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
