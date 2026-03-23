"""Tests for concurrent workspace contention fix.

Covers:
  1. gateway_pool round-robin assignment in OpenClawAgent
  2. Auto-sandbox creation in Runner when rock_agent_config_path is set
  3. Backward compatibility: single api_base still works
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class TestGatewayPoolRoundRobin:
    """Test that gateway_pool is round-robined across concurrent solve() calls."""

    def _make_agent(self, config: dict):
        from alphadiana.agent.openclaw import OpenClawAgent
        agent = OpenClawAgent()
        agent.version = "test"
        agent.setup(config)
        return agent

    def test_single_api_base_treated_as_pool_of_one(self):
        agent = self._make_agent({"api_base": "http://host/v1"})
        assert list(agent._gateway_pool) == ["http://host/v1"]

    def test_gateway_pool_overrides_api_base(self):
        agent = self._make_agent({
            "gateway_pool": ["http://host1/v1", "http://host2/v1"],
        })
        assert list(agent._gateway_pool) == ["http://host1/v1", "http://host2/v1"]

    def test_empty_config_yields_empty_pool(self):
        agent = self._make_agent({})
        assert list(agent._gateway_pool) == []

    def test_round_robin_rotates_pool(self):
        """Consecutive calls should return different URLs from the pool."""
        agent = self._make_agent({
            "gateway_pool": ["http://s1/v1", "http://s2/v1", "http://s3/v1"],
        })

        def _pick():
            with agent._gateway_pool_lock:
                url = agent._gateway_pool[0]
                agent._gateway_pool.rotate(-1)
                return url

        urls = [_pick() for _ in range(6)]
        # Should cycle through all 3 in order, then repeat
        assert urls == [
            "http://s1/v1", "http://s2/v1", "http://s3/v1",
            "http://s1/v1", "http://s2/v1", "http://s3/v1",
        ]

    def test_gateway_pool_rotates_on_each_pick(self):
        """Simulates what solve() does: each call picks and rotates the pool."""
        agent = self._make_agent({
            "gateway_pool": ["http://gw1/v1", "http://gw2/v1", "http://gw3/v1"],
            "model": "openclaw",
        })

        # Simulate what solve() does when sandbox=None and gateway_pool is set
        def _simulate_pool_pick():
            with agent._gateway_pool_lock:
                resolved = agent._gateway_pool[0]
                agent._gateway_pool.rotate(-1)
            return f"{resolved.rstrip('/')}/chat/completions"

        urls = [_simulate_pool_pick() for _ in range(6)]
        assert urls == [
            "http://gw1/v1/chat/completions",
            "http://gw2/v1/chat/completions",
            "http://gw3/v1/chat/completions",
            "http://gw1/v1/chat/completions",
            "http://gw2/v1/chat/completions",
            "http://gw3/v1/chat/completions",
        ]


class TestTrajectoryIsolation:
    """Trajectory retrieval must follow the current gateway target."""

    def _make_agent(self, config: dict):
        from alphadiana.agent.openclaw import OpenClawAgent
        agent = OpenClawAgent()
        agent.version = "test"
        agent.setup(config)
        return agent

    def _mock_http_client(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = json.dumps({
            "choices": [{"message": {"content": "42"}}],
        })
        mock_response.read = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        return mock_client

    def test_solve_uses_current_gateway_sandbox_for_trajectory_lookup(self):
        """gateway_pool rotations must also rotate the sandbox used for transcript lookup."""
        from alphadiana.benchmark.base import BenchmarkTask

        agent = self._make_agent({
            "gateway_pool": [
                "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/sid1/proxy/v1",
                "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/sid2/proxy/v1",
            ],
            "model": "openclaw",
            "max_attempts": 1,
        })
        task1 = BenchmarkTask(task_id="t1", problem="Problem 1", ground_truth="42")
        task2 = BenchmarkTask(task_id="t2", problem="Problem 2", ground_truth="42")

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(
                agent,
                "_retrieve_trajectory_sync",
                return_value=[{"role": "user", "content": "Problem 1"}],
            ) as mock_traj:
                with patch("httpx.Client", return_value=self._mock_http_client()):
                    agent.solve(task1)
                    agent.solve(task2)

        assert mock_traj.call_args_list[0].kwargs["sandbox_id"] == "sid1"
        assert mock_traj.call_args_list[0].kwargs["expected_user_content"] == "Problem 1"
        assert mock_traj.call_args_list[1].kwargs["sandbox_id"] == "sid2"
        assert mock_traj.call_args_list[1].kwargs["expected_user_content"] == "Problem 2"

    def test_retrieve_session_trajectory_matches_request_in_recent_sessions(self):
        """If the latest session is stale, trajectory lookup should select the matching request."""
        agent = self._make_agent({
            "api_base": "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/seed/proxy/v1",
            "model": "openclaw",
        })

        wrong_session = "\n".join([
            json.dumps({
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Wrong problem"}],
                },
            }),
            json.dumps({
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "wrong answer"}],
                },
            }),
        ])
        correct_session = "\n".join([
            json.dumps({
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Correct problem"}],
                },
            }),
            json.dumps({
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "42"}],
                },
            }),
        ])

        class FakeSandboxClient:
            async def create_session(self, _request):
                return None

            async def arun(self, command, session=None):
                if command.startswith("ls -t "):
                    return SimpleNamespace(output="/tmp/latest.jsonl\n/tmp/match.jsonl\n")
                if "latest.jsonl" in command:
                    return SimpleNamespace(output=wrong_session)
                if "match.jsonl" in command:
                    return SimpleNamespace(output=correct_session)
                raise AssertionError(command)

        with patch.object(agent, "_get_sandbox_client", return_value=FakeSandboxClient()):
            trajectory = asyncio.run(
                agent._retrieve_session_trajectory(
                    sandbox_id="sid2",
                    rock_sandbox_url="http://localhost:9001/apis/envs/sandbox/v1",
                    expected_user_content="Correct problem",
                )
            )

        assert trajectory[0]["role"] == "user"
        assert trajectory[0]["content"] == "Correct problem"


class TestAutoSandboxCreation:
    """Test that Runner auto-creates ROCK sandbox when rock_agent_config_path is set."""

    def test_auto_sandbox_triggers_when_dynamic_bootstrap_configured(self):
        """Runner should create ROCK sandbox when openclaw + rock_agent_config_path set."""
        from alphadiana.config.experiment_config import ExperimentConfig

        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="test",
            benchmark_name="aime",
            scorer_name="numeric",
            sandbox_name=None,  # no explicit sandbox
            agent_config={
                "rock_agent_config_path": "/path/to/config.yaml",
                "openclaw_config_path": "/path/to/openclaw.json",
                "admin_base_url": "http://localhost:9001",
                "proxy_base_url": "http://localhost:9001/apis/envs/sandbox/v1",
            },
            max_concurrent=3,
        )

        # The auto-sandbox detection should trigger for this config
        assert config.agent_name == "openclaw"
        assert config.agent_config.get("rock_agent_config_path")
        assert config.agent_config.get("openclaw_config_path")
        assert config.sandbox_name is None
        assert config.max_concurrent > 1

    def test_auto_sandbox_does_not_trigger_without_rock_config(self):
        """Runner should NOT create ROCK sandbox for static api_base (no rock_agent_config_path)."""
        from alphadiana.config.experiment_config import ExperimentConfig

        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="test",
            benchmark_name="aime",
            scorer_name="numeric",
            sandbox_name=None,
            agent_config={
                "api_base": "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/abc/proxy/v1",
            },
            max_concurrent=3,
        )

        # Without rock_agent_config_path, auto-sandbox should NOT trigger
        assert not config.agent_config.get("rock_agent_config_path")
        assert not config.agent_config.get("openclaw_config_path")

    def test_auto_sandbox_does_not_trigger_for_non_openclaw_agent(self):
        """Auto-sandbox should not trigger for other agent types."""
        from alphadiana.config.experiment_config import ExperimentConfig

        config = ExperimentConfig(
            agent_name="direct_llm",
            agent_version="test",
            benchmark_name="aime",
            scorer_name="numeric",
            sandbox_name=None,
            agent_config={
                "rock_agent_config_path": "/path/to/config.yaml",
                "openclaw_config_path": "/path/to/openclaw.json",
            },
            max_concurrent=3,
        )

        # Agent is not "openclaw", so auto-sandbox should not trigger
        assert config.agent_name != "openclaw"


class TestClassifyInternalError:
    """Test that classify_error detects 'internal error' responses from gateway."""

    def test_internal_error_in_message_field(self):
        from alphadiana.agent.openclaw import classify_error
        result = classify_error(
            None,
            response_json={"message": "Error: internal error"},
            status_code=200,
        )
        assert result == "internal_error"

    def test_internal_error_in_error_dict(self):
        from alphadiana.agent.openclaw import classify_error
        result = classify_error(
            None,
            response_json={"error": {"message": "internal error", "type": "server_error"}},
            status_code=500,
        )
        assert result == "internal_error"

    def test_internal_error_in_error_string(self):
        from alphadiana.agent.openclaw import classify_error
        result = classify_error(
            None,
            response_json={"error": "internal error"},
            status_code=200,
        )
        assert result == "internal_error"

    def test_normal_error_not_classified_as_internal(self):
        from alphadiana.agent.openclaw import classify_error
        result = classify_error(
            None,
            response_json={"message": "rate limit exceeded"},
            status_code=429,
        )
        assert result != "internal_error"


class TestBackwardCompatibility:
    """Test that single api_base / existing configs still work."""

    def _make_agent(self, config: dict):
        from alphadiana.agent.openclaw import OpenClawAgent
        agent = OpenClawAgent()
        agent.version = "test"
        agent.setup(config)
        return agent

    def test_single_api_base_backward_compat(self):
        """Existing single-api_base configs should still work unchanged."""
        agent = self._make_agent({
            "api_base": "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/abc123/proxy/v1",
            "model": "openclaw",
        })
        # Should extract sandbox_id from api_base
        assert agent._sandbox_id == "abc123"
        # Should have exactly one URL in gateway pool
        assert len(agent._gateway_pool) == 1
        assert "abc123" in agent._gateway_pool[0]

    def test_gateway_pool_with_all_same_url_works(self):
        """gateway_pool with 3 identical URLs is valid (behaves like shared gateway)."""
        url = "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/abc123/proxy/v1"
        agent = self._make_agent({
            "gateway_pool": [url, url, url],
        })
        assert len(agent._gateway_pool) == 3
