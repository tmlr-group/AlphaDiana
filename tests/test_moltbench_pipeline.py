"""Focused regression tests for the MoltBench AutoKernel pipeline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alphadiana.agent.base import AgentResponse
from alphadiana.agent.moltbench_docker import MoltBenchDockerAgent
from alphadiana.benchmark.moltbench_utils import build_task_json, parse_bench_log
from alphadiana.scorer.moltbench import MoltBenchScorer
from scripts import full_pipeline


def test_parse_bench_log_accepts_reference_time_ms():
    log_text = "\n".join([
        "correctness: PASS",
        "speedup: 4.500x",
        "kernel_time_ms: 1.2345",
        "reference_time_ms: 5.5555",
    ])
    parsed = parse_bench_log(log_text)

    assert parsed["correctness"] == "PASS"
    assert parsed["speedup"] == 4.5
    assert parsed["kernel_time_ms"] == 1.2345
    assert parsed["reference_time_ms"] == 5.5555
    assert parsed["pytorch_time_ms"] == 5.5555


def test_scorer_uses_reference_time_ms():
    scorer = MoltBenchScorer()
    scorer.setup({})

    task = SimpleNamespace(ground_truth={"level": 1, "problem_id": 1})
    response = AgentResponse(answer={
        "correctness": "PASS",
        "speedup": 2.25,
        "kernel_time_ms": 1.1,
        "reference_time_ms": 2.475,
    })

    result = scorer.score(task, response)

    assert result.correct is True
    assert result.score == 2.25
    assert result.metadata["reference_time_ms"] == 2.475
    assert result.metadata["pytorch_time_ms"] == 2.475


def test_scorer_marks_nonzero_exit_as_failure():
    scorer = MoltBenchScorer()
    scorer.setup({})

    task = SimpleNamespace(ground_truth={"level": 1, "problem_id": 1})
    response = AgentResponse(
        answer={
            "correctness": "PASS",
            "speedup": 2.25,
            "kernel_time_ms": 1.1,
            "reference_time_ms": 2.475,
        },
        metadata={"exit_code": 143},
    )

    result = scorer.score(task, response)

    assert result.correct is False
    assert result.score == 0.0
    assert result.metadata["exit_code"] == 143
    assert "exited with code 143" in result.predicted


def test_scorer_allows_benign_claude_max_turn_exit():
    scorer = MoltBenchScorer()
    scorer.setup({})

    task = SimpleNamespace(ground_truth={"level": 1, "problem_id": 1})
    response = AgentResponse(
        answer={
            "correctness": "PASS",
            "speedup": 1.25,
            "kernel_time_ms": 1.1,
            "reference_time_ms": 1.375,
        },
        metadata={"exit_code": 1, "exit_benign": True},
    )

    result = scorer.score(task, response)

    assert result.correct is True
    assert result.score == 1.25
    assert result.metadata["exit_benign"] is True


def test_moltbench_docker_adds_network_mode(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, timeout=None, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="container123\n", stderr="")

    monkeypatch.setattr("alphadiana.agent.moltbench_docker._run", fake_run)

    agent = MoltBenchDockerAgent()
    agent.setup({
        "agent_type": "openclaw",
        "network_mode": "host",
        "env": {"OPENAI_MODEL_NAME": "deepseek-chat"},
    })
    cid = agent._docker_run("/tmp/fake-task")

    assert cid == "container123"
    assert "--network" in captured["cmd"]
    idx = captured["cmd"].index("--network")
    assert captured["cmd"][idx + 1] == "host"


def test_moltbench_docker_mounts_host_claude_auth(monkeypatch, tmp_path):
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, timeout=None, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="container123\n", stderr="")

    monkeypatch.setattr("alphadiana.agent.moltbench_docker._run", fake_run)
    monkeypatch.setattr("alphadiana.agent.moltbench_docker.Path.home", lambda: tmp_path)

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text("{}", encoding="utf-8")

    agent = MoltBenchDockerAgent()
    agent.setup({
        "agent_type": "claude-code",
        "env": {"ANTHROPIC_API_KEY": "dummy"},
    })
    cid = agent._docker_run("/tmp/fake-task")

    assert cid == "container123"
    assert f"{tmp_path / '.claude'}:/root/.claude:ro" in captured["cmd"]
    assert f"{tmp_path / '.claude.json'}:/root/.claude.json:ro" in captured["cmd"]


def test_moltbench_docker_auto_detects_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    agent = MoltBenchDockerAgent()
    agent.setup({"agent_type": "claude-code", "env": {}})

    assert agent._env["HTTP_PROXY"] == "http://127.0.0.1:7897"
    assert agent._env["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert agent._env["NO_PROXY"] == "localhost,127.0.0.1"


def test_stage_autokernel_tree_excludes_runtime_noise(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / ".venv" / "bin").mkdir(parents=True)
    (src / ".git").mkdir()
    (src / "workspace").mkdir()
    (src / "__pycache__").mkdir()
    (src / "kernelbench").mkdir()
    (src / "keep.txt").write_text("keep", encoding="utf-8")
    (src / ".venv" / "bin" / "python").write_text("python", encoding="utf-8")
    (src / ".git" / "HEAD").write_text("old", encoding="utf-8")
    (src / "workspace" / "stale.txt").write_text("stale", encoding="utf-8")

    staged = full_pipeline.stage_autokernel_tree(src, dst / "autokernel")

    assert (staged / "keep.txt").exists()
    assert not (staged / ".venv").exists()
    assert not (staged / ".git").exists()
    assert not (staged / "__pycache__").exists()
    assert not (staged / "workspace" / "stale.txt").exists()


def test_step2_setup_workspace_passes_backend_and_reinitializes_git(tmp_path, monkeypatch):
    src = tmp_path / "autokernel_src"
    (src / "kernelbench").mkdir(parents=True)
    (src / ".venv" / "bin").mkdir(parents=True)
    (src / ".git").mkdir()
    (src / "workspace").mkdir()
    (src / ".git" / "HEAD").write_text("old-head", encoding="utf-8")
    (src / "workspace" / "stale.txt").write_text("stale", encoding="utf-8")
    (src / "README.md").write_text("src", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run_cmd(cmd, cwd=None, timeout=600):
        commands.append(list(cmd))
        cwd_path = Path(cwd) if cwd else None
        if cmd[:2] == ["python3", "kernelbench/bridge.py"]:
            backend = cmd[cmd.index("--backend") + 1]
            assert cwd_path is not None
            (cwd_path / "workspace" / "kb_active").mkdir(parents=True, exist_ok=True)
            (cwd_path / "workspace" / "kb_active" / "metadata.json").write_text(
                json.dumps({"backend": backend}),
                encoding="utf-8",
            )
            (cwd_path / "kernel.py").write_text(
                "class ModelNew:\n    def __init__(self):\n        super(Model, self)\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="setup ok\n", stderr="")
        if cmd[:2] == ["git", "init"]:
            assert cwd_path is not None
            (cwd_path / ".git").mkdir(exist_ok=True)
        if cmd[:2] == ["git", "config"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "add"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(full_pipeline, "AUTOKERNEL_DIR", str(src))
    monkeypatch.setattr(full_pipeline, "run_cmd", fake_run_cmd)

    task = SimpleNamespace(metadata={"task_json": {
        "level": 1,
        "problem_id": 1,
        "backend": "triton",
    }})

    work_dir, ak_dir = full_pipeline.step2_setup_workspace(task)
    try:
        metadata = json.loads((ak_dir / "workspace" / "kb_active" / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["backend"] == "triton"
        assert "--backend" in commands[0]
        assert not (ak_dir / ".venv").exists()
        assert not (ak_dir / "workspace" / "stale.txt").exists()
        assert (ak_dir / ".git").exists()
        assert "super()" in (ak_dir / "kernel.py").read_text(encoding="utf-8")
    finally:
        import shutil

        shutil.rmtree(work_dir, ignore_errors=True)


def test_build_task_json_exposes_generic_container_contract():
    task_json = build_task_json(
        level=1,
        problem_id=7,
        max_rounds=3,
        backend="triton",
        prompt="optimize this kernel",
    )

    assert task_json["benchmark_id"] == "autokernel"
    assert task_json["workdir"] == "/workspace/autokernel"
    assert task_json["setup_cmd"][-4:] == ["--backend", "triton", "--source", "hf"]
    assert task_json["verify_cmd"] == ["python3", "kernelbench/bench_kb.py"]
    assert task_json["primary_edit_file"] == "kernel.py"
    assert task_json["prompt"] == "optimize this kernel"


def test_openclaw_leaf_image_overrides_run_task():
    dockerfile = (PROJECT_ROOT / "docker" / "moltbench" / "Dockerfile.openclaw").read_text(encoding="utf-8")
    assert "COPY docker/moltbench/run_task.sh /workspace/run_task.sh" in dockerfile


def test_claude_leaf_image_overrides_run_task():
    dockerfile = (PROJECT_ROOT / "docker" / "moltbench" / "Dockerfile.claude-code").read_text(encoding="utf-8")
    assert "COPY docker/moltbench/run_task.sh /workspace/run_task.sh" in dockerfile


def test_run_task_gateway_probe_accepts_http_responses():
    script = (PROJECT_ROOT / "docker" / "moltbench" / "run_task.sh").read_text(encoding="utf-8")
    assert "probe_code=$(curl -sS -o /dev/null -w '%{http_code}'" in script
    assert 'if [[ "$probe_code" != "000" ]]; then' in script


def test_run_task_openclaw_supports_explicit_timeouts():
    script = (PROJECT_ROOT / "docker" / "moltbench" / "run_task.sh").read_text(encoding="utf-8")
    assert 'local agent_timeout_sec="${OPENCLAW_AGENT_TIMEOUT_SEC:-}"' in script
    assert 'local chat_timeout_sec="${OPENCLAW_CHAT_MAX_TIME_SEC:-}"' in script
    assert 'payload.setdefault("agents", {}).setdefault("defaults", {})["timeoutSeconds"] = int(agent_timeout)' in script
    assert '--max-time "$chat_timeout_sec"' in script


def test_run_task_openclaw_preserves_partial_artifacts_on_curl_failure():
    script = (PROJECT_ROOT / "docker" / "moltbench" / "run_task.sh").read_text(encoding="utf-8")
    assert "set +e" in script
    assert "curl_status=$?" in script
    assert "set -e" in script


def test_run_task_supports_manifest_driven_prompt_and_commands():
    script = (PROJECT_ROOT / "docker" / "moltbench" / "run_task.sh").read_text(encoding="utf-8")
    assert 'SETUP_CMD=$(python3 - <<' in script
    assert 'VERIFY_CMD=$(python3 - <<' in script
    assert 'TASK_PROMPT=$(python3 - <<' in script
    assert 'RESULT_JSON_PATH=$(python3 -c' in script
    assert 'cd "$WORKDIR"' in script
    assert 'local prompt="$TASK_PROMPT"' in script
    assert 'os.environ["MOLTBENCH_TASK_PROMPT"]' in script


def test_run_task_avoids_dangerous_skip_permissions_under_root():
    script = (PROJECT_ROOT / "docker" / "moltbench" / "run_task.sh").read_text(encoding="utf-8")
    assert 'if [[ "$(id -u)" != "0" ]]; then' in script
    assert 'claude_args+=(--dangerously-skip-permissions)' in script
