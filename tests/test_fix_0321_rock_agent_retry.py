"""Regression tests for transient ROCK agent install/run failures.

These tests cover the case where ROCK returns temporary
"upstream not reachable / run in session failed" errors while the
sandbox agent helper uploads config files.
"""

from __future__ import annotations

import pytest

from alphadiana.sandbox import rock as rock_module


class _FakeSandbox:
    def __init__(self, agent: object) -> None:
        self.agent = agent
        self.sandbox_id = "sb-test"


class _FlakyInstallAgent:
    def __init__(self) -> None:
        self.calls = 0

    def install(self, *args, **kwargs):
        self.calls += 1
        if self.calls < 2:
            raise RuntimeError(
                "run in session failed: Service unavailable: Upstream server is not reachable"
            )
        return "ok"


class _HardFailInstallAgent:
    def __init__(self) -> None:
        self.calls = 0

    def install(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("permission denied")


@pytest.fixture
def _no_wait(monkeypatch):
    async def _fake_wait(*args, **kwargs):
        return None

    monkeypatch.setattr(rock_module, "_wait_for_session_ready", _fake_wait)
    monkeypatch.setattr(rock_module.time, "sleep", lambda *_: None)


def test_call_agent_method_retries_transient_upstream_error(_no_wait):
    session = rock_module.ROCKSession.__new__(rock_module.ROCKSession)
    flaky = _FlakyInstallAgent()
    session._sandbox = _FakeSandbox(flaky)
    session._session_name = "default"

    result = session._call_agent_method("install")

    assert result == "ok"
    assert flaky.calls == 2


def test_call_agent_method_does_not_retry_non_upstream_error(_no_wait):
    session = rock_module.ROCKSession.__new__(rock_module.ROCKSession)
    hard_fail = _HardFailInstallAgent()
    session._sandbox = _FakeSandbox(hard_fail)
    session._session_name = "default"

    with pytest.raises(RuntimeError, match="permission denied"):
        session._call_agent_method("install")

    assert hard_fail.calls == 1


def test_start_sandbox_uses_full_timeout_for_requested_profile():
    import inspect

    src = inspect.getsource(rock_module.ROCKSession._start_sandbox)
    assert "else min(self._startup_timeout, self._fallback_startup_timeout)" in src


def test_start_sandbox_only_caches_requested_profile():
    import inspect

    src = inspect.getsource(rock_module.ROCKSession._start_sandbox)
    assert "if is_requested_profile:" in src
    assert "_PROFILE_CACHE[(self._admin_base_url, self._image)] = (memory, cpus)" in src


def test_start_sandbox_has_client_side_wait_for_guard():
    import inspect

    src = inspect.getsource(rock_module.ROCKSession._start_sandbox)
    assert "asyncio.wait_for" in src


def test_runner_openclaw_start_retries_default_3():
    import inspect
    from alphadiana.runner import runner as runner_module

    src = inspect.getsource(runner_module.Runner.run)
    assert '"rock_start_retries", 3' in src


def test_rock_sandbox_fallback_startup_timeout_default_180():
    from unittest.mock import patch
    sandbox = rock_module.ROCKSandbox()
    with patch.object(rock_module, "_require_rock_sdk"):
        sandbox.setup({
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9001",
        })
    assert sandbox.metadata()["fallback_startup_timeout"] == 180


def test_install_run_in_session_retry_retries_transient_error():
    calls = {"n": 0}

    class _FakeSandbox:
        async def run_in_session(self, action):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("run in session failed: Service unavailable: Upstream server is not reachable")
            return "ok"

    sandbox = _FakeSandbox()
    rock_module._install_run_in_session_retry(sandbox)

    result = rock_module._run_async(sandbox.run_in_session(object()))
    assert result == "ok"
    assert calls["n"] == 3


def test_install_run_in_session_retry_does_not_swallow_non_transient_error():
    class _FakeSandbox:
        async def run_in_session(self, action):
            raise RuntimeError("permission denied")

    sandbox = _FakeSandbox()
    rock_module._install_run_in_session_retry(sandbox)

    with pytest.raises(RuntimeError, match="permission denied"):
        rock_module._run_async(sandbox.run_in_session(object()))
