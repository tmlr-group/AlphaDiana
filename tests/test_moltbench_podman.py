"""Unit tests for the MoltBench Podman container_engine switch.

These cover the engine-selection logic, the docker --gpus -> NVIDIA CDI device
mapping, and the assembled `podman run` argv (GPU flags appended last, volumes
preserved with :ro). No real container engine or GPU is required — a fake
PodmanCLI runner captures the argv.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from alphadiana.benchmarks.moltbench.harness import MoltBenchDockerAgent
from alphadiana.engine.container_runtime.podman_cli import PodmanCLI


def _make_agent(**config):
    agent = MoltBenchDockerAgent()
    base = {"agent_type": "openclaw", "image": "img:test", "mount_claude_auth": False}
    base.update(config)
    agent.setup(base)
    return agent


def test_default_engine_is_docker():
    agent = _make_agent()
    assert agent._container_engine == "docker"
    assert agent._podman is None


def test_podman_engine_builds_cli():
    agent = _make_agent(container_engine="podman", gpus="7")
    assert agent._container_engine == "podman"
    assert isinstance(agent._podman, PodmanCLI)


def test_invalid_engine_rejected():
    with pytest.raises(ValueError):
        _make_agent(container_engine="bogus")


@pytest.mark.parametrize(
    "gpus,expected",
    [
        ("all", "all"),
        ("7", "7"),
        ("device=7", "7"),
        ("device=0,1", ["0", "1"]),
        ("", "all"),
    ],
)
def test_cdi_devices_mapping(gpus, expected):
    agent = _make_agent(container_engine="podman", gpus=gpus)
    assert agent._cdi_devices() == expected


def test_podman_run_argv_has_cdi_after_volumes():
    captured = {}

    def fake_runner(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="container123\n", stderr="")

    agent = _make_agent(container_engine="podman", gpus="7")
    agent._podman = PodmanCLI(runner=fake_runner)

    cid = agent._podman_run("/tmp/task_abc")
    assert cid == "container123"

    argv = captured["argv"]
    assert argv[:2] == ["podman", "run"]
    assert "-d" in argv
    # volume preserved with :ro
    assert "/tmp/task_abc:/workspace/task:ro" in argv
    # GPU injected via CDI, not --gpus
    assert "--gpus" not in argv
    assert "nvidia.com/gpu=7" in argv
    assert "label=disable" in argv
    # --device must come AFTER the volume (extra_args appended last)
    vol_idx = argv.index("/tmp/task_abc:/workspace/task:ro")
    dev_idx = argv.index("nvidia.com/gpu=7")
    assert dev_idx > vol_idx
    # image is the final argument
    assert argv[-1] == "img:test"


def test_podman_wait_timeout_becomes_timeoutexpired():
    def fake_runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    agent = _make_agent(container_engine="podman", gpus="7", request_timeout=1)
    agent._podman = PodmanCLI(runner=fake_runner)

    with pytest.raises(subprocess.TimeoutExpired):
        agent._podman_wait("container123")


# ── serving-noise protocol: warmup exclusion + steady-state median ──
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "mb_bench_serving", "moltbench/docker/mini_sglang/bench_serving.py"
)
_bench = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_bench)


def _pass(tput, correct=True):
    return {"throughput_tok_per_s": tput, "p50_ttft_ms": 100.0,
            "correct": correct, "num_success": 64, "num_error": 0}


def test_aggregate_excludes_warmup_and_takes_median():
    # cold first pass must be dropped; reported throughput is median of the rest
    passes = [_pass(600.0), _pass(19000.0), _pass(20000.0), _pass(21000.0)]
    agg = _bench._aggregate_passes(passes, warmup=1)
    assert agg["throughput_tok_per_s"] == 20000.0       # median of 19k/20k/21k
    assert agg["cold_throughput_tok_per_s"] == 600.0    # cold preserved for reference
    assert agg["measured_passes"] == 3 and agg["warmup_passes"] == 1
    assert agg["correct"] is True


def test_aggregate_correct_false_if_any_measured_pass_fails():
    passes = [_pass(600.0), _pass(19000.0), _pass(0.0, correct=False)]
    agg = _bench._aggregate_passes(passes, warmup=1)
    assert agg["correct"] is False


def test_aggregate_survives_failed_pass_missing_keys():
    # A wholly-failed pass returns a REDUCED dict (no num_success/num_error/p50)
    # — exactly what _run_benchmark emits when every request errors. Aggregation
    # must not KeyError-crash the harness on it (regression: c384 probe crash).
    failed = {"throughput_tok_per_s": 0.0, "correct": False,
              "error": "256 requests failed: timeout"}
    passes = [_pass(600.0), _pass(19000.0), failed]
    agg = _bench._aggregate_passes(passes, warmup=1)
    assert agg["correct"] is False
    assert agg["num_success"] == 0 and agg["num_error"] == 0
    assert agg["measured_passes"] == 2


# ── docker path: no --rm (artifacts are cp'd out after the container exits) ──
def test_docker_run_has_no_rm_flag():
    captured = {}
    import alphadiana.benchmarks.moltbench.harness as _h

    def fake_run(cmd, **kw):
        captured["argv"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="cid\n", stderr="")

    agent = _make_agent(gpus="7")  # docker engine
    orig = _h._run
    _h._run = fake_run
    try:
        agent._docker_run("/tmp/task_xyz")
    finally:
        _h._run = orig
    argv = captured["argv"]
    assert argv[:3] == ["docker", "run", "-d"]
    assert "--rm" not in argv          # would delete container before docker cp
    assert "/tmp/task_xyz:/workspace/task:ro" in argv


# ── loader: optimization_only scope metadata (Design-4 §3/§9) ──
def test_loader_declares_optimization_only_scope():
    from alphadiana.benchmarks.moltbench.benchmark import MoltBenchBenchmark
    b = MoltBenchBenchmark()
    tasks = b._load_mini_sglang_tasks({"task": "mini_sglang", "difficulty": "easy",
                                       "max_attempts": 3})
    md = tasks[0].metadata
    assert md["moltbench_task_level"] == "optimization_only"
    assert md["stability_metric"] == "N/A"
    assert md["memory_metric"] == "N/A"
    assert md["drift_metric"] == "N/A"
    scoring = md["task_json"]["scoring"]
    assert scoring["stability_metric"] == "N/A" and scoring["memory_metric"] == "N/A"


# ── hidden verifier: eval_runner cost vector + MoltEvent logging ──
_er_spec = _ilu.spec_from_file_location(
    "mb_eval_runner", "moltbench/docker/mini_sglang/eval_runner.py"
)
_er = _ilu.module_from_spec(_er_spec)
_er_spec.loader.exec_module(_er)


def test_cost_vector_folds_in_token_cost(tmp_path):
    mc = tmp_path / "model_cost.json"
    mc.write_text(json.dumps({"total_model_calls": 7, "total_input_tokens": 1000,
                              "total_output_tokens": 500, "source": "claude-code"}))
    old = _er.MODEL_COST_FILE
    _er.MODEL_COST_FILE = str(mc)
    try:
        cost = _er._cost_vector(attempt=2, wall=120.0, og=1.25, correct=True)
    finally:
        _er.MODEL_COST_FILE = old
    assert cost["model_calls"] == 7
    assert cost["total_tokens"] == 1500
    assert cost["attempts_used"] == 2 and cost["wall_time_sec"] == 120.0
    # cost_per_improvement = wall / gain(=0.25)
    assert cost["cost_per_improvement"] == round(120.0 / 0.25, 1)


def test_cost_vector_without_model_cost_has_keys(tmp_path):
    old = _er.MODEL_COST_FILE
    _er.MODEL_COST_FILE = str(tmp_path / "absent.json")
    try:
        cost = _er._cost_vector(attempt=1, wall=10.0, og=0.9, correct=True)
    finally:
        _er.MODEL_COST_FILE = old
    assert "total_tokens" in cost and cost["total_tokens"] is None
    assert "model_calls" in cost
    assert cost["cost_per_improvement"] is None   # no gain (og < 1)


def test_event_writes_moltevent_schema(tmp_path):
    ev = tmp_path / "events.jsonl"
    old = _er.EVENTS_FILE
    _er.EVENTS_FILE = str(ev)
    _er.ARTIFACTS_DIR = str(tmp_path)
    try:
        _er._event("run_eval_call", {"mode": "fast"}, stage_id="improvement", attempt_id=1)
    finally:
        _er.EVENTS_FILE = old
    rec = json.loads(ev.read_text().splitlines()[0])
    for k in ("event_id", "event_type", "timestamp", "payload", "content_hash",
              "stage_id", "attempt_id", "agent_id", "visibility"):
        assert k in rec
    assert rec["event_type"] == "run_eval_call" and rec["attempt_id"] == 1


# ── harness trajectory: model-call collection from agent logs ──
_ca_spec = _ilu.spec_from_file_location(
    "mb_collect_artifacts", "moltbench/docker/mini_sglang/collect_artifacts.py"
)
_ca = _ilu.module_from_spec(_ca_spec)
_ca_spec.loader.exec_module(_ca)


def test_collect_artifacts_claude_tokens(tmp_path, monkeypatch):
    (tmp_path / "claude_output.jsonl").write_text(
        json.dumps({"type": "assistant", "message": {"model": "qwen",
                    "usage": {"input_tokens": 100, "output_tokens": 50}}}) + "\n" +
        json.dumps({"type": "assistant", "message": {"model": "qwen",
                    "usage": {"input_tokens": 200, "output_tokens": 80}}}) + "\n")
    monkeypatch.setattr(_ca.sys, "argv",
                        ["collect_artifacts.py", str(tmp_path), "claude-code",
                         str(tmp_path / "model_cost.json")])
    assert _ca.main() == 0
    summary = json.loads((tmp_path / "model_cost.json").read_text())
    assert summary["total_model_calls"] == 2
    assert summary["total_input_tokens"] == 300
    assert summary["total_output_tokens"] == 130
    assert summary["tokens_complete"] is True


def test_collect_artifacts_zeroclaw_documents_gap(tmp_path, monkeypatch):
    (tmp_path / "zeroclaw_output.txt").write_text("did some work\nmore work\n")
    monkeypatch.setattr(_ca.sys, "argv",
                        ["collect_artifacts.py", str(tmp_path), "zeroclaw",
                         str(tmp_path / "model_cost.json")])
    assert _ca.main() == 0
    summary = json.loads((tmp_path / "model_cost.json").read_text())
    assert summary["total_model_calls"] == 0
    assert "no per-call usage" in summary["note"]   # gap documented, not silently zero


# ── admission validator static mode passes end-to-end ──
def test_validate_task_static_all_pass():
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("mb_validate_task", "moltbench/validate_task.py")
    vt = ilu.module_from_spec(spec)
    spec.loader.exec_module(vt)
    checks = vt._checks_static()
    failed = [m for ok, m in checks if not ok]
    assert not failed, f"static checks failed: {failed}"
