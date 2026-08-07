from pathlib import Path
from types import SimpleNamespace

import pytest

from alphadiana.analysis.report import ReportGenerator, RunSummary
from alphadiana.engine.config.experiment_config import ExperimentConfig
from alphadiana.engine.runner import (
    _MemoryScopeLifecycle,
    _configured_memory_scope,
    _effective_dispatch_concurrency,
    _is_gateway_autodeploy_agent,
    _should_predeploy_openclaw,
    _stabilize_openclaw_gateway_token,
    _validate_stateful_run_policy,
    _validate_stateful_checkpoint_resume,
    Runner,
)
from alphadiana.engine.task_dispatcher import TaskDispatcher
from alphadiana.harness.registry import AgentRegistry
from alphadiana.harness.openclaw.agent import OpenClawAgent, _clean_local_agent_reply
from alphadiana.harness.opencode.agent import OpenCodeAgent
from alphadiana.harness.zeroclaw.agent import _memory_entry_count
from alphadiana.utils.openclaw_security import is_weak_openclaw_gateway_token


def _config(scope: str = "", *, persistent: bool = False, max_concurrent: int = 5):
    agent_config = {"persistent_memory": persistent}
    if scope:
        agent_config["memory_scope"] = scope
    return SimpleNamespace(
        agent_config=agent_config,
        max_concurrent=max_concurrent,
        sandbox_name=None,
        task_retries=0,
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

    assert lifecycle.needs_reset("aime_0") is False
    lifecycle.commit("aime_0")
    assert lifecycle.needs_reset("aime_0") is False
    assert lifecycle.needs_reset("aime_1") is True
    lifecycle.commit("aime_1")
    assert lifecycle.needs_reset("aime_1") is False


def test_cross_sample_lifecycle_does_not_commit_failed_reset():
    lifecycle = _MemoryScopeLifecycle("cross_sample")
    lifecycle.commit("aime_0")

    assert lifecycle.needs_reset("aime_1") is True
    assert lifecycle.needs_reset("aime_1") is True


def test_cross_sample_reset_failure_keeps_old_state(monkeypatch):
    events = []

    class OldAgent:
        def teardown(self):
            events.append("old-agent-teardown")

    class NewAgent:
        version = ""

        def setup(self, config):
            events.append("new-agent-setup")

        def teardown(self):
            events.append("new-agent-teardown")

    class OldSession:
        def close(self):
            events.append("old-session-close")

    class FailingSandbox:
        def create_session(self):
            raise RuntimeError("startup failed")

    monkeypatch.setitem(AgentRegistry._registry, "memory-reset-failure-test", NewAgent)
    config = SimpleNamespace(
        agent_name="memory-reset-failure-test",
        agent_version="test-1",
        agent_config={"memory_scope": "cross_sample", "persistent_memory": True},
    )
    runner = Runner(config)
    old_agent = OldAgent()
    runner.agent = old_agent
    runner.sandbox = FailingSandbox()

    with pytest.raises(RuntimeError, match="startup failed"):
        runner._reset_cross_sample_state(OldSession())

    assert runner.agent is old_agent
    assert events == ["new-agent-setup", "new-agent-teardown"]


@pytest.mark.parametrize("scope", ["", "intra_task", "cross_task"])
def test_other_memory_scopes_never_request_task_boundary_reset(scope):
    lifecycle = _MemoryScopeLifecycle(scope)

    assert lifecycle.needs_reset("aime_0") is False
    lifecycle.commit("aime_0")
    assert lifecycle.needs_reset("aime_1") is False


@pytest.mark.parametrize("scope", ["cross_sample", "cross_task"])
def test_stateful_scope_rejects_task_retries(scope):
    config = _config(scope, persistent=True)
    config.task_retries = 1

    with pytest.raises(ValueError, match="task_retries=0"):
        _validate_stateful_run_policy(config)


def test_stateful_scope_accepts_zero_task_retries():
    _validate_stateful_run_policy(_config("cross_task", persistent=True))


def test_dispatcher_stop_on_error_does_not_run_later_stateful_items():
    seen = []

    def solve(item):
        seen.append(item)
        if item == "bad":
            raise RuntimeError("memory state is no longer trustworthy")
        return item

    outcomes = TaskDispatcher(max_concurrent=1, stop_on_error=True).dispatch(
        ["ok", "bad", "must-not-run"], solve
    )

    assert seen == ["ok", "bad"]
    assert [outcome["success"] for outcome in outcomes] == [True, False]


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
        assert {config.num_samples for config in configs} == {4}
        assert {bool(config.agent_config.get("memory_enabled")) for config in configs} == {True}
        assert {bool(config.agent_config.get("strict_memory")) for config in configs} == {True}
        if scope in {"cross_sample", "cross_task"}:
            assert {config.task_retries for config in configs} == {0}
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
        ("new-agent-setup", config.agent_config, "test-1"),
        "new-session-create",
        "old-session-close",
        "old-agent-teardown",
    ]


def test_opencode_teardown_removes_persistent_workdir(tmp_path):
    workdir = tmp_path / "opencode-persistent"
    workdir.mkdir()
    (workdir / "state.db").write_text("state", encoding="utf-8")
    agent = OpenCodeAgent()
    agent._persistent_workdir = workdir
    agent._last_session_id = "session-1"

    agent.teardown()

    assert not workdir.exists()
    assert agent._persistent_workdir is None
    assert agent._last_session_id == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Total 3 entries", 3),
        ("Entries: 4", 4),
        ("Total memories: 5", 5),
        ("Memory Statistics:\n  Backend: sqlite\n  Total:    6", 6),
        ("memory backend healthy", None),
    ],
)
def test_zeroclaw_memory_entry_count_formats(text, expected):
    assert _memory_entry_count(text) == expected


def test_openclaw_strict_memory_rejects_unverified_store(monkeypatch):
    agent = OpenClawAgent()
    agent._strict_memory = True
    agent._request_timeout = 10
    agent._config = {"enable_thinking": False}
    agent._oracle_feedback = False
    agent._user_system_prompt = ""
    monkeypatch.setattr(agent, "_build_local_agent_config_json", lambda **kwargs: "{}")
    monkeypatch.setattr(
        agent,
        "_run_local_agent_turn",
        lambda **kwargs: ("$$\\boxed{1}$$", "rc=0\nmem_before=0\nmem_after=0", True),
    )
    monkeypatch.setattr(
        agent,
        "_store_local_memory_entry",
        lambda **kwargs: ("store_rc=0\nmem_before=0\nmem_after=0", True),
    )
    task = SimpleNamespace(
        task_id="aime_0", metadata={}, problem="1+0?", ground_truth="1"
    )

    with pytest.raises(RuntimeError, match="verified LanceDB entry"):
        agent._try_solve_via_local_agent(
            task=task,
            sandbox=object(),
            runtime_info={},
            request_messages=[],
            user_text="1+0?",
            start=1.0,
        )


def test_openclaw_local_reply_rejects_cli_no_reply_sentinel():
    raw = "[plugins] memory-lancedb: plugin registered\n\nNo reply from agent.\n"

    assert _clean_local_agent_reply(raw) == ""


def test_dependent_memory_samples_are_not_labeled_standard_pass_at_k():
    summary = RunSummary(
        run_id="memory-dependent",
        agent="opencode",
        agent_version="1.3.2",
        benchmark="aime",
        total_tasks=4,
        completed=4,
        failed=0,
        accuracy=0.5,
        accuracy_total=0.5,
        mean_score=0.5,
        mean_wall_time_sec=1.0,
        total_tokens={},
        per_category={},
        num_samples=4,
        pass_at_k=0.75,
        avg_at_k=0.5,
        samples_independent=False,
    )

    markdown = ReportGenerator().to_markdown(summary)

    assert "Sequential Any@4" in markdown
    assert "Sequential Mean@4" in markdown
    assert "| Pass@4 |" not in markdown
