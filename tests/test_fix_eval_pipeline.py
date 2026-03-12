"""评测流程与实验管理（问题 1.1 ~ 1.4）

覆盖:
- 1.1 Config override 解析与深度合并、ExperimentConfig.from_yaml(overrides=)
- 1.2 Sandbox 状态隔离：close 时 workspace 清理、reset_between_tasks 配置
- 1.3 BatchRunner 串行/并行批量运行
- 1.4 SandboxPool 分配/归还/teardown
"""

import textwrap
from unittest.mock import MagicMock, patch

import pytest

from alphadiana.config.experiment_config import ExperimentConfig


# ===================================================================
# 1.1 Config override
# ===================================================================


class TestConfigOverride:
    """--override 解析与深度合并。"""

    # --- 解析 ---

    def test_parse_dotted_override(self):
        from alphadiana.config.experiment_config import parse_override

        result = parse_override("agent.config.api_base=http://x")
        assert result == {"agent": {"config": {"api_base": "http://x"}}}

    def test_parse_numeric_value(self):
        from alphadiana.config.experiment_config import parse_override

        assert parse_override("max_concurrent=4") == {"max_concurrent": 4}

    def test_parse_boolean_value(self):
        from alphadiana.config.experiment_config import parse_override

        r = parse_override("sandbox.config.reset_between_tasks=false")
        assert r["sandbox"]["config"]["reset_between_tasks"] is False

    # --- 深度合并 ---

    def test_deep_merge_adds_new_key(self):
        from alphadiana.config.experiment_config import deep_merge

        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_deep_merge_overwrites(self):
        from alphadiana.config.experiment_config import deep_merge

        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_deep_merge_preserves_unrelated(self):
        from alphadiana.config.experiment_config import deep_merge

        result = deep_merge({"b": {"c": 3, "d": 4}}, {"b": {"c": 99}})
        assert result["b"] == {"c": 99, "d": 4}

    def test_deep_merge_nested(self):
        from alphadiana.config.experiment_config import deep_merge

        result = deep_merge({"x": {"y": {"z": 1}}}, {"x": {"y": {"z": 2, "w": 3}}})
        assert result["x"]["y"] == {"z": 2, "w": 3}

    # --- from_yaml with overrides ---

    def _write_yaml(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text(textwrap.dedent("""\
            run_id: "test-001"
            agent:
              name: openclaw
              version: "2026.3.7"
              config:
                temperature: 0.7
                max_tokens: 8192
            benchmark:
              name: aime
            scorer:
              name: numeric
        """))
        return str(p)

    def test_from_yaml_with_overrides(self, tmp_path):
        path = self._write_yaml(tmp_path)
        config = ExperimentConfig.from_yaml(
            path, overrides={"agent": {"config": {"temperature": 0.0}}}
        )
        assert config.agent_config["temperature"] == 0.0

    def test_override_does_not_mutate_yaml(self, tmp_path):
        path = self._write_yaml(tmp_path)
        original = open(path).read()
        ExperimentConfig.from_yaml(
            path, overrides={"agent": {"config": {"temperature": 0.0}}}
        )
        assert open(path).read() == original


# ===================================================================
# 1.2 Sandbox 状态隔离
# ===================================================================


class TestSandboxIsolation:
    """close() 清理 workspace、reset_between_tasks 配置。"""

    def test_reset_between_tasks_default_true(self):
        from alphadiana.sandbox.rock import ROCKSandbox

        sb = ROCKSandbox()
        with patch("alphadiana.sandbox.rock._require_rock_sdk"):
            sb.setup({
                "admin_base_url": "http://localhost:9000",
                "proxy_base_url": "http://localhost:9001",
            })
        assert sb._reset_between_tasks is True

    def test_reset_between_tasks_configurable(self):
        from alphadiana.sandbox.rock import ROCKSandbox

        sb = ROCKSandbox()
        with patch("alphadiana.sandbox.rock._require_rock_sdk"):
            sb.setup({
                "admin_base_url": "http://localhost:9000",
                "proxy_base_url": "http://localhost:9001",
                "reset_between_tasks": False,
            })
        assert sb._reset_between_tasks is False

    def test_close_executes_cleanup(self):
        from alphadiana.sandbox.rock import ROCKSession

        session = MagicMock(spec=ROCKSession)
        session._reset_between_tasks = True
        session.execute = MagicMock()

        ROCKSession._cleanup_workspace(session)

        session.execute.assert_called()
        cmd = session.execute.call_args[0][0]
        assert "/root/.openclaw/workspace" in cmd
        assert "/root/.openclaw/agents/main/sessions" in cmd

    def test_close_calls_cleanup_before_stop(self):
        from alphadiana.sandbox.rock import ROCKSession

        session = object.__new__(ROCKSession)
        session._sandbox = MagicMock()
        session._admin_base_url = "http://localhost:9000"
        session._cleanup_workspace = MagicMock()

        with patch("alphadiana.sandbox.rock._run_async"), patch(
            "alphadiana.sandbox.rock._maybe_await",
            new=MagicMock(return_value=None),
        ):
            ROCKSession.close(session)

        session._cleanup_workspace.assert_called_once()
        session._sandbox.stop.assert_called_once()

    def test_close_skips_cleanup_when_disabled(self):
        from alphadiana.sandbox.rock import ROCKSession

        session = MagicMock(spec=ROCKSession)
        session._reset_between_tasks = False
        session.execute = MagicMock()

        ROCKSession._cleanup_workspace(session)

        session.execute.assert_not_called()

    def test_reset_executes_cleanup(self):
        from alphadiana.sandbox.rock import ROCKSession

        session = MagicMock(spec=ROCKSession)
        session._cleanup_workspace = MagicMock()

        ROCKSession.reset(session)

        session._cleanup_workspace.assert_called_once()

    def test_cleanup_paths_in_module(self):
        from alphadiana.sandbox.rock import CLEANUP_PATHS

        joined = " ".join(CLEANUP_PATHS)
        assert "/root/.openclaw/workspace" in joined
        assert "/root/.openclaw/agents/main/sessions" in joined

    def test_schema_includes_reset_between_tasks(self):
        from pathlib import Path

        schema = (Path(__file__).parent.parent / "configs" / "schema.yaml").read_text()
        assert "reset_between_tasks" in schema

    def test_create_session_passes_reset_and_proxy_timeout(self):
        from alphadiana.sandbox.rock import ROCKSandbox

        sb = ROCKSandbox()
        with patch("alphadiana.sandbox.rock._require_rock_sdk"):
            sb.setup({
                "admin_base_url": "http://localhost:9000",
                "proxy_base_url": "http://localhost:9001",
                "reset_between_tasks": False,
                "proxy_timeout": 321,
            })

        with patch("alphadiana.sandbox.rock.ROCKSession") as Session:
            sb.create_session()

        kwargs = Session.call_args.kwargs
        assert kwargs["reset_between_tasks"] is False
        assert kwargs["proxy_timeout"] == 321


# ===================================================================
# 1.3 BatchRunner
# ===================================================================


def _make_config(run_id: str) -> ExperimentConfig:
    return ExperimentConfig(
        run_id=run_id,
        agent_name="openclaw",
        agent_version="2026.3.7",
        benchmark_name="aime",
        scorer_name="numeric",
    )


class TestBatchRunner:
    """串行/并行批量运行。"""

    def test_sequential_runs_all(self):
        from alphadiana.runner.batch_runner import BatchRunner

        configs = [_make_config(f"r{i}") for i in range(3)]
        ids = []

        with patch("alphadiana.runner.batch_runner.Runner") as R:
            def mk(cfg):
                ids.append(cfg.run_id)
                m = MagicMock()
                m.run.return_value = MagicMock(accuracy=1.0)
                return m

            R.side_effect = mk
            BatchRunner(configs, parallel=False).run()

        assert set(ids) == {"r0", "r1", "r2"}

    def test_sequential_continues_on_error(self):
        from alphadiana.runner.batch_runner import BatchRunner

        configs = [_make_config(f"r{i}") for i in range(3)]
        count = {"n": 0}

        with patch("alphadiana.runner.batch_runner.Runner") as R:
            def mk(cfg):
                count["n"] += 1
                m = MagicMock()
                if cfg.run_id == "r1":
                    m.run.side_effect = RuntimeError("boom")
                else:
                    m.run.return_value = MagicMock(accuracy=1.0)
                return m

            R.side_effect = mk
            BatchRunner(configs, parallel=False).run()

        assert count["n"] == 3

    def test_parallel_runs_all(self):
        from alphadiana.runner.batch_runner import BatchRunner

        configs = [_make_config(f"r{i}") for i in range(3)]

        with patch("alphadiana.runner.batch_runner.Runner") as R:
            R.return_value = MagicMock(**{"run.return_value": MagicMock(accuracy=0.9)})
            summaries = BatchRunner(configs, parallel=True).run()

        assert len(summaries) == 3

    def test_parallel_uses_independent_runners(self):
        from alphadiana.runner.batch_runner import BatchRunner

        configs = [_make_config(f"r{i}") for i in range(2)]
        instances = []

        with patch("alphadiana.runner.batch_runner.Runner") as R:
            def mk(cfg):
                m = MagicMock()
                m.run.return_value = MagicMock(accuracy=1.0)
                instances.append(m)
                return m

            R.side_effect = mk
            BatchRunner(configs, parallel=True).run()

        assert len(instances) == 2
        assert instances[0] is not instances[1]

    def test_runner_lifecycle_wrapped(self):
        from alphadiana.runner.batch_runner import BatchRunner

        configs = [_make_config("r0")]

        with patch("alphadiana.runner.batch_runner.Runner") as R:
            runner = MagicMock()
            runner.run.return_value = MagicMock(accuracy=1.0)
            R.return_value = runner
            BatchRunner(configs, parallel=False).run()

        runner.setup.assert_called_once()
        runner.run.assert_called_once()
        runner.teardown.assert_called_once()


# ===================================================================
# 1.4 SandboxPool
# ===================================================================


class TestSandboxPool:
    """SandboxPool 分配/归还/teardown。"""

    def _make_pool(self, size=3):
        from alphadiana.sandbox.pool import SandboxPool

        mock_sb = MagicMock()
        sessions = [MagicMock(name=f"s{i}") for i in range(size)]
        mock_sb.create_session = MagicMock(side_effect=sessions)
        return SandboxPool(sandbox=mock_sb, size=size), sessions

    def test_creates_k_sessions(self):
        pool, _ = self._make_pool(3)
        assert pool.size == 3

    def test_acquire_returns_session(self):
        pool, sessions = self._make_pool(2)
        assert pool.acquire() in sessions

    def test_release_then_reacquire(self):
        pool, _ = self._make_pool(1)
        s = pool.acquire()
        assert pool.available == 0
        pool.release(s)
        assert pool.available == 1

    def test_release_resets_session_before_requeue(self):
        pool, sessions = self._make_pool(1)
        session = pool.acquire()

        pool.release(session)

        sessions[0].reset.assert_called_once()

    def test_reused_session_is_same_instance_after_reset(self):
        pool, sessions = self._make_pool(1)
        first = pool.acquire()
        pool.release(first)
        second = pool.acquire()

        assert first is second
        sessions[0].reset.assert_called_once()

    def test_acquire_raises_when_empty(self):
        pool, _ = self._make_pool(1)
        pool.acquire()
        with pytest.raises((RuntimeError, TimeoutError)):
            pool.acquire(timeout=0.1)

    def test_teardown_closes_all(self):
        pool, sessions = self._make_pool(3)
        pool.teardown()
        for s in sessions:
            s.close.assert_called_once()
