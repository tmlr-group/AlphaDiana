from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from alphadiana.agent.terminal_bench2_common import (
    LocalCommandResult,
    NATIVE_AGENT_PROMPT,
    TerminalBench2ContainerMixin,
    TerminalBench2RuntimeContext,
    VerifierResult,
)
from alphadiana.agent.terminal_bench2_incontainer import (
    IN_CONTAINER_AGENT_PROMPT,
    TerminalBench2InContainerMixin,
)
from alphadiana.agent.terminal_bench2_opencode import (
    TerminalBench2OpenCodeAgent,
    _jsonl_from_stdout,
)
from alphadiana.agent.terminal_bench2_openclaw import TerminalBench2OpenClawAgent
from alphadiana.agent.terminal_bench2_zeroclaw import (
    TerminalBench2ZeroClawAgent,
    _ZEROCLAW_SYSTEM_PROMPT,
)
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator
from alphadiana.container_runtime.podman_cli import PodmanCLI
from alphadiana.results.status import infer_score_status, is_valid_completed_record
from alphadiana.utils.rock_runtime import PREBUILT_SANDBOX_IMAGE


class _MixinHarness(TerminalBench2ContainerMixin):
    pass


class _InContainerHarness(TerminalBench2InContainerMixin):
    pass


def test_logs_dir_isolated_by_sample_and_execution_id(tmp_path: Path) -> None:
    harness = _MixinHarness()
    harness._logs_base_dir = tmp_path
    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo",
        ground_truth="1",
        metadata={"sample_index": 2, "execution_id": "abc123456789"},
    )

    logs_dir = harness._logs_dir_for_task(task)

    assert logs_dir == tmp_path / "tb2_demo_s2_abc12345"


def test_logs_base_dir_relative_config_resolves_to_absolute_bind_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    harness = _MixinHarness()
    harness._setup_container_config({"logs_base_dir": "logs/tb2-runtime"})
    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo",
        ground_truth="1",
        metadata={"sample_index": 0, "execution_id": "abcdef123456"},
    )

    logs_dir = harness._logs_dir_for_task(task)

    assert logs_dir == (tmp_path / "logs/tb2-runtime/tb2_demo_s0_abcdef12").resolve()
    assert logs_dir.is_absolute()


def test_terminal_bench2_helper_workspace_uses_control_side_helpers(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo task", ground_truth="1")

    helper_paths = harness._write_helper_scripts(
        tmp_path,
        "demo-container",
        task,
        prompt_text="demo prompt",
    )

    assert helper_paths["task"].read_text(encoding="utf-8") == "demo task"
    assert not helper_paths["task_hints"].exists()
    assert "docker exec demo-container bash -lc" in helper_paths["exec"].read_text(encoding="utf-8")
    assert "docker cp demo-container:" in helper_paths["copy_from"].read_text(encoding="utf-8")
    assert "docker cp \"$1\" demo-container:" in helper_paths["copy_to"].read_text(encoding="utf-8")
    assert "bash /tests/test.sh" in helper_paths["test"].read_text(encoding="utf-8")
    assert helper_paths["prompt"].read_text(encoding="utf-8") == "demo prompt"
    assert "Do not call `docker` directly" in helper_paths["agents"].read_text(encoding="utf-8")
    assert "do not call it during normal solving" in NATIVE_AGENT_PROMPT
    assert "manual debugging" in helper_paths["agents"].read_text(encoding="utf-8")


def test_terminal_bench2_podman_helpers_use_runtime_shim(tmp_path: Path) -> None:
    harness = _MixinHarness()
    harness._setup_container_config({"container_engine": "podman"})
    task = BenchmarkTask(task_id="tb2_demo", problem="demo task", ground_truth="1")

    helper_paths = harness._write_helper_scripts(tmp_path, "demo-container", task)

    exec_text = helper_paths["exec"].read_text(encoding="utf-8")
    copy_from_text = helper_paths["copy_from"].read_text(encoding="utf-8")
    assert "alphadiana.container_runtime.task_cli exec demo-container" in exec_text
    assert "alphadiana.container_runtime.task_cli cp demo-container:" in copy_from_text
    assert "docker exec" not in exec_text


def test_terminal_bench2_podman_start_container_uses_runtime(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "run":
            return subprocess.CompletedProcess(argv, 0, stdout="podman-cid\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    harness = _MixinHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    cid = harness._start_container("tb2-task:latest", logs_dir, task)

    assert cid == "podman-cid"
    assert calls[0][:4] == ["podman", "run", "-d", "--rm"]
    assert "--entrypoint" in calls[0]
    assert "/bin/sh" in calls[0]
    assert "--network" in calls[0]
    assert "slirp4netns:allow_host_loopback=true" in calls[0]
    assert "-v" in calls[0]
    assert f"{logs_dir}:/logs" in calls[0]
    assert calls[0][-3:] == ["tb2-task:latest", "-lc", "sleep infinity"]
    assert calls[1][:5] == ["podman", "exec", "--user", "0:0", "podman-cid"]


def test_terminal_bench2_podman_start_container_allows_network_override(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "run":
            return subprocess.CompletedProcess(argv, 0, stdout="podman-cid\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    harness = _MixinHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_network": "",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    harness._start_container("tb2-task:latest", logs_dir, task)

    assert "--network" not in calls[0]


def test_terminal_bench2_podman_start_container_rewrites_loopback_proxy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for key in (
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:17890")
    monkeypatch.setenv("HTTPS_PROXY", "http://localhost:17890")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "run":
            return subprocess.CompletedProcess(argv, 0, stdout="podman-cid\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    harness = _MixinHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    harness._start_container("tb2-task:latest", logs_dir, task)

    run_argv = calls[0]
    assert "--http-proxy=false" in run_argv
    assert "HTTP_PROXY=http://host.containers.internal:17890" in run_argv
    assert "HTTPS_PROXY=http://host.containers.internal:17890" in run_argv
    assert "NO_PROXY=127.0.0.1,localhost" in run_argv
    assert "HTTP_PROXY=http://127.0.0.1:17890" not in run_argv
    assert "HTTPS_PROXY=http://localhost:17890" not in run_argv


def test_terminal_bench2_podman_start_container_bypasses_provider_proxy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for key in (
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:17890")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://172.17.0.1:8011/v1")
    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "run":
            return subprocess.CompletedProcess(argv, 0, stdout="podman-cid\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    harness = _MixinHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    harness._start_container("tb2-task:latest", logs_dir, task)

    run_argv = calls[0]
    assert "HTTP_PROXY=http://host.containers.internal:17890" in run_argv
    assert "NO_PROXY=127.0.0.1,localhost,172.17.0.1" in run_argv


def test_terminal_bench2_podman_start_container_can_disable_proxy_forwarding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:17890")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "run":
            return subprocess.CompletedProcess(argv, 0, stdout="podman-cid\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    harness = _MixinHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "forward_proxy_env": False,
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    harness._start_container("tb2-task:latest", logs_dir, task)

    run_argv = calls[0]
    assert not any(arg.startswith("HTTP_PROXY=") for arg in run_argv)
    assert not any(arg.startswith("NO_PROXY=") for arg in run_argv)


def test_terminal_bench2_podman_start_recovers_container_after_timeout(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, timeout=kwargs.get("timeout"))
        if argv[1] == "inspect":
            payload = [{"Id": "recovered-cid", "State": {"Running": True, "Status": "running"}}]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    harness = _MixinHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })
    task = BenchmarkTask(
        task_id="tb2_fix-code-vulnerability",
        problem="demo",
        ground_truth="1",
        metadata={"sample_index": 0, "execution_id": "abc123456789"},
    )
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    cid = harness._start_container("tb2-task:latest", logs_dir, task)

    assert cid == "recovered-cid"
    run_argv = calls[0]
    assert "--name" in run_argv
    assert "alphadiana-tb2_fix-code-vulnerability-s0-abc123456789" in run_argv
    assert calls[1][1:3] == ["inspect", "alphadiana-tb2_fix-code-vulnerability-s0-abc123456789"]
    assert calls[2][:5] == ["podman", "exec", "--user", "0:0", "recovered-cid"]


def test_terminal_bench2_podman_image_exists_retries_transient_timeout() -> None:
    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, timeout=kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, 0, stdout="[]\n", stderr="")

    harness = _InContainerHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })

    assert harness._docker_image_exists("alexgshaw/configure-git-webserver:20251031") is True
    assert [call[1:3] for call in calls] == [["image", "inspect"], ["image", "inspect"]]


def test_terminal_bench2_podman_build_retries_transient_failure() -> None:
    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 125, stdout="", stderr="transient storage lock")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    harness = _InContainerHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })

    harness._docker_build_image(
        "alphadiana-tb2-runtime:demo",
        "FROM scratch\n",
        build_args={"BASE_IMAGE": "alexgshaw/demo:latest"},
    )

    assert [call[1] for call in calls] == ["build", "build"]
    assert "BASE_IMAGE=docker.io/alexgshaw/demo:latest" in calls[0]


def test_terminal_bench2_podman_exec_retries_transient_storage_error() -> None:
    calls: list[list[str]] = []
    transient = (
        "Error: open /tmp/alphadiana-podman-data/containers/storage/"
        "overlay-containers/demo/userdata/abc/exit/demo: no such file or directory"
    )

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 125, stdout="", stderr=transient)
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    harness = _InContainerHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })

    result = harness._docker_exec_capture("demo", "echo ok", timeout_sec=30, check=True)

    assert result.returncode == 0
    assert result.stdout == "ok\n"
    assert [call[1] for call in calls] == ["exec", "exec"]


def test_terminal_bench2_podman_copy_from_falls_back_to_exec_cat(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "cp":
            return subprocess.CompletedProcess(
                argv,
                125,
                stdout="",
                stderr='error determining run uid: user: unknown user error looking up user "root"',
            )
        if argv[1] == "exec" and "cat /tmp/session.jsonl" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, stdout='{"type":"session"}\n', stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    harness = _InContainerHarness()
    harness._setup_container_config({
        "container_engine": "podman",
        "podman_runtime": PodmanCLI(runner=fake_runner),
    })
    local_path = tmp_path / "session.jsonl"

    copied = harness._copy_file_from_container(
        "demo",
        remote_path="/tmp/session.jsonl",
        local_path=local_path,
    )

    assert copied is True
    assert local_path.read_text(encoding="utf-8") == '{"type":"session"}\n'
    assert [call[1] for call in calls] == ["cp", "exec"]


def test_terminal_bench2_db_wal_task_note_mentions_workspace_snapshot(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(
        task_id="tb2_db-wal-recovery",
        problem="recover sqlite wal",
        ground_truth="1",
        metadata={"task_dir": "/tmp/terminal-bench-2/db-wal-recovery"},
    )
    for prefix in ("app", "original"):
        snapshot_dir = tmp_path / "bootstrap" / prefix
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "main.db").write_text("db", encoding="utf-8")
        (snapshot_dir / "main.db-wal").write_text("wal", encoding="utf-8")

    note = harness._task_runtime_note(task, tmp_path)
    helper_paths = harness._write_helper_scripts(
        tmp_path,
        "demo-container",
        task,
        task_note=note,
    )

    assert "./bootstrap/app/main.db" in note
    assert "./bootstrap/app/main.db-wal" in note
    assert "./bootstrap/original/main.db" in note
    assert "./bootstrap/original/main.db-wal" in note
    assert "sqlite3 \"file:/app/main.db?mode=ro\"" in note
    assert helper_paths["task_hints"].exists()
    assert "Task-specific guidance:" in helper_paths["task_hints"].read_text(encoding="utf-8")
    assert "--- Task Hints ---" in helper_paths["task"].read_text(encoding="utf-8")


def test_terminal_bench2_native_agents_disable_manual_test_helper(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo task", ground_truth="1")

    helper_paths = harness._write_helper_scripts(
        tmp_path,
        "demo-container",
        task,
        prompt_text="demo prompt",
    )
    harness._disable_test_helper(helper_paths)

    helper_text = helper_paths["test"].read_text(encoding="utf-8")
    assert "tb2-test is disabled for native agents" in helper_text
    assert "harness will run verification" in helper_text


def test_terminal_bench2_incontainer_db_wal_task_note_mentions_live_paths() -> None:
    harness = _InContainerHarness()
    task = BenchmarkTask(
        task_id="tb2_db-wal-recovery",
        problem="recover sqlite wal",
        ground_truth="1",
        metadata={"task_dir": "/tmp/terminal-bench-2/db-wal-recovery"},
    )

    note = harness._build_incontainer_task_note(task)

    assert "/app/main.db" in note
    assert "/app/main.db-wal" in note
    assert "/app/recovered.json" in note
    assert "bootstrap" not in note


def test_terminal_bench2_incontainer_workspace_detection_prefers_app_from_root() -> None:
    harness = _InContainerHarness()

    with patch.object(
        harness,
        "_docker_exec_capture",
        return_value=LocalCommandResult(stdout="/\n", stderr="", returncode=0),
    ), patch.object(harness, "_docker_path_exists", return_value=True):
        workspace = harness._detect_container_workspace("demo-container")

    assert workspace == "/app"


def test_terminal_bench2_incontainer_zeroclaw_overlay_uses_direct_binary_copy() -> None:
    dockerfile = _InContainerHarness._build_runtime_overlay_dockerfile("zeroclaw")

    assert "COPY --from=runtime /usr/local/bin/zeroclaw /usr/local/bin/zeroclaw" in dockerfile
    assert "ld-linux-x86-64.so.2" not in dockerfile


def test_terminal_bench2_incontainer_builds_local_task_images_from_environment_dir(
    tmp_path: Path,
) -> None:
    harness = _InContainerHarness()
    task_dir = tmp_path / "adaptive-rejection-sampler"
    env_dir = task_dir / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_path = env_dir / "Dockerfile"
    dockerfile_path.write_text("FROM ubuntu:24.04\n", encoding="utf-8")
    task = BenchmarkTask(
        task_id="tb2_adaptive-rejection-sampler",
        problem="demo",
        ground_truth="1",
        metadata={
            "docker_image": "adaptive-rejection-sampler-r-min:local",
            "task_dir": str(task_dir),
            "build_timeout_sec": 321.0,
        },
    )

    pulled_images: list[str] = []
    local_builds: list[tuple[str, str, str]] = []
    overlay_builds: list[tuple[str, dict[str, str]]] = []

    def _fake_exists(image: str) -> bool:
        return image == "tmlrgroup/alphadiana:opencode"

    def _fake_local_build(build_task: BenchmarkTask, image: str) -> None:
        assert build_task is task
        local_builds.append((image, str(dockerfile_path), str(env_dir)))

    def _fake_overlay_build(image: str, dockerfile: str, *, build_args: dict[str, str]) -> None:
        overlay_builds.append((image, build_args))

    with patch.object(harness, "_docker_image_exists", side_effect=_fake_exists), patch.object(
        harness,
        "_docker_pull",
        side_effect=pulled_images.append,
    ), patch.object(
        harness,
        "_docker_build_local_task_image",
        side_effect=_fake_local_build,
    ), patch.object(
        harness,
        "_docker_build_image",
        side_effect=_fake_overlay_build,
    ):
        runtime_image, metadata = harness._prepare_runtime_image(
            task,
            agent_type="opencode",
            runtime_source_image="tmlrgroup/alphadiana:opencode",
        )

    assert runtime_image.startswith("alphadiana-tb2-runtime:opencode-")
    assert local_builds == [
        ("adaptive-rejection-sampler-r-min:local", str(dockerfile_path), str(env_dir))
    ]
    assert pulled_images == []
    assert overlay_builds
    assert overlay_builds[0][1]["BASE_IMAGE"] == "adaptive-rejection-sampler-r-min:local"
    assert metadata["base_image_built_locally"] is True


def test_terminal_bench2_openclaw_validation_requires_api_base() -> None:
    config = ExperimentConfig(
        run_id="tb2-openclaw",
        agent_name="terminal_bench2_openclaw",
        agent_version="1.0",
        agent_config={
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
        },
        benchmark_name="terminal_bench2",
        benchmark_config={"tasks_dir": "/tmp/tb2"},
        scorer_name="terminal_bench2",
    )

    errors = ConfigValidator().validate(config)

    assert any("terminal_bench2_openclaw" in error and "api_base" in error for error in errors)


def test_terminal_bench2_zeroclaw_validation_requires_api_base() -> None:
    config = ExperimentConfig(
        run_id="tb2-zeroclaw",
        agent_name="terminal_bench2_zeroclaw",
        agent_version="0.6.9",
        agent_config={},
        benchmark_name="terminal_bench2",
        benchmark_config={"tasks_dir": "/tmp/tb2"},
        scorer_name="terminal_bench2",
    )

    errors = ConfigValidator().validate(config)

    assert any("terminal_bench2_zeroclaw" in error and "api_base" in error for error in errors)


def test_terminal_bench2_zeroclaw_config_reads_openai_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL_NAME", "minimax-m2.5")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    yaml_path = tmp_path / "tb2_zeroclaw.yaml"
    yaml_path.write_text(
        """
agent:
  name: terminal_bench2_zeroclaw
  version: "0.6.9"
  config: {}
benchmark:
  name: terminal_bench2
  config:
    tasks_dir: /tmp/tb2
scorer:
  name: terminal_bench2
""".strip(),
        encoding="utf-8",
    )

    config = ExperimentConfig.from_yaml(str(yaml_path))

    assert config.agent_config["model"] == "minimax-m2.5"
    assert config.agent_config["model_name"] == "minimax-m2.5"
    assert config.agent_config["api_base"] == "http://localhost/v1"
    assert config.agent_config["api_key"] == "secret"


def test_terminal_bench2_openclaw_builds_local_cli_env(monkeypatch) -> None:
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    assert agent._runtime_source_image == PREBUILT_SANDBOX_IMAGE
    assert agent._verbose == "on"
    assert agent._min_context_window == 32768

    env = agent._build_env("/tmp/openclaw-home", "/tmp/openclaw-plugins")

    assert set(env) == {
        "CUSTOM_API_KEY",
        "HOME",
        "NO_PROXY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL_NAME",
        "OPENCLAW_BUNDLED_PLUGINS_DIR",
        "OPENCLAW_HOME",
        "OPENCLAW_UNDICI_STREAM_TIMEOUT_MS",
        "no_proxy",
    }
    assert env["HOME"] == "/tmp/openclaw-home"
    assert env["OPENCLAW_HOME"] == "/tmp/openclaw-home"
    assert env["OPENCLAW_BUNDLED_PLUGINS_DIR"] == "/tmp/openclaw-plugins"
    assert env["OPENAI_BASE_URL"] == "http://localhost/v1"
    assert env["OPENAI_MODEL_NAME"] == "minimax-m2.5"
    assert env["CUSTOM_API_KEY"] == "secret"
    assert env["OPENCLAW_UNDICI_STREAM_TIMEOUT_MS"] == "1800000"
    assert env["NO_PROXY"] == "localhost"
    assert env["no_proxy"] == "localhost"


def test_terminal_bench2_openclaw_onboard_uses_noninteractive_local_mode() -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "openclaw_bin": "openclaw",
    })

    cmd = agent._build_onboard_command("/app")
    assert cmd[:4] == ["openclaw", "onboard", "--non-interactive", "--flow"]
    assert "--mode" in cmd and "local" in cmd
    assert "--workspace" in cmd and "/app" in cmd
    assert "--custom-base-url" in cmd and "http://localhost/v1" in cmd
    assert "--custom-model-id" in cmd and "minimax-m2.5" in cmd
    assert "--skip-health" in cmd


def test_terminal_bench2_openclaw_agent_command_uses_local_json_mode(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "openclaw_bin": "openclaw",
        "thinking": "high",
        "verbose": "debug",
        "solver_timeout_sec": 321,
    })

    cmd = agent._build_agent_command()
    assert cmd[:2] == ["openclaw", "agent"]
    assert "--local" in cmd
    assert "--json" in cmd
    assert "--message" in cmd and '$(cat "$ALPHADIANA_PROMPT_FILE")' in cmd
    assert "--timeout" in cmd and "321" in cmd
    assert "--thinking" in cmd and "high" in cmd
    assert "--verbose" in cmd and "full" in cmd
    assert cmd[-2:] == ["--message", '$(cat "$ALPHADIANA_PROMPT_FILE")']


def test_terminal_bench2_openclaw_finds_latest_session_under_runtime_home() -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    with patch.object(
        agent,
        "_docker_exec_capture",
        return_value=LocalCommandResult(
            stdout="/tmp/openclaw/home/.openclaw/agents/main/sessions/newer.jsonl\n",
            stderr="",
            returncode=0,
        ),
    ) as mock_exec:
        session = agent._find_latest_remote_session_file(
            "demo-container",
            "/tmp/openclaw/home/.openclaw/agents",
        )

    assert session.endswith("newer.jsonl")
    assert "-printf '%T@ %p\\n'" in mock_exec.call_args.args[1]
    assert "tail -n 1" in mock_exec.call_args.args[1]


def test_terminal_bench2_openclaw_context_preflight_rejects_small_window(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "qwen/qwen3.5-27b",
        "min_context_window": 32768,
        "fail_on_small_context_window": True,
    })

    with patch.object(
        agent,
        "_resolve_context_window",
        return_value=(16000, "models_endpoint"),
    ):
        try:
            agent._write_context_preflight(tmp_path)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected low context window preflight to fail")

    assert "resolved=16000" in message
    preflight = (tmp_path / "openclaw_context_preflight.json").read_text(encoding="utf-8")
    assert '"resolved_context_window": 16000' in preflight


def test_terminal_bench2_openclaw_patches_runtime_max_tokens(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "Qwen/Qwen3.5-4B",
        "max_tokens": 131072,
    })
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps({
            "models": {
                "providers": {
                    "custom": {
                        "models": [
                            {
                                "id": "Qwen/Qwen3.5-4B",
                                "name": "Qwen/Qwen3.5-4B",
                                "contextWindow": 32768,
                                "maxTokens": 4096,
                            }
                        ]
                    }
                }
            }
        }),
        encoding="utf-8",
    )

    patch = agent._patch_runtime_model_contract(config_path, resolved_context_window=262144)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    model = payload["models"]["providers"]["custom"]["models"][0]

    assert patch["patched"] is True
    assert patch["max_tokens"] == 131072
    assert patch["timeout_seconds"] == 1800
    assert model["contextWindow"] == 262144
    assert model["maxTokens"] == 131072
    assert payload["agents"]["defaults"]["timeoutSeconds"] == 1800
    assert payload["tools"]["exec"]["timeoutSec"] == 1800


def test_terminal_bench2_opencode_writes_native_prompt_and_provider_config(tmp_path: Path) -> None:
    agent = TerminalBench2OpenCodeAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "model": "custom/minimax-m2.5",
        "opencode_bin": "opencode",
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": 4096,
        "capture_logprobs": True,
        "top_logprobs": 7,
    })
    assert agent._runtime_source_image == "tmlrgroup/alphadiana:opencode"
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    task_text, prompt_text = agent._build_incontainer_prompt(task)
    assert task_text == "demo"
    assert prompt_text.startswith(IN_CONTAINER_AGENT_PROMPT)
    assert "tb2-exec" not in prompt_text
    assert "do not assume they exist under `/app`" in prompt_text
    assert "Read `TASK.md` for the task details" not in prompt_text
    assert agent._opencode_command_prefix() == ["opencode"]
    run_cmd = agent._build_run_command(task_id="tb2_demo", container_workdir="/app")

    config_path = tmp_path / "xdg-config" / "opencode" / "opencode.json"
    agent._write_provider_config(config_path)
    payload = config_path.read_text(encoding="utf-8")
    config = json.loads(payload)
    options = config["provider"]["custom"]["options"]

    assert '"tool_call": true' in payload
    assert '"baseURL": "http://localhost/v1"' in payload
    assert '"timeout": 1800000' in payload
    assert options["temperature"] == 0.0
    assert options["top_p"] == 0.95
    assert options["max_tokens"] == 4096
    assert options["logprobs"] is True
    assert options["top_logprobs"] == 7
    assert '"model": "custom/minimax-m2.5"' in payload
    assert run_cmd[-1] == '$(cat "$ALPHADIANA_PROMPT_FILE")'
    assert run_cmd[0:4] == ["opencode", "run", "--format", "json"]
    assert "--model" in run_cmd and "custom/minimax-m2.5" in run_cmd


def test_terminal_bench2_zeroclaw_writes_native_prompt_and_config(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
    })

    assert agent._runtime_source_image == "zeroclaw-reasoning:0.6.9"
    assert agent._system_prompt == _ZEROCLAW_SYSTEM_PROMPT.strip()
    assert agent._workspace_only is False
    assert agent._security_sandbox_enabled is False

    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    _, prompt_text = agent._build_runtime_prompt(task)
    env = agent._build_tb2_env("/tmp/zeroclaw-home", "/tmp/zeroclaw-home/.zeroclaw")
    config_text = agent._build_config_toml()

    assert env["OPENAI_BASE_URL"] == "http://localhost/v1"
    assert env["OPENAI_MODEL_NAME"] == "minimax-m2.5"
    assert 'default_model = "minimax-m2.5"' in config_text
    assert 'default_provider = "custom:http://localhost/v1"' in config_text
    assert "[security.sandbox]" in config_text
    assert "enabled = false" in config_text
    assert 'runtime_trace_mode = "full"' in config_text
    assert "workspace_only = false" in config_text
    assert '"./tb2-exec"' not in config_text
    assert '"./tb2-copy-from"' not in config_text
    assert '"./tb2-copy-to"' not in config_text
    assert prompt_text.endswith("--- Task ---\ndemo\n")
    assert "You are solving a terminal-bench-2 task from inside the task container." in prompt_text
    assert "do not assume they exist under `/app`" in prompt_text
    assert "Read `TASK.md` for the task details" not in prompt_text
    assert "You are already inside the target task container." in _ZEROCLAW_SYSTEM_PROMPT
    assert "You are NOT inside the target task container." not in prompt_text


def test_terminal_bench2_zeroclaw_agent_command_uses_local_prompt_file(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
        "zeroclaw_bin": "zeroclaw",
        "solver_timeout_sec": 321,
    })
    cmd = agent._build_run_command({
        "workspace_dir": "/app",
        "zc_home_dir": "/tmp/zeroclaw-home/.zeroclaw",
        "session_state_path": "/tmp/zeroclaw-home/state/zeroclaw-session-state.json",
        "task_path": str(tmp_path / "task.txt"),
        "stdout_path": str(tmp_path / "zeroclaw_output.txt"),
        "stderr_path": str(tmp_path / "zeroclaw_stderr.log"),
    })

    assert "cd /app" in cmd
    assert f"prompt=$(cat {tmp_path / 'task.txt'})" in cmd
    assert "timeout 321 zeroclaw --config-dir /tmp/zeroclaw-home/.zeroclaw agent" in cmd
    assert "--session-state-file /tmp/zeroclaw-home/state/zeroclaw-session-state.json" in cmd
    assert '-m "$prompt"' in cmd
    assert str(tmp_path / "zeroclaw_output.txt") in cmd


def test_terminal_bench2_zeroclaw_provider_proxy_is_preserved(tmp_path: Path) -> None:
    proxy_instances = []

    class _FakeProxy:
        def __init__(self, upstream, top_logprobs, **kwargs):
            self.upstream = upstream.rstrip("/")
            self.top_logprobs = top_logprobs
            self.kwargs = kwargs
            self.proxy_url = "http://172.17.0.1:4567"
            self.stopped = False
            self._records_drained = False
            proxy_instances.append(self)

        def start(self):
            return None

        def stop(self):
            self.stopped = True

        def drain_request_summaries(self):
            return [
                {
                    "path": "/v1/chat/completions",
                    "model": "minimax-m2.5",
                    "message_roles_before": ["user", "system"],
                    "message_roles_after": ["system", "user"],
                    "system_indices_before": [1],
                    "system_indices_after": [0],
                    "normalized_system_messages": True,
                    "max_tokens": 4096,
                    "logprobs": None,
                    "top_logprobs": None,
                    "has_tools": True,
                    "tool_count": 1,
                }
            ]

        def drain_response_summaries(self):
            return [
                {
                    "path": "/v1/chat/completions",
                    "status_code": 200,
                    "model": "minimax-m2.5",
                    "choice_count": 1,
                    "finish_reasons": ["tool_calls"],
                    "content_lengths": [0],
                    "reasoning_lengths": [11],
                    "tool_call_counts": [1],
                    "empty_content_choice_count": 1,
                    "empty_reasoning_choice_count": 0,
                    "empty_content_and_reasoning_choice_count": 0,
                    "empty_content_and_reasoning_choice_indices": [],
                    "empty_content_reasoning_and_tool_choice_count": 0,
                    "empty_content_reasoning_and_tool_choice_indices": [],
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                }
            ]

        def drain_records(self):
            if self._records_drained:
                return []
            self._records_drained = True
            return [
                {
                    "token_index": 0,
                    "token": "answer",
                    "logprob": -0.1,
                    "top_logprobs": [{"token": "answer", "logprob": -0.1}],
                }
            ]

    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
        "solver_timeout_sec": 321,
        "max_tokens": 4096,
        "capture_logprobs": True,
        "top_logprobs": 7,
        "normalize_provider_system_messages": True,
        "provider_proxy_capture_request_summary": True,
        "provider_proxy_inject_logprobs": True,
        "provider_proxy_upstream_stream": True,
    })
    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-zeroclaw-proxy-")

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout="", stderr="", returncode=0),
                LocalCommandResult(stdout="", stderr="", returncode=0),
            ],
        ) as mock_exec, patch.object(
            agent,
            "_read_container_text",
            side_effect=lambda _container_id, remote_path, **_kwargs: (
                "assistant output" if remote_path.endswith("zeroclaw_output.txt") else ""
            ),
        ), patch.object(
            agent,
            "_run_verifier_and_read_reward",
            return_value=VerifierResult(
                test_output="verifier output",
                reward="0",
                status="ok",
                reward_path="/tmp/reward.txt",
                verifier_output_path="/tmp/verifier",
            ),
        ), patch(
            "alphadiana.agent.terminal_bench2_zeroclaw.LogprobCaptureProxy",
            _FakeProxy,
        ), patch(
            "alphadiana.agent.terminal_bench2_zeroclaw.secrets.token_urlsafe",
            lambda _: "proxy-token",
        ), patch.object(
            agent,
            "_cleanup_runtime",
            return_value=None,
        ):
            response = agent.solve(task)

        proxy = proxy_instances[0]
        assert proxy.upstream == "http://localhost/v1"
        assert proxy.kwargs["inject_logprobs"] is True
        assert proxy.top_logprobs == 7
        assert proxy.kwargs["normalize_system_messages"] is True
        assert proxy.kwargs["capture_request_summary"] is True
        assert proxy.kwargs["upstream_stream"] is True
        assert proxy.kwargs["request_overrides"]["max_tokens"] == 4096
        assert proxy.stopped is True

        config_text = (
            runtime.workdir / ".zeroclaw-home" / ".zeroclaw" / "config.toml"
        ).read_text(encoding="utf-8")
        assert 'default_provider = "custom:http://172.17.0.1:4567/v1"' in config_text
        exec_env = mock_exec.call_args_list[1].kwargs["env"]
        assert exec_env["OPENAI_BASE_URL"] == "http://172.17.0.1:4567/v1"
        assert exec_env["OPENAI_API_KEY"] == "proxy-token"
        assert exec_env["ZEROCLAW_PROVIDER"] == "custom:http://172.17.0.1:4567/v1"

        summary_text = response.workspace_file_contents[
            "/terminal_bench2/zeroclaw/provider_request_summary.jsonl"
        ]
        assert "demo task" not in summary_text
        assert '"normalized_system_messages": true' in summary_text
        assert response.artifact_manifest["files"]["provider_request_summary"] == (
            "provider_request_summary.jsonl"
        )
        response_summary_text = response.workspace_file_contents[
            "/terminal_bench2/zeroclaw/provider_response_summary.jsonl"
        ]
        assert "thinking" not in response_summary_text
        assert '"reasoning_lengths": [11]' in response_summary_text
        assert response.artifact_manifest["files"]["provider_response_summary"] == (
            "provider_response_summary.jsonl"
        )
        assert response.metadata["provider_proxy_request_count"] == 1
        assert response.metadata["provider_proxy_response_count"] == 1
        assert response.metadata["provider_proxy_normalized_request_count"] == 1
        assert response.metadata["provider_proxy_empty_content_response_count"] == 1
        assert response.metadata["provider_proxy_error_response_count"] == 0
        assert response.metadata["provider_proxy_upstream_stream"] is True
        assert response.metadata[
            "provider_proxy_empty_content_and_reasoning_response_count"
        ] == 0
        assert response.metadata[
            "provider_proxy_empty_content_reasoning_and_tool_response_count"
        ] == 0
        assert response.metadata["provider_proxy_logprob_record_count"] == 1
        assert response.metadata["logprobs_capture_status"] == "captured"
        assert response.metadata["logprob_capture_harness"] == "zeroclaw"
        assert response.metadata["logprob_source"] == "provider_proxy"
        assert response.token_entropy_stats["n_tokens"] == 1
        assert response.metadata["logprob_records"][0]["token"] == "answer"
    finally:
        runtime.cleanup()


def test_terminal_bench2_zeroclaw_solve_preserves_nonzero_solver_result(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    tempdir = tempfile.TemporaryDirectory(prefix="tb2-zeroclaw-solve-")
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path / "logs",
        workdir=Path(tempdir.name),
        helper_paths={
            "task": tmp_path / "TASK.md",
            "task_hints": tmp_path / "TASK_HINTS.md",
            "agents": tmp_path / "AGENTS.md",
            "prompt": tmp_path / "PROMPT.txt",
            "test": tmp_path / "tb2-test",
        },
        _tempdir=tempdir,
    )
    for path in runtime.helper_paths.values():
        path.write_text("", encoding="utf-8")

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout="", stderr="", returncode=0),
                LocalCommandResult(stdout="", stderr="controller stderr", returncode=1),
            ],
        ), patch.object(
            agent,
            "_read_container_text",
            side_effect=lambda _container_id, remote_path, **_kwargs: (
                "2026-04-20T06:17:59.498566Z  INFO zeroclaw::config::schema: Config loaded\n"
                "Loop detector summary\n"
                if remote_path.endswith("zeroclaw_output.txt")
                else (
                    "Error: Agent loop aborted by loop detector\n"
                    if remote_path.endswith("zeroclaw_stderr.log")
                    else (
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "role": "assistant",
                                    "content": "Loop detector summary",
                                    "thinking": "checked git state",
                                },
                            }
                        )
                        + "\n"
                        if remote_path.endswith("runtime-trace.jsonl")
                        else ""
                    )
                )
            ),
            ), patch.object(
                agent,
                "_run_verifier_and_read_reward",
                return_value=VerifierResult(
                    test_output="verifier output",
                    reward="0",
                    status="ok",
                    reward_path="/tmp/reward.txt",
                    verifier_output_path="/tmp/verifier",
                ),
            ), patch.object(
                agent,
                "_cleanup_runtime",
                return_value=None,
            ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "0"
    assert len(response.trajectory) >= 2
    assert response.raw_output == "Loop detector summary"
    assert response.metadata["raw_output_sanitized"] is True
    assert response.metadata["runtime_logs_dropped_from_output"]
    assert response.metadata["returncode"] == 1
    assert response.metadata["solver_error"] == "Error: Agent loop aborted by loop detector"
    assert response.metadata["container_workdir"] == "/app"
    assert response.response_json["runtime_trace_present"] is True
    assert response.artifact_manifest["files"]["response_stream"] == "runtime_trace.jsonl"
    assert response.artifact_manifest["files"]["stdout_log"] == "zeroclaw_output.txt"
    assert response.artifact_manifest["files"]["stderr_log"] == "zeroclaw_stderr.log"
    assert len(response.reasoning_trajectory) >= 1


def _make_incontainer_runtime(tmp_path: Path, task: BenchmarkTask, prefix: str) -> TerminalBench2RuntimeContext:
    tempdir = tempfile.TemporaryDirectory(prefix=prefix)
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path / "logs",
        workdir=Path(tempdir.name),
        helper_paths={
            "task": tmp_path / "TASK.md",
            "task_hints": tmp_path / "TASK_HINTS.md",
            "agents": tmp_path / "AGENTS.md",
            "prompt": tmp_path / "PROMPT.txt",
            "test": tmp_path / "tb2-test",
        },
        _tempdir=tempdir,
    )
    for path in runtime.helper_paths.values():
        path.write_text("", encoding="utf-8")
    return runtime


def test_terminal_bench2_cleanup_preserves_verifier_logs_dir(tmp_path: Path) -> None:
    harness = _MixinHarness()
    tempdir = tempfile.TemporaryDirectory(prefix="tb2-cleanup-")
    logs_dir = tmp_path / "logs"
    verifier_dir = logs_dir / "verifier"
    verifier_dir.mkdir(parents=True)
    reward_path = verifier_dir / "reward.txt"
    reward_path.write_text("0", encoding="utf-8")
    task = BenchmarkTask(task_id="tb2_demo", problem="demo task", ground_truth="1")
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=logs_dir,
        workdir=Path(tempdir.name),
        helper_paths={},
        _tempdir=tempdir,
    )

    with patch.object(harness, "_stop_container", return_value=None):
        harness._cleanup_runtime(runtime)

    assert reward_path.read_text(encoding="utf-8") == "0"
    assert not runtime.workdir.exists()


def test_terminal_bench2_zeroclaw_timeout_returncode_marks_timeout(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-zeroclaw-empty-")
    runtime_logs = (
        "2026-04-20T06:17:59.498566Z  INFO zeroclaw::config::schema: Config loaded\n"
        "2026-04-20T06:17:59.498566Z  INFO zeroclaw::agent::loop_: Memory initialized\n"
    )

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout="", stderr="", returncode=0),
                LocalCommandResult(stdout="", stderr="", returncode=124),
            ],
        ), patch.object(
            agent,
            "_read_container_text",
            side_effect=lambda _container_id, remote_path, **_kwargs: (
                runtime_logs if remote_path.endswith("zeroclaw_output.txt") else ""
            ),
        ), patch.object(
            agent,
            "_run_verifier_and_read_reward",
            return_value=VerifierResult(
                test_output="verifier output",
                reward="0",
                status="ok",
                reward_path="/tmp/reward.txt",
                verifier_output_path="/tmp/verifier",
            ),
        ), patch.object(
            agent,
            "_cleanup_runtime",
            return_value=None,
        ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "0"
    assert response.raw_output == ""
    assert response.finish_reason == "timeout"
    assert response.metadata["failure_reason"] == "timeout"
    assert response.metadata["timed_out"] is True
    assert response.metadata["zeroclaw_timeout_scored_zero"] is True
    assert response.metadata["zeroclaw_timeout_seconds"] == 1800
    assert "zeroclaw_empty_assistant_output" not in response.metadata
    assert response.metadata["trajectory_status"] == "abnormal"
    assert response.metadata["trajectory_failure_reason"] == "timeout"
    assert response.metadata["solver_error"] == "exit code 124"
    assert response.metadata["raw_output_sanitized"] is True
    assert response.metadata["runtime_logs_dropped_from_output"]
    assert response.artifact_manifest["files"]["response_stream"] == "zeroclaw_output.txt"
    assert infer_score_status({
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "finish_reason": response.finish_reason,
        "metadata": response.metadata,
        "score": 0.0,
        "correct": False,
    }) == "valid_scored"
    assert infer_score_status({
        "finish_reason": response.finish_reason,
        "metadata": response.metadata,
        "score": 0.0,
        "correct": False,
    }) == "valid_scored"


def test_terminal_bench2_zeroclaw_runtime_only_success_marks_agent_empty(
    tmp_path: Path,
) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-zeroclaw-empty-success-")
    runtime_logs = (
        "2026-04-20T06:17:59.498566Z  INFO zeroclaw::config::schema: Config loaded\n"
        "2026-04-20T06:17:59.498566Z  INFO zeroclaw::agent::loop_: Memory initialized\n"
    )

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout="", stderr="", returncode=0),
                LocalCommandResult(stdout="", stderr="", returncode=0),
            ],
        ), patch.object(
            agent,
            "_read_container_text",
            side_effect=lambda _container_id, remote_path, **_kwargs: (
                runtime_logs if remote_path.endswith("zeroclaw_output.txt") else ""
            ),
        ), patch.object(
            agent,
            "_run_verifier_and_read_reward",
            return_value=VerifierResult(
                test_output="verifier output",
                reward="0",
                status="ok",
                reward_path="/tmp/reward.txt",
                verifier_output_path="/tmp/verifier",
            ),
        ), patch.object(
            agent,
            "_cleanup_runtime",
            return_value=None,
        ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "0"
    assert response.raw_output == ""
    assert response.finish_reason == "agent_empty_output"
    assert response.metadata["failure_reason"] == "zeroclaw_empty_assistant_output"
    assert response.metadata["zeroclaw_empty_assistant_output"] is True
    assert response.metadata["timed_out"] is False
    assert response.metadata["trajectory_status"] == "abnormal"
    assert response.metadata["trajectory_failure_reason"] == "zeroclaw_empty_assistant_output"
    assert "solver_error" not in response.metadata
    assert response.metadata["raw_output_sanitized"] is True
    assert response.metadata["runtime_logs_dropped_from_output"]


def test_terminal_bench2_zeroclaw_provider_error_not_masked_by_verifier(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-zeroclaw-provider-")
    runtime_logs = (
        "2026-04-20T06:17:59.498566Z  INFO zeroclaw::config::schema: Config loaded\n"
    )
    provider_error = (
        "Error: All providers/models failed. Attempts:\n"
        "provider=custom:http://localhost/v1 model=minimax-m2.5 attempt 1/3: "
        "non_retryable; error=Custom API error (400 Bad Request): "
        "System message must be at the beginning."
    )

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout="", stderr="", returncode=0),
                LocalCommandResult(stdout="", stderr=provider_error, returncode=1),
            ],
        ), patch.object(
            agent,
            "_read_container_text",
            side_effect=lambda _container_id, remote_path, **_kwargs: (
                runtime_logs
                if remote_path.endswith("zeroclaw_output.txt")
                else (provider_error if remote_path.endswith("zeroclaw_stderr.log") else "")
            ),
        ), patch.object(
            agent,
            "_run_verifier_and_read_reward",
            return_value=VerifierResult(
                test_output="verifier output",
                reward="0",
                status="ok",
                reward_path="/tmp/reward.txt",
                verifier_output_path="/tmp/verifier",
            ),
        ), patch.object(
            agent,
            "_cleanup_runtime",
            return_value=None,
        ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "0"
    assert response.raw_output == provider_error
    assert response.metadata["failure_reason"] == "provider_error"
    assert response.metadata["zeroclaw_selected_classification"] == "provider_error"
    assert response.metadata["solver_error"] == provider_error
    assert infer_score_status({
        "metadata": response.metadata,
        "score": 0.0,
        "correct": False,
    }) == "provider_error"


def test_terminal_bench2_openclaw_solve_preserves_session_trace_and_summary(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-openclaw-solve-")
    session_jsonl = "\n".join([
        json.dumps({"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "demo task"}]}}),
        json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "inspect logs"},
                        {"type": "toolCall", "name": "bash", "arguments": {"cmd": "ls"}},
                        {"type": "text", "text": "fixed"},
                    ],
                },
            }
        ),
    ])

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_write_context_preflight",
            return_value={"resolved_context_window": 131072, "source": "models_endpoint"},
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_copy_file_from_container",
            side_effect=lambda _container_id, remote_path, local_path, **_kwargs: local_path.write_text(
                session_jsonl if str(remote_path).endswith("session.jsonl") else "{\"models\":{}}",
                encoding="utf-8",
            ),
        ), patch.object(
            agent,
            "_patch_runtime_model_contract",
            return_value={"patched": False, "resolved_context_window": 131072},
        ), patch.object(
            agent,
            "_find_latest_remote_session_file",
            return_value="/tmp/openclaw/home/.openclaw/agents/main/sessions/demo-session.jsonl",
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout='{"status":"ok"}\n', stderr="", returncode=0),
                LocalCommandResult(
                    stdout='{"choices":[{"message":{"role":"assistant","content":"fixed"}}]}\n',
                    stderr="",
                    returncode=0,
                ),
            ],
            ), patch.object(
                agent,
                "_run_verifier_and_read_reward",
                return_value=VerifierResult(
                    test_output="verifier output",
                    reward="1",
                    status="ok",
                    reward_path="/tmp/reward.txt",
                    verifier_output_path="/tmp/verifier",
                ),
            ), patch.object(
                agent,
                "_cleanup_runtime",
                return_value=None,
            ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "1"
    assert response.response_json["session_id"] == "demo-session"
    assert response.response_json["output_text"] == "fixed"
    assert response.artifact_manifest["files"]["session_trace"] == "session.jsonl"
    assert response.artifact_manifest["files"]["response_stream"] == "openclaw_stdout.log"
    assert len(response.reasoning_trajectory) >= 2
    assert any(
        step.get("thinking") == "inspect logs"
        for step in response.trajectory
        if step.get("role") == "assistant"
    )


def test_terminal_bench2_openclaw_timeout_returncode_marks_timeout(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-openclaw-timeout-")
    session_jsonl = "\n".join([
        json.dumps({
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "demo task"}],
            },
        }),
        json.dumps({
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "still working"}],
            },
        }),
    ])

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_write_context_preflight",
            return_value={"resolved_context_window": 131072, "source": "models_endpoint"},
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_copy_file_from_container",
            side_effect=lambda _container_id, remote_path, local_path, **_kwargs: local_path.write_text(
                session_jsonl if str(remote_path).endswith("session.jsonl") else "{\"models\":{}}",
                encoding="utf-8",
            ),
        ), patch.object(
            agent,
            "_patch_runtime_model_contract",
            return_value={"patched": False, "resolved_context_window": 131072},
        ), patch.object(
            agent,
            "_find_latest_remote_session_file",
            return_value="/tmp/openclaw/home/.openclaw/agents/main/sessions/demo-session.jsonl",
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout='{"status":"ok"}\n', stderr="", returncode=0),
                LocalCommandResult(
                    stdout="",
                    stderr="podman command timed out after 1830s",
                    returncode=124,
                ),
            ],
        ), patch.object(
            agent,
            "_run_verifier_and_read_reward",
            return_value=VerifierResult(
                test_output="verifier output",
                reward="0",
                status="ok",
                reward_path="/tmp/reward.txt",
                verifier_output_path="/tmp/verifier",
            ),
        ), patch.object(
            agent,
            "_cleanup_runtime",
            return_value=None,
        ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "0"
    assert response.finish_reason == "timeout"
    assert response.metadata["failure_reason"] == "timeout"
    assert response.metadata["openclaw_selected_classification"] == "timeout"
    assert response.metadata["openclaw_timeout_scored_zero"] is True
    assert response.metadata["openclaw_timeout_seconds"] == 1800
    assert response.metadata["timed_out"] is True
    assert response.metadata["trajectory_status"] == "abnormal"
    assert response.metadata["trajectory_failure_reason"] == "timeout"
    assert response.response_json["timed_out"] is True
    assert response.artifact_manifest["files"]["session_trace"] == "session.jsonl"
    assert "response_stream" not in response.artifact_manifest["files"]
    assert "openclaw_empty_assistant_output" not in response.metadata
    assert infer_score_status({
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "finish_reason": response.finish_reason,
        "metadata": response.metadata,
        "score": 0.0,
        "correct": False,
    }) == "valid_scored"


def test_terminal_bench2_openclaw_solve_marks_missing_session_trace_abnormal(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-openclaw-missing-session-")

    def copy_side_effect(_container_id: str, remote_path: str, local_path: Path, **_kwargs: object) -> bool:
        if str(remote_path).endswith("openclaw.json"):
            local_path.write_text("{\"models\":{}}", encoding="utf-8")
            return True
        return False

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_write_context_preflight",
            return_value={"resolved_context_window": 131072, "source": "models_endpoint"},
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_copy_file_from_container",
            side_effect=copy_side_effect,
        ), patch.object(
            agent,
            "_patch_runtime_model_contract",
            return_value={"patched": False, "resolved_context_window": 131072},
        ), patch.object(
            agent,
            "_find_latest_remote_session_file",
            return_value="/tmp/openclaw/home/.openclaw/agents/main/sessions/demo-session.jsonl",
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout='{"status":"ok"}\n', stderr="", returncode=0),
                LocalCommandResult(stdout='{"output_text":"partial"}\n', stderr="", returncode=0),
            ],
        ), patch.object(
            agent,
            "_run_verifier_and_read_reward",
            return_value=VerifierResult(
                test_output="verifier output",
                reward="1",
                status="ok",
                reward_path="/tmp/reward.txt",
                verifier_output_path="/tmp/verifier",
            ),
        ), patch.object(
            agent,
            "_cleanup_runtime",
            return_value=None,
        ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "1"
    assert response.metadata["trajectory_status"] == "abnormal"
    assert response.metadata["trajectory_failure_reason"].startswith("session_trace_missing_after_copy:")
    assert response.response_json["trajectory_error"].startswith("session_trace_missing_after_copy:")
    assert response.metadata["session_trace_source"] == "stdout_jsonl_fallback"
    assert response.artifact_manifest["files"]["session_trace"] == "session.jsonl"
    assert "/terminal_bench2/openclaw/session.jsonl" in response.workspace_file_contents
    assert response.raw_output.strip()


def test_terminal_bench2_opencode_solve_preserves_stream_and_session_trace(tmp_path: Path) -> None:
    agent = TerminalBench2OpenCodeAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "model": "custom/minimax-m2.5",
        "capture_logprobs": True,
        "top_logprobs": 7,
        "max_tokens": 4096,
        "logprob_proxy_advertise_host": "127.0.0.1",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-opencode-solve-")
    output_record = {
        "type": "assistant",
        "sessionID": "session-123",
        "message": {"role": "assistant", "content": "patched"},
    }
    session_record = {
        "type": "tool_call",
        "name": "bash",
        "content": "git status",
    }
    proxy_instances = []

    class _FakeProxy:
        def __init__(self, upstream, top_logprobs, **kwargs):
            self.upstream = upstream
            self.top_logprobs = top_logprobs
            self.kwargs = kwargs
            self.proxy_url = "http://127.0.0.1:19999"
            self.stopped = False
            proxy_instances.append(self)

        def start(self):
            pass

        def drain_records(self):
            return [
                {
                    "token_index": 0,
                    "token": "patched",
                    "logprob": -0.1,
                    "top_logprobs": [{"token": "patched", "logprob": -0.1}],
                }
            ]

        def stop(self):
            self.stopped = True

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_collect_session_trace",
            return_value=f"# file: /tmp/demo.jsonl\n{json.dumps(session_record)}\n",
        ), patch.object(
            agent,
            "_docker_exec_capture",
            return_value=LocalCommandResult(
                stdout=f"{json.dumps(output_record)}\n",
                stderr="stderr line",
                returncode=0,
            ),
        ) as mock_exec, patch.object(
            agent,
            "_run_verifier_and_read_reward",
            return_value=VerifierResult(
                test_output="verifier output",
                reward="0",
                status="ok",
                reward_path="/tmp/reward.txt",
                verifier_output_path="/tmp/verifier",
            ),
        ), patch(
            "alphadiana.agent.terminal_bench2_opencode.LogprobCaptureProxy",
            _FakeProxy,
        ), patch.object(
            agent,
            "_cleanup_runtime",
            return_value=None,
        ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "0"
    assert response.response_json["session_id"] == "session-123"
    assert response.response_json["num_session_records"] == 1
    assert response.artifact_manifest["files"]["response_stream"] == "opencode_stdout.log"
    assert response.artifact_manifest["files"]["session_trace"] == "opencode_session.jsonl"
    assert len(response.reasoning_trajectory) >= 1
    assert response.trajectory[-1]["content"] == "patched"
    assert response.metadata["logprobs_capture_status"] == "captured"
    assert response.metadata["logprob_capture_harness"] == "opencode"
    assert response.metadata["logprob_probe_proxy_count"] == 1
    assert response.metadata["logprob_source"] == "provider_proxy"
    assert response.metadata["session_trace_source"] == "opencode_session_files"
    assert response.token_entropy_stats["n_tokens"] == 1
    assert proxy_instances[0].top_logprobs == 7
    assert proxy_instances[0].kwargs["request_overrides"]["max_tokens"] == 4096
    assert proxy_instances[0].stopped is True
    exec_script = mock_exec.call_args.args[1]
    assert "timeout --kill-after=15s 1800s opencode run" in exec_script
    assert mock_exec.call_args.kwargs["timeout_sec"] == 1830


def test_terminal_bench2_opencode_session_trace_falls_back_to_stdout_jsonl() -> None:
    raw_output = "\n".join([
        "non-json line",
        json.dumps({"type": "text", "text": "hello"}),
        "",
        "{not json",
        json.dumps({"type": "step_finish", "reason": "stop"}),
    ])

    assert _jsonl_from_stdout(raw_output) == (
        f"{json.dumps({'type': 'text', 'text': 'hello'})}\n"
        f"{json.dumps({'type': 'step_finish', 'reason': 'stop'})}\n"
    )


def test_terminal_bench2_opencode_host_mode_keeps_configured_binary() -> None:
    agent = TerminalBench2OpenCodeAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "controller_mode": "host",
        "opencode_bin": "opencode --fast",
    })

    assert agent._opencode_command_prefix() == ["opencode", "--fast"]


def test_terminal_bench2_controller_process_wraps_command_in_docker_run(tmp_path: Path) -> None:
    harness = _MixinHarness()
    harness._setup_controller_config(
        {
            "controller_mode": "docker",
            "controller_image": "alphadiana/tb2-opencode-controller:latest",
        },
        default_mode="docker",
        default_image="alphadiana/tb2-opencode-controller:latest",
    )

    with patch.object(
        harness,
        "_run_local_process",
        return_value=LocalCommandResult(stdout="", stderr="", returncode=0),
    ) as mock_run:
        harness._run_controller_process(
            ["opencode", "run", "demo"],
            cwd=tmp_path,
            env={"OPENAI_API_KEY": "secret"},
            timeout_sec=30,
        )

    docker_cmd = mock_run.call_args.args[0]
    assert docker_cmd[:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path}:{tmp_path}" in docker_cmd
    assert "--entrypoint" in docker_cmd and "/bin/bash" in docker_cmd
    assert "alphadiana/tb2-opencode-controller:latest" in docker_cmd
    assert docker_cmd[-2:] == ["-lc", "opencode run demo"]


def test_terminal_bench2_controller_process_preserves_explicit_home_and_tmpdir(tmp_path: Path) -> None:
    harness = _MixinHarness()
    harness._setup_controller_config(
        {
            "controller_mode": "docker",
            "controller_image": "alphadiana/tb2-zeroclaw-controller:latest",
        },
        default_mode="docker",
        default_image="alphadiana/tb2-zeroclaw-controller:latest",
    )

    explicit_home = tmp_path / ".zeroclaw-home"
    explicit_tmp = tmp_path / ".zeroclaw-tmp"
    explicit_cache = tmp_path / ".zeroclaw-cache"

    with patch.object(
        harness,
        "_run_local_process",
        return_value=LocalCommandResult(stdout="", stderr="", returncode=0),
    ) as mock_run:
        harness._run_controller_process(
            ["zeroclaw", "agent", "-m", "demo"],
            cwd=tmp_path,
            env={
                "HOME": str(explicit_home),
                "TMPDIR": str(explicit_tmp),
                "XDG_CACHE_HOME": str(explicit_cache),
                "OPENAI_API_KEY": "secret",
            },
            timeout_sec=30,
        )

    docker_cmd = mock_run.call_args.args[0]
    assert "-e" in docker_cmd
    assert f"HOME={explicit_home}" in docker_cmd
    assert f"TMPDIR={explicit_tmp}" in docker_cmd
    assert f"TMP={explicit_tmp}" in docker_cmd
    assert f"TEMP={explicit_tmp}" in docker_cmd
    assert f"XDG_CACHE_HOME={explicit_cache}" in docker_cmd


def test_terminal_bench2_verifier_reuses_existing_reward(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    reward_dir = tmp_path / "verifier"
    reward_dir.mkdir(parents=True)
    (reward_dir / "reward.txt").write_text("1", encoding="utf-8")
    tempdir = tempfile.TemporaryDirectory(prefix="tb2-test-")
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path,
        workdir=Path(tempdir.name),
        helper_paths={},
        _tempdir=tempdir,
    )
    try:
        with patch.object(harness, "_run_tests") as mock_run:
            verifier_result = harness._run_verifier_and_read_reward(runtime, timeout_sec=30)
    finally:
        runtime.cleanup()

    assert verifier_result.reward == "1"
    assert verifier_result.status == "ok"
    assert "reused existing verifier reward.txt" in verifier_result.test_output
    mock_run.assert_not_called()


def test_terminal_bench2_verifier_waits_for_existing_run_to_finish(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    reward_dir = tmp_path / "verifier"
    reward_dir.mkdir(parents=True)
    reward_path = reward_dir / "reward.txt"
    tempdir = tempfile.TemporaryDirectory(prefix="tb2-test-")
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path,
        workdir=Path(tempdir.name),
        helper_paths={},
        _tempdir=tempdir,
    )

    def _write_reward_after_wait(_: float) -> None:
        reward_path.write_text("1", encoding="utf-8")

    try:
        with patch.object(harness, "_probe_running_verifier", return_value=(True, "ok")), patch.object(
            harness,
            "_run_tests",
        ) as mock_run, patch(
            "alphadiana.agent.terminal_bench2_common.time.sleep",
            side_effect=_write_reward_after_wait,
        ):
            verifier_result = harness._run_verifier_and_read_reward(runtime, timeout_sec=5)
    finally:
        runtime.cleanup()

    assert verifier_result.reward == "1"
    assert verifier_result.status == "ok"
    assert "existing verifier" in verifier_result.test_output
    mock_run.assert_not_called()


def test_terminal_bench2_verifier_skipped_duplicate_keeps_reward_missing(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    tempdir = tempfile.TemporaryDirectory(prefix="tb2-test-")
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path,
        workdir=Path(tempdir.name),
        helper_paths={},
        _tempdir=tempdir,
    )

    try:
        with patch.object(harness, "_probe_running_verifier", return_value=(True, "ok")), patch.object(
            harness,
            "_wait_for_existing_verifier",
            return_value=(
                "[skipped duplicate verifier run after waiting 5s for an existing verifier]",
                None,
                "skipped_duplicate",
            ),
        ), patch.object(harness, "_run_tests") as mock_run:
            verifier_result = harness._run_verifier_and_read_reward(runtime, timeout_sec=5)
    finally:
        runtime.cleanup()

    assert verifier_result.status == "skipped_duplicate"
    assert verifier_result.reward is None
    mock_run.assert_not_called()


def test_terminal_bench2_missing_reward_is_verifier_error_not_valid_scored() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "metadata": {
            "verifier_status": "missing_reward",
            "reward": "0",
        },
    }

    assert infer_score_status(record) == "verifier_error"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is False


def test_terminal_bench2_reward_zero_with_verifier_ok_is_valid_scored() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "metadata": {
            "verifier_status": "ok",
            "reward": "0",
        },
    }

    assert infer_score_status(record) == "valid_scored"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is True


def test_terminal_bench2_observed_agent_abnormality_with_reward_is_valid_scored() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "finish_reason": "agent_empty_output",
        "metadata": {
            "verifier_status": "ok",
            "reward": "0",
            "verifier_reward_observed": True,
            "failure_reason": "zeroclaw_empty_assistant_output",
            "zeroclaw_empty_assistant_output": True,
            "trajectory_status": "abnormal",
            "trajectory_failure_reason": "zeroclaw_empty_assistant_output",
        },
    }

    assert infer_score_status(record) == "valid_scored"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is True


def test_terminal_bench2_skipped_duplicate_with_observed_reward_is_valid_scored() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "metadata": {
            "verifier_status": "skipped_duplicate",
            "verifier_reward_observed": True,
            "reward": "0",
        },
    }

    assert infer_score_status(record) == "valid_scored"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is True


def test_terminal_bench2_skipped_duplicate_without_reward_is_verifier_error() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "metadata": {
            "verifier_status": "skipped_duplicate",
            "verifier_reward_observed": False,
            "reward": None,
        },
    }

    assert infer_score_status(record) == "verifier_error"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is False


def test_terminal_bench2_trajectory_error_does_not_invalidate_verifier_ok_result() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "metadata": {
            "verifier_status": "ok",
            "reward": "0",
            "trajectory_error": "Request was aborted",
        },
    }

    assert infer_score_status(record) == "valid_scored"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is True


def test_terminal_bench2_verifier_probe_skips_its_own_shell() -> None:
    harness = _MixinHarness()

    with patch("alphadiana.agent.terminal_bench2_common.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = ""
        assert harness._has_running_verifier("demo-container") is False

    probe = mock_run.call_args.args[0][-1]
    assert 'me="$$"' in probe
    assert '[ "${pid##*/}" = "$me" ] && continue' in probe


def test_terminal_bench2_verifier_bootstraps_uvx_shim() -> None:
    harness = _MixinHarness()

    with patch("alphadiana.agent.terminal_bench2_common.subprocess.run") as mock_run:
        harness._ensure_verifier_tooling("demo-container")

    bootstrap = mock_run.call_args.args[0][-1]
    assert '/root/.local/bin/env' in bootstrap
    assert '/usr/local/bin/uvx' in bootstrap
    assert '--break-system-packages' in bootstrap
    assert 'python3 -m pytest' in bootstrap


def test_terminal_bench2_docker_validation_requires_api_base() -> None:
    config = ExperimentConfig(
        run_id="tb2-direct",
        agent_name="terminal_bench2_docker",
        agent_version="1.0",
        agent_config={},
        benchmark_name="terminal_bench2",
        benchmark_config={"tasks_dir": str(Path("/tmp/tb2"))},
        scorer_name="terminal_bench2",
    )

    errors = ConfigValidator().validate(config)

    assert any("terminal_bench2_docker" in error and "api_base" in error for error in errors)
