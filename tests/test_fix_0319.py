"""Unit tests for 2026-03-19 fixes (based on problem_0318.md).

Covers:
- Fix 0: ROCK post_proxy hardcoded 120s → configurable
- Fix 1: openclaw.py request_timeout default 600 → 1800
- Fix 2: rock.py proxy_timeout default 600 → 1800
- Fix 3: openclaw.py retry delay differentiation (empty_response vs other)
- Fix 4: openclaw.py max_tokens fallback 32768 → 65536
- Fix 5: Example YAML includes max_tokens/request_timeout/max_attempts
- Fix 6: Dashboard deduplicates task_ids for multi-sample
- Fix 7: Per-task JSON merges all samples into one file
- Fix 8: YAML config supports environment variable expansion
- Fix 9: OpenClaw health check on first solve()
- Fix 10: Example YAML documents timeout configuration
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ===================================================================
# Fix 0: ROCK post_proxy uses configurable timeout
# ===================================================================


class TestFix0RockProxyTimeout:
    """Fix 0: Verify ROCK post_proxy uses self.proxy_config.timeout."""

    def test_post_proxy_uses_proxy_config_timeout(self):
        """post_proxy should reference self.proxy_config.timeout, not 120."""
        rock_file = Path("/hd2/chentao/ROCK/rock/sandbox/service/sandbox_proxy_service.py")
        if not rock_file.exists():
            pytest.skip("ROCK repo not available at expected path")
        src = rock_file.read_text()
        assert "timeout=self.proxy_config.timeout" in src, (
            "post_proxy should use self.proxy_config.timeout instead of hardcoded 120"
        )

    def test_post_proxy_no_hardcoded_120_in_build_request(self):
        """post_proxy's build_request should not contain timeout=120."""
        rock_file = Path("/hd2/chentao/ROCK/rock/sandbox/service/sandbox_proxy_service.py")
        if not rock_file.exists():
            pytest.skip("ROCK repo not available at expected path")
        src = rock_file.read_text()
        # Find post_proxy method and check no hardcoded timeout=120
        in_post_proxy = False
        for line in src.splitlines():
            if "async def post_proxy" in line or "def post_proxy" in line:
                in_post_proxy = True
            if in_post_proxy and line.strip().startswith(("async def ", "def ")) and "post_proxy" not in line:
                break
            if in_post_proxy and "timeout=120" in line:
                pytest.fail("post_proxy still contains hardcoded timeout=120")


# ===================================================================
# Fix 1: request_timeout default 600 → 1800
# ===================================================================


class TestFix1RequestTimeout:
    """Fix 1: OpenClaw request_timeout default should be 1800."""

    def test_default_request_timeout_1800(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost:9001/v1"})
        assert agent._request_timeout == 1800.0

    def test_custom_request_timeout(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost:9001/v1", "request_timeout": 3600})
        assert agent._request_timeout == 3600.0


# ===================================================================
# Fix 2: proxy_timeout default 600 → 1800
# ===================================================================


class TestFix2ProxyTimeout:
    """Fix 2: ROCK sandbox proxy_timeout default should be 1800."""

    def test_rock_session_default_proxy_timeout(self):
        """ROCKSession.__init__ default proxy_timeout should be 1800."""
        from alphadiana.sandbox.rock import ROCKSession

        sig = inspect.signature(ROCKSession.__init__)
        default = sig.parameters["proxy_timeout"].default
        assert default == 1800, f"Expected default proxy_timeout=1800, got {default}"

    def test_rock_sandbox_setup_default_proxy_timeout(self):
        """ROCKSandbox.setup should default proxy_timeout to 1800."""
        from alphadiana.sandbox import rock as rock_module

        src = inspect.getsource(rock_module.ROCKSandbox.setup)
        assert "1800" in src, "ROCKSandbox.setup should default proxy_timeout to 1800"

    def test_proxy_timeout_info_log(self):
        """Setting proxy_timeout should log an INFO message about SDK-only scope."""
        from alphadiana.sandbox import rock as rock_module

        src = inspect.getsource(rock_module.ROCKSession._start_sandbox)
        assert "logger.info" in src and "proxy_timeout" in src


# ===================================================================
# Fix 3: Retry delay differentiation
# ===================================================================


class TestFix3RetryDelay:
    """Fix 3: Retry delay should be much longer for empty_response."""

    def test_max_attempts_default_5(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost:9001/v1"})
        assert agent._max_attempts == 5

    def test_empty_response_uses_longer_delay(self):
        """Source should differentiate empty_response from other errors."""
        from alphadiana.agent.openclaw import OpenClawAgent

        src = inspect.getsource(OpenClawAgent.solve)
        assert "empty_response" in src, "solve() should check for empty_response error_type"
        # Should have at least two different delay computation paths
        assert src.count("delay = min(") >= 2, "Should have separate delay for empty vs other errors"

    def test_empty_response_base_delay_at_least_60(self):
        """empty_response retry should use at least 60s base delay."""
        from alphadiana.agent.openclaw import OpenClawAgent

        src = inspect.getsource(OpenClawAgent.solve)
        # Find the retry-delay branch for empty_response (last_error_type check).
        # There may be multiple "empty_response" occurrences; find the one in the
        # retry-delay block which contains the 60s base.
        idx = src.find('last_error_type == "empty_response"')
        assert idx != -1, "solve() should have a retry delay branch for empty_response"
        nearby = src[idx : idx + 400]
        assert "60" in nearby, "empty_response delay should use 60s base"


# ===================================================================
# Fix 4: max_tokens fallback 32768 → 65536
# ===================================================================


class TestFix4MaxTokens:
    """Fix 4: Default max_tokens fallback should be 65536."""

    def test_default_max_tokens_fallback(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost:9001/v1"})
        with patch("httpx.get", side_effect=Exception("no server")):
            result = agent._resolve_max_tokens()
        assert result == 65536, f"Expected fallback 65536, got {result}"

    def test_explicit_max_tokens_preserved(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost:9001/v1", "max_tokens": 131072})
        result = agent._resolve_max_tokens()
        assert result == 131072


# ===================================================================
# Fix 5: Example YAML includes recommended settings
# ===================================================================


class TestFix5ExampleYaml:
    """Fix 5: Example YAML should have recommended timeout settings."""

    @pytest.fixture(autouse=True)
    def _check_yaml_exists(self):
        self.yaml_path = Path("configs/examples/openclaw_aime2024.yaml")
        if not self.yaml_path.exists():
            pytest.skip("Example YAML not found")

    def test_yaml_has_max_tokens(self):
        import yaml

        data = yaml.safe_load(self.yaml_path.read_text())
        agent_config = data.get("agent", {}).get("config", {})
        assert "max_tokens" in agent_config
        assert agent_config["max_tokens"] >= 65536

    def test_yaml_has_request_timeout(self):
        import yaml

        data = yaml.safe_load(self.yaml_path.read_text())
        agent_config = data.get("agent", {}).get("config", {})
        assert "request_timeout" in agent_config
        assert agent_config["request_timeout"] >= 1800

    def test_yaml_has_max_attempts(self):
        import yaml

        data = yaml.safe_load(self.yaml_path.read_text())
        agent_config = data.get("agent", {}).get("config", {})
        assert "max_attempts" in agent_config
        assert agent_config["max_attempts"] >= 5


# ===================================================================
# Fix 6: Dashboard deduplicates task_ids
# ===================================================================


class TestFix6DashboardDedup:
    """Fix 6: Dashboard should not show duplicate lines for multi-sample."""

    def test_no_duplicate_lines(self):
        from alphadiana.runner.dashboard import PlainTextDashboard

        class FakeTask:
            def __init__(self, tid):
                self.task_id = tid

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "dashboard.txt"
            # Simulate num_samples=3: same task appears 3 times in work_items
            tasks = [FakeTask("t1"), FakeTask("t1"), FakeTask("t1"), FakeTask("t2"), FakeTask("t2")]
            db = PlainTextDashboard(path, tasks, samples_per_task=3)
            content = path.read_text()
            problem_lines = [l for l in content.splitlines() if l.startswith("Problem")]
            assert len(problem_lines) == 2, f"Expected 2 lines, got {len(problem_lines)}: {problem_lines}"

    def test_samples_aggregated(self):
        from alphadiana.runner.dashboard import PlainTextDashboard

        class FakeTask:
            def __init__(self, tid):
                self.task_id = tid

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "dashboard.txt"
            tasks = [FakeTask("t1"), FakeTask("t1")]
            db = PlainTextDashboard(path, tasks, samples_per_task=2)
            db.update("t1", True)
            db.update("t1", False)
            content = path.read_text()
            assert "OX" in content
            assert "(0 left)" in content


# ===================================================================
# Fix 7: Per-task JSON merges all samples into one file
# ===================================================================


class TestFix7PerTaskJsonMerge:
    """Fix 7: All samples for same task should be in one JSON file."""

    def test_single_file_for_multi_sample(self):
        from alphadiana.results.result_store import ResultStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResultStore(tmpdir, "run1")
            store._ensure_dirs()
            store._save_per_task_json("task_a", {"task_id": "task_a", "sample_index": 0, "score": 1})
            store._save_per_task_json("task_a", {"task_id": "task_a", "sample_index": 1, "score": 0})
            store._save_per_task_json("task_a", {"task_id": "task_a", "sample_index": 2, "score": 1})
            tasks_dir = Path(tmpdir) / "run1" / "tasks"
            task_files = list(tasks_dir.glob("task_a*"))
            assert len(task_files) == 1, f"Expected 1 file, got {len(task_files)}: {task_files}"
            assert task_files[0].name == "task_a.json"
            content = json.loads(task_files[0].read_text())
            assert isinstance(content, list)
            assert len(content) == 3
            assert [r["sample_index"] for r in content] == [0, 1, 2]

    def test_replace_existing_sample(self):
        from alphadiana.results.result_store import ResultStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResultStore(tmpdir, "run1")
            store._ensure_dirs()
            store._save_per_task_json("task_b", {"task_id": "task_b", "sample_index": 0, "score": 0})
            store._save_per_task_json("task_b", {"task_id": "task_b", "sample_index": 0, "score": 1})
            content = json.loads((Path(tmpdir) / "run1" / "tasks" / "task_b.json").read_text())
            assert isinstance(content, list)
            assert len(content) == 1
            assert content[0]["score"] == 1

    def test_migrate_old_single_record_format(self):
        from alphadiana.results.result_store import ResultStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResultStore(tmpdir, "run1")
            store._ensure_dirs()
            tasks_dir = Path(tmpdir) / "run1" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            old_record = {"task_id": "task_c", "sample_index": 0, "score": 1}
            (tasks_dir / "task_c.json").write_text(json.dumps(old_record))
            store._save_per_task_json("task_c", {"task_id": "task_c", "sample_index": 1, "score": 0})
            content = json.loads((tasks_dir / "task_c.json").read_text())
            assert isinstance(content, list)
            assert len(content) == 2


# ===================================================================
# Fix 8: YAML config supports environment variable expansion
# ===================================================================


class TestFix8EnvVarExpansion:
    """Fix 8: YAML config should expand $VAR and ${VAR}."""

    def test_expand_env_vars_function(self):
        from alphadiana.config.experiment_config import _expand_env_vars

        os.environ["TEST_FIX8_VAR"] = "hello"
        try:
            assert _expand_env_vars("$TEST_FIX8_VAR") == "hello"
            assert _expand_env_vars("${TEST_FIX8_VAR}_world") == "hello_world"
            assert _expand_env_vars({"key": "$TEST_FIX8_VAR"}) == {"key": "hello"}
            assert _expand_env_vars(["$TEST_FIX8_VAR", 42]) == ["hello", 42]
            assert _expand_env_vars(123) == 123
        finally:
            del os.environ["TEST_FIX8_VAR"]

    def test_from_yaml_expands_env_vars(self):
        os.environ["TEST_SANDBOX_ID"] = "abc123"
        os.environ["TEST_ROCK_PORT"] = "9001"
        try:
            yaml_content = """\
run_id: "test-run"
agent:
  name: openclaw
  version: "1.0"
  config:
    api_base: "http://localhost:${TEST_ROCK_PORT}/sandboxes/${TEST_SANDBOX_ID}/proxy/v1"
benchmark:
  name: aime
scorer:
  name: math_verify
"""
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                f.write(yaml_content)
                f.flush()
                from alphadiana.config.experiment_config import ExperimentConfig

                config = ExperimentConfig.from_yaml(f.name)
                assert "abc123" in config.agent_config["api_base"]
                assert "9001" in config.agent_config["api_base"]
                assert "${TEST_SANDBOX_ID}" not in config.agent_config["api_base"]
        finally:
            del os.environ["TEST_SANDBOX_ID"]
            del os.environ["TEST_ROCK_PORT"]
            os.unlink(f.name)

    def test_no_env_var_unchanged(self):
        from alphadiana.config.experiment_config import _expand_env_vars

        assert _expand_env_vars("plain text") == "plain text"
        assert _expand_env_vars("http://localhost:9001") == "http://localhost:9001"


# ===================================================================
# Fix 9: Health check on first solve()
# ===================================================================


class TestFix9HealthCheck:
    """Fix 9: OpenClaw should do one-time health check on first solve()."""

    def test_health_check_method_exists(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost:9001/v1"})
        assert hasattr(agent, "_health_check")
        assert callable(agent._health_check)

    def test_health_check_runs_once(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost:9001/v1"})
        with patch("httpx.get", side_effect=Exception("no server")) as mock_get:
            agent._health_check(
                "http://localhost:9001/v1/chat/completions",
                {"Authorization": "bearer OPENCLAW"},
            )
            assert mock_get.call_count == 1
            # Second call should be a no-op
            agent._health_check(
                "http://localhost:9001/v1/chat/completions",
                {"Authorization": "bearer OPENCLAW"},
            )
            assert mock_get.call_count == 1

    def test_health_check_called_in_solve(self):
        """solve() source should call _health_check."""
        from alphadiana.agent.openclaw import OpenClawAgent

        src = inspect.getsource(OpenClawAgent.solve)
        assert "_health_check" in src


# ===================================================================
# Fix 10: Example YAML documents timeout configuration
# ===================================================================


class TestFix10TimeoutDocs:
    """Fix 10: Example YAML should document timeout parameters."""

    @pytest.fixture(autouse=True)
    def _check_yaml_exists(self):
        self.yaml_path = Path("configs/examples/openclaw_aime2024.yaml")
        if not self.yaml_path.exists():
            pytest.skip("Example YAML not found")

    def test_yaml_documents_gateway_agent_timeout(self):
        content = self.yaml_path.read_text()
        assert "timeoutSeconds" in content or "timeout" in content.lower()

    def test_yaml_documents_rock_proxy(self):
        content = self.yaml_path.read_text()
        assert "proxy" in content.lower() and "timeout" in content.lower()
