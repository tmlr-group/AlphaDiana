from types import SimpleNamespace
from pathlib import Path

import pytest

from alphadiana.engine.config.experiment_config import ExperimentConfig
from alphadiana.engine.runner import (
    _MemoryScopeLifecycle,
    _configured_memory_scope,
    _effective_dispatch_concurrency,
    _is_gateway_autodeploy_agent,
    _should_predeploy_openclaw,
    _stabilize_openclaw_gateway_token,
    _validate_stateful_checkpoint_resume,
    Runner,
)
from alphadiana.harness.registry import AgentRegistry
from alphadiana.harness.openclaw.agent import OpenClawAgent
from alphadiana.utils.openclaw_security import is_weak_openclaw_gateway_token


def _config(scope: str = "", *, persistent: bool = False, max_concurrent: int = 5):
    agent_config = {"persistent_memory": persistent}
    if scope:
        agent_config["memory_scope"] = scope
    return SimpleNamespace(
        agent_config=agent_config,
        max_concurrent=max_concurrent,
        sandbox_name=None,
    )


@pytest.mark.parametrize("scope", ["intra_task", "cross_sample", "cross_task"])
def test_configured_memory_scope_accepts_supported_values(scope):
    persistent = scope != "intra_task"
    assert _configured_memory_scope(_config(scope, persistent=persistent)) == scope


def test_configured_memory_scope_preserves_legacy_configs_without_scope():
    assert _configured_memory_scope(_config()) == ""


def test_configured_memory_scope_rejects_unknown_value():
    with pytest.raises(ValueError, match="memory_scope"):
        _configured_memory_scope(_config("forever", persistent=True))


@pytest.mark.parametrize(
    ("scope", "persistent"),
    [("intra_task", True), ("cross_sample", False), ("cross_task", False)],
)
def test_configured_memory_scope_rejects_persistence_mismatch(scope, persistent):
    with pytest.raises(ValueError, match="persistent_memory"):
        _configured_memory_scope(_config(scope, persistent=persistent))


@pytest.mark.parametrize("scope", ["cross_sample", "cross_task"])
def test_stateful_memory_scopes_force_sequential_dispatch(scope):
    assert _effective_dispatch_concurrency(
        _config(scope, persistent=True, max_concurrent=8)
    ) == 1


def test_intra_task_scope_keeps_requested_dispatch_concurrency():
    assert _effective_dispatch_concurrency(
        _config("intra_task", persistent=False, max_concurrent=8)
    ) == 8


def test_cross_sample_lifecycle_resets_only_between_tasks():
    lifecycle = _MemoryScopeLifecycle("cross_sample")

    assert lifecycle.before_work_item("aime_0", 0) is False
    assert lifecycle.before_work_item("aime_0", 1) is False
    assert lifecycle.before_work_item("aime_0", 2) is False
    assert lifecycle.before_work_item("aime_1", 0) is True
    assert lifecycle.before_work_item("aime_1", 1) is False


@pytest.mark.parametrize("scope", ["", "intra_task", "cross_task"])
def test_other_memory_scopes_never_request_task_boundary_reset(scope):
    lifecycle = _MemoryScopeLifecycle(scope)

    assert lifecycle.before_work_item("aime_0", 0) is False
    assert lifecycle.before_work_item("aime_0", 1) is False
    assert lifecycle.before_work_item("aime_1", 0) is False


def _work_items():
    return [
        (SimpleNamespace(task_id=task_id), sample_index)
        for task_id in ("aime_0", "aime_1")
        for sample_index in range(2)
    ]


def test_cross_task_rejects_partial_checkpoint_resume():
    with pytest.raises(ValueError, match="cross_task"):
        _validate_stateful_checkpoint_resume(
            "cross_task", _work_items(), {("aime_0", 0)}
        )


def test_cross_sample_rejects_partially_completed_task_resume():
    with pytest.raises(ValueError, match="partially sampled"):
        _validate_stateful_checkpoint_resume(
            "cross_sample", _work_items(), {("aime_0", 0)}
        )


def test_cross_sample_allows_resume_at_complete_task_boundary():
    _validate_stateful_checkpoint_resume(
        "cross_sample", _work_items(), {("aime_0", 0), ("aime_0", 1)}
    )


@pytest.mark.parametrize("scope", ["cross_sample", "cross_task"])
def test_stateful_scope_allows_fully_completed_checkpoint(scope):
    _validate_stateful_checkpoint_resume(
        scope,
        _work_items(),
        {
            ("aime_0", 0),
            ("aime_0", 1),
            ("aime_1", 0),
            ("aime_1", 1),
        },
    )


def test_release_reference_matrix_has_three_harnesses_per_scope():
    root = Path(__file__).parents[1] / "configs" / "micro_runs" / "Memory"
    expected_harnesses = {"openclaw", "opencode", "zeroclaw"}

    for scope in ("intra_task", "cross_sample", "cross_task"):
        paths = sorted((root / scope).glob("aime2026_*_qwen35_27b.yaml"))
        assert len(paths) == 3

        configs = [ExperimentConfig.from_yaml(str(path)) for path in paths]
        assert {config.agent_name for config in configs} == expected_harnesses
        assert {config.agent_config["memory_scope"] for config in configs} == {scope}
        assert {config.max_concurrent for config in configs} == {1}
        assert {
            bool(config.agent_config.get("persistent_memory", False))
            for config in configs
        } == {scope != "intra_task"}


def test_openclaw_reference_keeps_provider_url_out_of_gateway_api_base(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://provider.example/v1")
    root = Path(__file__).parents[1] / "configs" / "micro_runs" / "Memory"
    config = ExperimentConfig.from_yaml(
        str(root / "intra_task" / "aime2026_openclaw_qwen35_27b.yaml")
    )

    assert config.agent_config.get("api_base") in (None, "")
    assert config.agent_config["OPENAI_BASE_URL"] == "http://provider.example/v1"
    assert _is_gateway_autodeploy_agent(config) is True


def test_openclaw_autodeploy_agent_and_runtime_share_strong_gateway_token(monkeypatch):
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test")
    root = Path(__file__).parents[1] / "configs" / "micro_runs" / "Memory"
    config = ExperimentConfig.from_yaml(
        str(root / "intra_task" / "aime2026_openclaw_qwen35_27b.yaml")
    )
    _stabilize_openclaw_gateway_token(config)
    agent = OpenClawAgent()

    agent.setup(config.agent_config)

    assert not is_weak_openclaw_gateway_token(agent._gateway_token)
    assert agent._runtime_manager._gateway_token == agent._gateway_token


@pytest.mark.parametrize(
    ("scope", "expected"),
    [("intra_task", True), ("cross_sample", False), ("cross_task", False)],
)
def test_stateful_openclaw_uses_shared_sandbox_instead_of_fresh_predeploy(
    monkeypatch, scope, expected
):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://provider.example/v1")
    root = Path(__file__).parents[1] / "configs" / "micro_runs" / "Memory"
    config = ExperimentConfig.from_yaml(
        str(root / scope / "aime2026_openclaw_qwen35_27b.yaml")
    )

    assert _should_predeploy_openclaw(config) is expected


def test_cross_sample_reset_recreates_agent_and_shared_session(monkeypatch):
    events = []

    class OldAgent:
        def teardown(self):
            events.append("old-agent-teardown")

    class NewAgent:
        version = ""

        def setup(self, config):
            events.append(("new-agent-setup", config, self.version))

        def teardown(self):
            pass

    class OldSession:
        def close(self):
            events.append("old-session-close")

    new_session = object()

    class Sandbox:
        def create_session(self):
            events.append("new-session-create")
            return new_session

    monkeypatch.setitem(AgentRegistry._registry, "memory-scope-test", NewAgent)
    config = SimpleNamespace(
        agent_name="memory-scope-test",
        agent_version="test-1",
        agent_config={"memory_scope": "cross_sample", "persistent_memory": True},
    )
    runner = Runner(config)
    runner.agent = OldAgent()
    runner.sandbox = Sandbox()

    replacement = runner._reset_cross_sample_state(OldSession())

    assert replacement is new_session
    assert isinstance(runner.agent, NewAgent)
    assert events == [
        "old-agent-teardown",
        ("new-agent-setup", config.agent_config, "test-1"),
        "old-session-close",
        "new-session-create",
    ]
