from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from alphadiana.agent.opencode_container_runtime import (
    DEFAULT_INSTALL_OPENCODE_COMMAND,
    OPENCODE_CONFIG_PATH,
    OpenCodeContainerRuntimeManager,
)
from alphadiana.agent.openclaw_container_runtime import (
    LIBSIGNAL_MIRROR_REMOTE_ARCHIVE,
    OpenClawContainerRuntimeManager,
)
from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.cli import main
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult
from alphadiana.scorer.swe_bench import SWEBenchScorer
from alphadiana.sandbox.swebench_container import (
    FORWARDED_CONTAINER_ENV_KEYS,
    SWEBenchContainerSandbox,
)
from alphadiana.utils.swebench import harden_test_spec_repo_clone


def _make_task(task_id: str = "swe_demo") -> BenchmarkTask:
    return BenchmarkTask(
        task_id=task_id,
        problem="Fix the issue.",
        ground_truth="gold patch",
        metadata={
            "instance_id": task_id,
            "repo": "demo/repo",
            "base_commit": "abc123",
            "version": "1.0",
            "FAIL_TO_PASS": ["tests/test_demo.py::test_fix"],
            "PASS_TO_PASS": ["tests/test_demo.py::test_regression"],
            "test_patch": "",
        },
    )


def test_swebench_empty_patch_skips_harness(tmp_path, monkeypatch):
    scorer = SWEBenchScorer()
    scorer.setup({"log_dir": str(tmp_path / "eval_logs")})
    task = _make_task()
    response = AgentResponse(answer="   ")

    def fail_runtime():
        raise AssertionError("runtime should not be loaded for empty patches")

    monkeypatch.setattr(scorer, "_runtime_api", fail_runtime)

    result = scorer.score(task, response)

    assert result.correct is False
    assert result.score == 0.0
    assert "Empty patch" in result.rationale


def test_swebench_scoring_uses_report_and_persists_artifacts(tmp_path, monkeypatch):
    scorer = SWEBenchScorer()
    log_root = tmp_path / "eval_logs"
    scorer.setup({"log_dir": str(log_root)})
    task = _make_task()
    response = AgentResponse(answer="diff --git a/demo.py b/demo.py")
    run_eval_module = SimpleNamespace(RUN_EVALUATION_LOG_DIR=log_root)

    def fake_make_test_spec(instance, **_kwargs):
        return SimpleNamespace(
            instance_id=instance["instance_id"],
            repo=instance["repo"],
            version=instance["version"],
            FAIL_TO_PASS=json.loads(instance["FAIL_TO_PASS"]),
            PASS_TO_PASS=json.loads(instance["PASS_TO_PASS"]),
            instance_image_key="fake:image",
        )

    def fake_run_instance(test_spec, pred, *_args, run_id, **_kwargs):
        log_dir = log_root / run_id / pred["model_name_or_path"].replace("/", "__") / test_spec.instance_id
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "report.json").write_text(
            json.dumps({test_spec.instance_id: {"resolved": True}}),
            encoding="utf-8",
        )
        (log_dir / "run_instance.log").write_text("completed\n", encoding="utf-8")
        (log_dir / "test_output.txt").write_text("tests passed\n", encoding="utf-8")
        (log_dir / "patch.diff").write_text(pred["model_patch"], encoding="utf-8")
        return {"completed": True, "resolved": True}

    runtime = {
        "docker": SimpleNamespace(from_env=lambda: object()),
        "run_evaluation_module": run_eval_module,
        "build_env_images": lambda *_args, **_kwargs: (["env"], []),
        "build_instance_image": lambda *_args, **_kwargs: None,
        "make_test_spec": fake_make_test_spec,
        "run_instance": fake_run_instance,
        "KEY_INSTANCE_ID": "instance_id",
        "KEY_MODEL": "model_name_or_path",
        "KEY_PREDICTION": "model_patch",
        "LOG_REPORT": "report.json",
        "LOG_INSTANCE": "run_instance.log",
        "LOG_TEST_OUTPUT": "test_output.txt",
        "APPLY_PATCH_FAIL": "PATCH_FAIL",
        "RESET_FAILED": "RESET_FAILED",
        "TESTS_ERROR": "TESTS_ERROR",
        "TESTS_TIMEOUT": "TESTS_TIMEOUT",
    }
    monkeypatch.setattr(scorer, "_runtime_api", lambda: runtime)

    result = scorer.score(task, response)

    assert result.correct is True
    assert result.score == 1.0
    assert response.metadata["swe_bench_eval"]["resolved"] is True
    assert f"/swebench_eval/{task.task_id}/report.json" in response.workspace_file_contents
    assert f"/swebench_eval/{task.task_id}/run_instance.log" in response.workspace_file_contents


def test_swebench_patch_apply_failure_is_scored_not_raised(tmp_path, monkeypatch):
    scorer = SWEBenchScorer()
    log_root = tmp_path / "eval_logs"
    scorer.setup({"log_dir": str(log_root)})
    task = _make_task()
    response = AgentResponse(answer="not a valid patch")
    run_eval_module = SimpleNamespace(RUN_EVALUATION_LOG_DIR=log_root)

    def fake_make_test_spec(instance, **_kwargs):
        return SimpleNamespace(
            instance_id=instance["instance_id"],
            repo=instance["repo"],
            version=instance["version"],
            FAIL_TO_PASS=[],
            PASS_TO_PASS=[],
            instance_image_key="fake:image",
        )

    def fake_run_instance(test_spec, pred, *_args, run_id, **_kwargs):
        log_dir = log_root / run_id / pred["model_name_or_path"].replace("/", "__") / test_spec.instance_id
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "run_instance.log").write_text(
            ">>>>>> Patch Apply Failed\n",
            encoding="utf-8",
        )
        return {"completed": False, "resolved": False}

    runtime = {
        "docker": SimpleNamespace(from_env=lambda: object()),
        "run_evaluation_module": run_eval_module,
        "build_env_images": lambda *_args, **_kwargs: (["env"], []),
        "build_instance_image": lambda *_args, **_kwargs: None,
        "make_test_spec": fake_make_test_spec,
        "run_instance": fake_run_instance,
        "KEY_INSTANCE_ID": "instance_id",
        "KEY_MODEL": "model_name_or_path",
        "KEY_PREDICTION": "model_patch",
        "LOG_REPORT": "report.json",
        "LOG_INSTANCE": "run_instance.log",
        "LOG_TEST_OUTPUT": "test_output.txt",
        "APPLY_PATCH_FAIL": ">>>>>> Patch Apply Failed",
        "RESET_FAILED": "RESET_FAILED",
        "TESTS_ERROR": "TESTS_ERROR",
        "TESTS_TIMEOUT": "TESTS_TIMEOUT",
    }
    monkeypatch.setattr(scorer, "_runtime_api", lambda: runtime)

    result = scorer.score(task, response)

    assert result.correct is False
    assert result.score == 0.0
    assert "Patch did not apply" in result.rationale


def test_result_store_persists_score_metadata(tmp_path):
    store = ResultStore(
        output_dir=str(tmp_path),
        run_id="swebench-run",
        run_metadata={
            "run_id": "swebench-run",
            "agent_name": "opencode",
            "agent_version": "1.0",
            "benchmark_name": "swe_bench",
            "scorer_name": "swe_bench",
            "num_samples": 1,
        },
    )
    task = _make_task("swe_store")
    response = AgentResponse(answer="patch")
    score = ScoreResult(
        correct=False,
        score=0.0,
        expected="gold patch",
        predicted="unresolved",
        rationale="not resolved",
        metadata={"resolved": False, "status_marker": "PATCH_FAIL"},
    )

    store.append(task, response, score)

    jsonl_record = json.loads(Path(store.path).read_text(encoding="utf-8").strip())
    per_task_records = json.loads(
        (Path(tmp_path) / "swebench-run" / "tasks" / "swe_store.json").read_text(encoding="utf-8")
    )

    assert jsonl_record["score_metadata"] == {"resolved": False, "status_marker": "PATCH_FAIL"}
    assert per_task_records[0]["score_metadata"] == {"resolved": False, "status_marker": "PATCH_FAIL"}


def test_harden_test_spec_repo_clone_wraps_git_clone():
    test_spec = SimpleNamespace(
        repo_script_list=[
            "git clone -o origin --single-branch https://github.com/example/repo /testbed",
            "cd /testbed",
        ]
    )

    harden_test_spec_repo_clone(test_spec, clone_retries=4, retry_sleep_sec=7)

    rendered = "\n".join(test_spec.repo_script_list)
    assert "git config --global http.version HTTP/1.1" in rendered
    assert "cd /" in rendered
    assert "git clone attempt ${attempt}/4 failed" in rendered
    assert "sleep 7" in rendered
    assert "git -c http.version=HTTP/1.1 clone -o origin --single-branch https://github.com/example/repo /testbed && break" in rendered


def test_opencode_install_command_keeps_optional_binary_dependencies():
    assert "npm install -g opencode-ai@" in DEFAULT_INSTALL_OPENCODE_COMMAND
    assert "--omit=optional" not in DEFAULT_INSTALL_OPENCODE_COMMAND
    assert "npm install -g opencode@" not in DEFAULT_INSTALL_OPENCODE_COMMAND


def test_opencode_runtime_writes_permission_config():
    uploaded: dict[str, str] = {}

    class FakeSandbox:
        def upload(self, path, content):
            uploaded[path] = content.decode("utf-8")

    runtime = OpenCodeContainerRuntimeManager(
        {
            "api_base": "https://example.test/v1",
            "api_key": "secret",
            "permission": {"edit": "deny", "bash": "allow"},
        }
    )

    runtime._write_opencode_config(FakeSandbox(), model_name="minimax-m2.5")

    payload = json.loads(uploaded[OPENCODE_CONFIG_PATH])
    assert payload["permission"] == {"edit": "deny", "bash": "allow"}


def test_openclaw_install_command_uses_official_registry_and_git_prereqs():
    with open("alphadiana/agent/openclaw_container_runtime.py", encoding="utf-8") as f:
        source = f.read()

    assert 'NPM_REGISTRY = "https://registry.npmjs.org"' in source
    assert "command -v git" in source
    assert "git config --global http.version HTTP/1.1" in source
    assert "libsignal-node-bare" in source
    assert "git config --global --add url.'file://" in source


def test_swebench_container_skips_loopback_proxy_environment(monkeypatch, tmp_path):
    for key in FORWARDED_CONTAINER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8927")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8927")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    captured: dict[str, object] = {}

    class FakeContainer:
        id = "container-id"
        name = "container-name"
        attrs: dict = {
            "NetworkSettings": {
                "Ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32768"}]}
            }
        }

        def reload(self):
            pass

    class FakeContainers:
        def run(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return FakeContainer()

    sandbox = SWEBenchContainerSandbox()
    sandbox.setup({"log_dir": str(tmp_path / "logs"), "environment": {"CUSTOM_ENV": "1"}})

    fake_client = SimpleNamespace(containers=FakeContainers())
    fake_test_spec = SimpleNamespace(
        repo_directory="/testbed",
        docker_user="root",
        instance_image_key="fake:image",
    )
    monkeypatch.setattr(
        sandbox,
        "_runtime_api",
        lambda: {
            "docker": SimpleNamespace(from_env=lambda: fake_client),
            "DOCKER_WORKDIR": "/testbed",
            "DOCKER_USER": "root",
        },
    )
    monkeypatch.setattr(
        sandbox,
        "_build_test_spec",
        lambda _task: ({"repo": "demo/repo", "base_commit": "abc123"}, fake_test_spec),
    )
    monkeypatch.setattr(sandbox, "_build_image", lambda _task, _spec: tmp_path / "build")
    monkeypatch.setattr(sandbox, "_resolve_gateway_host_port", lambda _container, _port: "32768")

    sandbox.create_session(_make_task("astropy__astropy-12907"))

    env = captured["kwargs"]["environment"]
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["CUSTOM_ENV"] == "1"


def test_openclaw_runtime_uploads_git_mirror_once():
    uploaded: dict[str, bytes] = {}

    class FakeSandbox:
        sandbox_id = "sandbox-1"

        def upload(self, path, content):
            uploaded[path] = content

    runtime = OpenClawContainerRuntimeManager({"openclaw_config_path": "openclaw_deploy/openclaw_swe_bench.runtime.json"})
    runtime._build_git_dependency_mirror_archive = lambda: (LIBSIGNAL_MIRROR_REMOTE_ARCHIVE, b"mirror-bytes")

    sandbox = FakeSandbox()
    runtime._upload_git_dependency_mirrors(sandbox)
    runtime._upload_git_dependency_mirrors(sandbox)

    assert uploaded == {LIBSIGNAL_MIRROR_REMOTE_ARCHIVE: b"mirror-bytes"}


def test_swebench_runtime_imports_status_constants_from_constants_module():
    with open("alphadiana/scorer/swe_bench.py", encoding="utf-8") as f:
        source = f.read()

    assert "from swebench.harness.constants import (" in source
    assert "RESET_FAILED" in source
    assert "TESTS_ERROR" in source
    assert "TESTS_TIMEOUT" in source
    assert "from swebench.harness.run_evaluation import run_instance" in source


def test_cli_list_benchmarks_includes_swe_bench():
    runner = CliRunner()
    result = runner.invoke(main, ["list-benchmarks"])
    assert result.exit_code == 0
    assert "swe_bench" in result.output


def test_runner_bootstrap_imports_swe_bench_scorer():
    with open("alphadiana/runner/runner.py", encoding="utf-8") as f:
        source = f.read()
    assert "import alphadiana.scorer.swe_bench  # noqa: F401" in source
    assert "import alphadiana.benchmark.swe_bench  # noqa: F401" in source

    with open("alphadiana/cli.py", encoding="utf-8") as f:
        cli_source = f.read()
    assert "import alphadiana.benchmark.swe_bench  # noqa: F401" in cli_source
