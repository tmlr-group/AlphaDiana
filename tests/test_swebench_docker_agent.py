from __future__ import annotations

import json
import inspect
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import alphadiana.agent.swebench_docker  # noqa: F401
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.runner.runner import Runner
from alphadiana.agent.swebench_docker import SWEBenchDockerAgent


def _task(
    dockerhub_tag: str = "tag-123",
    *,
    problem: str = "Produce a patch that fixes the issue.",
    metadata: dict | None = None,
    ground_truth: dict | None = None,
) -> BenchmarkTask:
    task_metadata = {"dockerhub_tag": dockerhub_tag}
    if metadata:
        task_metadata.update(metadata)
    return BenchmarkTask(
        task_id="instance_demo",
        problem=problem,
        ground_truth=ground_truth or {},
        metadata=task_metadata,
    )


def _docker_ok(cmd: list[str], timeout=None, **kwargs):
    if cmd[:2] == ["docker", "create"]:
        return subprocess.CompletedProcess(cmd, 0, stdout="container123\n", stderr="")
    return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")


class TestSWEBenchDockerRegistry:
    def test_registry_returns_swebench_docker(self):
        assert AgentRegistry.get("swebench_docker") is SWEBenchDockerAgent

    def test_runner_setup_imports_swebench_docker(self):
        source = inspect.getsource(Runner.setup)
        assert "import alphadiana.agent.swebench_docker  # noqa: F401" in source

    def test_openclaw_runner_script_checks_gateway_readiness_and_events(self):
        script = Path("alphadiana/agent/swebench_assets/run_openclaw.sh").read_text(
            encoding="utf-8"
        )
        assert "OpenClaw gateway readiness timeout" in script
        assert "OpenClaw produced no SSE events" in script
        assert "OPENCLAW_BUNDLED_PLUGINS_DIR" in script
        assert "OPENCLAW_COMPLETION_MAX_TOKENS" in script
        assert "OPENCLAW_AGENT_ID" in script
        assert "OPENCLAW_TOOLS_PROFILE" in script
        assert "OPENCLAW_REQUIRE_PATCH" in script
        assert "OPENCLAW_SMOKE_MODEL_CANDIDATES" in script
        assert "OPENCLAW_PROMPT_PROFILE" in script
        assert "OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS" in script
        assert "OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT" in script
        assert "OPENCLAW_MAX_NO_EDIT_SECONDS" in script
        assert 'REQUEST_BODY_FILE="$WORKDIR/request.json"' in script
        assert "openclaw_request.json" in script
        assert "openclaw_tool_verdict.json" in script
        assert "openclaw_edit_convergence.json" in script
        assert "openclaw_attempt_matrix.json" in script
        assert "openclaw_selected_attempt.json" in script
        assert "openclaw_candidate_models.txt" in script
        assert "openclaw_prompt_contract.txt" in script
        assert "openclaw_prompt_profile.txt" in script
        assert "toolful_no_edit" in script
        assert "openclaw_session.jsonl" in script
        assert "urllib.request.Request(" in script
        assert 'Path(os.environ["REQUEST_BODY_FILE"]).read_bytes()' in script

    def test_opencode_runner_script_includes_preflight_and_startup_diagnostics(self):
        script = Path("alphadiana/agent/swebench_assets/run_opencode.sh").read_text(
            encoding="utf-8"
        )
        assert "OPENCODE_SMOKE_MODEL_NAME" in script
        assert "OPENCODE_SMOKE_MODEL_CANDIDATES" in script
        assert "OPENCODE_STRATEGY_SEQUENCE" in script
        assert "OPENCODE_REQUIRE_PATCH" in script
        assert "OPENCODE_PROMPT_PROFILE" in script
        assert "OPENCODE_AUTO_TARGET_HINTS" in script
        assert "OPENCODE_TARGET_FILE_HINTS" in script
        assert "OPENCODE_PRIMARY_TARGET_FILE" in script
        assert "OPENCODE_PROBLEM_STATEMENT_MAX_CHARS" in script
        assert "OPENCODE_PREFLIGHT_TIMEOUT_SEC" in script
        assert "OPENCODE_STARTUP_TIMEOUT_SEC" in script
        assert "OPENCODE_MAX_ACTIVE_NO_EDIT_SEC" in script
        assert "OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT" in script
        assert "OPENCODE_ACTIVITY_HEARTBEAT_SEC" in script
        assert "opencode_provider_preflight.txt" in script
        assert "opencode_startup_diagnostics.txt" in script
        assert "opencode_activity_summary.json" in script
        assert "opencode_no_edit_reason.txt" in script
        assert "opencode_prompt_contract.txt" in script
        assert "opencode_prompt_profile.txt" in script
        assert "opencode_candidate_models.txt" in script
        assert "opencode_target_file_hints.txt" in script
        assert "opencode_edit_bootstrap.txt" in script
        assert "opencode_edit_contract.txt" in script
        assert "opencode_attempt_matrix.json" in script
        assert "opencode_selected_attempt.json" in script
        assert "active_session_no_patch" in script
        assert "OpenCode provider preflight" in script
        assert "OpenCode startup diagnostics" in script

    def test_zeroclaw_runner_script_includes_patch_and_attempt_artifacts(self):
        script = Path("alphadiana/agent/swebench_assets/run_zeroclaw.sh").read_text(
            encoding="utf-8"
        )
        assert "ZEROCLAW_SMOKE_MODEL_NAME" in script
        assert "ZEROCLAW_SMOKE_MODEL_CANDIDATES" in script
        assert "ZEROCLAW_TIMEOUT_SEC" in script
        assert "ZEROCLAW_REQUIRE_PATCH" in script
        assert "ZEROCLAW_PROMPT_PROFILE" in script
        assert "ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS" in script
        assert "ZEROCLAW_WORKSPACE_ONLY" in script
        assert "ZEROCLAW_MAX_TOOL_ITERATIONS" in script
        assert "ZEROCLAW_MAX_ACTIONS_PER_HOUR" in script
        assert "ZEROCLAW_RUNTIME_TRACE_MODE" in script
        assert "ZEROCLAW_TEMPERATURE" in script
        assert "ZEROCLAW_PROVIDER_TIMEOUT_SECS" in script
        assert "ZEROCLAW_PROVIDER_MAX_TOKENS" in script
        assert "ZEROCLAW_REASONING_ENABLED" in script
        assert "ZEROCLAW_REASONING_EFFORT" in script
        assert "zeroclaw_candidate_models.txt" in script
        assert "zeroclaw_prompt_contract.txt" in script
        assert "zeroclaw_prompt_profile.txt" in script
        assert "zeroclaw_no_edit_reason.txt" in script
        assert "zeroclaw_attempt_matrix.json" in script
        assert "zeroclaw_selected_attempt.json" in script
        assert 'git -C "$REPO_ROOT" diff --binary >"$ZEROCLAW_PATCH_PATH"' in script
        assert "active_session_no_patch" in script
        assert "untracked_only_changes" in script


class TestSWEBenchDockerImageResolution:
    def test_resolve_image_uses_dockerhub_tag(self):
        agent = SWEBenchDockerAgent()
        agent.setup({"agent_type": "direct_llm"})

        assert agent._resolve_image(_task()) == "jefzda/sweap-images:tag-123"

    def test_resolve_image_honors_explicit_override(self):
        agent = SWEBenchDockerAgent()
        agent.setup({"agent_type": "direct_llm", "image": "custom/image:latest"})

        assert agent._resolve_image(_task()) == "custom/image:latest"


class TestSWEBenchDockerLifecycle:
    def test_lifecycle_runs_pull_create_start_stop_rm(self, monkeypatch):
        commands: list[list[str]] = []

        def fake_run(cmd, timeout=None, **kwargs):
            commands.append(cmd)
            return _docker_ok(cmd, timeout=timeout, **kwargs)

        monkeypatch.setattr("alphadiana.agent.swebench_docker._run", fake_run)

        agent = SWEBenchDockerAgent()
        agent.setup({"agent_type": "direct_llm", "model": "m", "api_base": "http://x/v1", "api_key": "k"})
        monkeypatch.setattr(agent, "_docker_image_exists", lambda image: False)
        monkeypatch.setattr(
            agent,
            "_solve_direct_llm",
            lambda task, image, container_id, start_time: SimpleNamespace(answer="diff --git a/x b/x"),
        )

        response = agent.solve(_task())

        assert response.answer == "diff --git a/x b/x"
        docker_actions = [cmd[1] for cmd in commands if cmd[:2] != ["docker", "image"]]
        assert docker_actions == ["pull", "create", "start", "stop", "rm"]

    def test_cleanup_still_runs_after_failure(self, monkeypatch):
        commands: list[list[str]] = []

        def fake_run(cmd, timeout=None, **kwargs):
            commands.append(cmd)
            return _docker_ok(cmd, timeout=timeout, **kwargs)

        def boom(task, image, container_id, start_time):
            raise RuntimeError("boom")

        monkeypatch.setattr("alphadiana.agent.swebench_docker._run", fake_run)

        agent = SWEBenchDockerAgent()
        agent.setup({"agent_type": "direct_llm", "model": "m", "api_base": "http://x/v1", "api_key": "k"})
        monkeypatch.setattr(agent, "_docker_image_exists", lambda image: False)
        monkeypatch.setattr(agent, "_solve_direct_llm", boom)

        with pytest.raises(RuntimeError, match="boom"):
            agent.solve(_task())

        docker_actions = [cmd[1] for cmd in commands if cmd[:2] != ["docker", "image"]]
        assert docker_actions == ["pull", "create", "start", "stop", "rm"]


class TestSWEBenchDockerDirectLLM:
    def test_direct_llm_returns_patch_string_and_preserves_response_json(self, monkeypatch):
        commands: list[list[str]] = []

        def fake_run(cmd, timeout=None, **kwargs):
            commands.append(cmd)
            return _docker_ok(cmd, timeout=timeout, **kwargs)

        monkeypatch.setattr("alphadiana.agent.swebench_docker._run", fake_run)

        response = MagicMock()
        response.choices = [
            SimpleNamespace(
                message=SimpleNamespace(
                    content="```diff\ndiff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n```",
                    model_extra={},
                ),
                finish_reason="stop",
            )
        ]
        response.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        response.model_dump.return_value = {"id": "resp-1"}

        client = MagicMock()
        client.chat.completions.create.return_value = response

        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "direct_llm",
            "model": "demo-model",
            "api_base": "http://localhost/v1",
            "api_key": "demo-key",
            "stream": False,
            "max_tokens": 1024,
        })
        agent._client = client

        response = agent.solve(_task())

        assert response.answer.startswith("diff --git")
        assert response.metadata["patch_format"] == "git_diff"
        assert response.metadata["dockerhub_tag"] == "tag-123"
        assert response.response_json == {"id": "resp-1"}
        assert response.raw_output.startswith("```diff")
        assert response.request_messages[1]["content"] == "Produce a patch that fixes the issue."
        assert all(cmd[1] != "exec" for cmd in commands), "docker exec should not be used in direct_llm mode"


def _write_artifacts(dst: str | Path, files: dict[str, str]) -> None:
    dst_path = Path(dst)
    dst_path.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (dst_path / name).write_text(content, encoding="utf-8")


class TestSWEBenchDockerInContainerModes:
    def test_copy_attempt_files_preserves_existing_aggregate_artifacts(self, tmp_path):
        source_dir = tmp_path / "source"
        target_dir = tmp_path / "target"
        _write_artifacts(
            source_dir,
            {
                "patch.diff": "diff --git a/x b/x\n",
                "opencode_attempt_matrix.json": json.dumps({"attempts": ["single"]}),
                "opencode_selected_attempt.json": json.dumps({"attempt_index": 1}),
            },
        )
        _write_artifacts(
            target_dir,
            {
                "opencode_attempt_matrix.json": json.dumps({"attempts": ["aggregate"]}),
                "opencode_selected_attempt.json": json.dumps({"attempt_index": 2}),
            },
        )

        SWEBenchDockerAgent._copy_attempt_files(
            source_dir,
            target_dir,
            exclude_names={
                "opencode_attempt_matrix.json",
                "opencode_selected_attempt.json",
            },
        )

        assert (target_dir / "patch.diff").read_text(encoding="utf-8").startswith("diff --git")
        assert json.loads(
            (target_dir / "opencode_attempt_matrix.json").read_text(encoding="utf-8")
        ) == {"attempts": ["aggregate"]}
        assert json.loads(
            (target_dir / "opencode_selected_attempt.json").read_text(encoding="utf-8")
        ) == {"attempt_index": 2}

    def test_prompt_parts_normalize_stringified_metadata_lists(self):
        agent = SWEBenchDockerAgent()
        agent.setup({"agent_type": "openclaw"})

        parts = agent._build_container_prompt_parts(
            _task(
                metadata={
                    "selected_test_files_to_run": '["tests/test_a.py", "tests/test_b.py"]',
                    "fail_to_pass": "['case_a', 'case_b']",
                    "pass_to_pass": "baseline_case",
                }
            ),
            "/workspace/repo",
        )

        assert parts["relevant_tests"] == ["tests/test_a.py", "tests/test_b.py"]
        assert parts["fail_to_pass"] == ["case_a", "case_b"]
        assert parts["pass_to_pass"] == ["baseline_case"]

    def test_prepare_runtime_image_builds_openclaw_overlay_from_official_source(self, monkeypatch):
        agent = SWEBenchDockerAgent()
        agent.setup({"agent_type": "openclaw", "request_timeout": 120})
        pulls: list[str] = []
        builds: list[tuple[str, str, dict[str, str] | None]] = []

        monkeypatch.setattr(agent, "_docker_image_exists", lambda image: False)
        monkeypatch.setattr(agent, "_docker_pull", lambda image: pulls.append(image))
        monkeypatch.setattr(
            agent,
            "_docker_build_image",
            lambda image, dockerfile, *, build_args=None: builds.append(
                (image, dockerfile, build_args)
            ),
        )

        runtime_image, metadata = agent._prepare_runtime_image("jefzda/sweap-images:tag-123")

        assert runtime_image.startswith("alphadiana-swebench-runtime:openclaw-")
        assert pulls == ["jefzda/sweap-images:tag-123", "tmlrgroup/alphadiana:v1"]
        assert metadata["runtime_source_image"] == "tmlrgroup/alphadiana:v1"
        assert metadata["runtime_injected"] is True
        assert metadata["runtime_image_built"] is True
        assert len(builds) == 1
        assert builds[0][2] == {
            "BASE_IMAGE": "jefzda/sweap-images:tag-123",
            "RUNTIME_IMAGE": "tmlrgroup/alphadiana:v1",
        }
        assert "COPY --from=runtime /app /opt/openclaw" in builds[0][1]
        assert "COPY --from=runtime /usr/local/bin/node /usr/local/bin/node" in builds[0][1]

    def test_prepare_runtime_image_builds_opencode_overlay_from_official_source(self, monkeypatch):
        agent = SWEBenchDockerAgent()
        agent.setup({"agent_type": "opencode", "request_timeout": 120})
        pulls: list[str] = []
        builds: list[tuple[str, str, dict[str, str] | None]] = []

        monkeypatch.setattr(agent, "_docker_image_exists", lambda image: False)
        monkeypatch.setattr(agent, "_docker_pull", lambda image: pulls.append(image))
        monkeypatch.setattr(
            agent,
            "_docker_build_image",
            lambda image, dockerfile, *, build_args=None: builds.append(
                (image, dockerfile, build_args)
            ),
        )

        runtime_image, metadata = agent._prepare_runtime_image("jefzda/sweap-images:tag-123")

        assert runtime_image.startswith("alphadiana-swebench-runtime:opencode-")
        assert pulls == ["jefzda/sweap-images:tag-123", "tmlrgroup/alphadiana:opencode"]
        assert metadata["runtime_source_image"] == "tmlrgroup/alphadiana:opencode"
        assert metadata["runtime_injected"] is True
        assert metadata["runtime_image_built"] is True
        assert len(builds) == 1
        assert builds[0][2] == {
            "BASE_IMAGE": "jefzda/sweap-images:tag-123",
            "RUNTIME_IMAGE": "tmlrgroup/alphadiana:opencode",
        }
        assert "COPY --from=runtime /usr/bin/node /usr/bin/node" in builds[0][1]
        assert "COPY --from=runtime /usr/lib/node_modules/opencode-ai /usr/lib/node_modules/opencode-ai" in builds[0][1]

    def test_prepare_runtime_image_builds_zeroclaw_overlay_from_official_source(self, monkeypatch):
        agent = SWEBenchDockerAgent()
        agent.setup({"agent_type": "zeroclaw", "request_timeout": 120})
        pulls: list[str] = []
        builds: list[tuple[str, str, dict[str, str] | None]] = []

        monkeypatch.setattr(agent, "_docker_image_exists", lambda image: False)
        monkeypatch.setattr(agent, "_docker_pull", lambda image: pulls.append(image))
        monkeypatch.setattr(
            agent,
            "_docker_build_image",
            lambda image, dockerfile, *, build_args=None: builds.append(
                (image, dockerfile, build_args)
            ),
        )

        runtime_image, metadata = agent._prepare_runtime_image("jefzda/sweap-images:tag-123")

        assert runtime_image.startswith("alphadiana-swebench-runtime:zeroclaw-")
        assert pulls == ["jefzda/sweap-images:tag-123", "zeroclaw-reasoning:0.6.9"]
        assert metadata["runtime_source_image"] == "zeroclaw-reasoning:0.6.9"
        assert metadata["runtime_injected"] is True
        assert metadata["runtime_image_built"] is True
        assert len(builds) == 1
        assert builds[0][2] == {
            "BASE_IMAGE": "jefzda/sweap-images:tag-123",
            "RUNTIME_IMAGE": "zeroclaw-reasoning:0.6.9",
        }
        assert "COPY --from=runtime /usr/local/bin/zeroclaw /opt/zeroclaw-runtime/bin/zeroclaw" in builds[0][1]
        assert "COPY --from=runtime /lib/x86_64-linux-gnu /opt/zeroclaw-runtime/lib/x86_64-linux-gnu" in builds[0][1]
        assert 'exec "$LIB64/ld-linux-x86-64.so.2"' in builds[0][1]
        assert "libssl3" not in builds[0][1]

    def test_openclaw_mode_stages_assets_and_returns_patch(self, monkeypatch, tmp_path):
        staged_paths: list[str] = []
        docker_exec_calls: list[list[str]] = []
        exec_envs: list[dict[str, str]] = []

        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "openclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENCLAW_SMOKE_MODEL_CANDIDATES": "demo-model,fallback-model",
                "OPENCLAW_PROMPT_PROFILE": "edit_first",
                "OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS": "12000",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
                "OPENCLAW_CONTEXT_WINDOW": "4096",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:openclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": True,
                    "runtime_source_image": "tmlrgroup/alphadiana:v1",
                },
            ),
        )

        def fake_stage(container_id, *, local_path, remote_path):
            staged_paths.append(remote_path)

        def fake_exec(
            container_id,
            args,
            *,
            timeout=None,
            check=True,
            env=None,
            workdir=None,
        ):
            docker_exec_calls.append(args)
            if env is not None:
                exec_envs.append(env)
            return subprocess.CompletedProcess(args, 0, stdout="runner ok\n", stderr="")

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "patch.diff": "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
                    "openclaw_response.txt": "Applied fix",
                    "gateway.log": "gateway ready\n",
                    "openclaw_output.jsonl": json.dumps({"choices": [{"delta": {"content": "Applied fix"}}]}) + "\n",
                    "openclaw_runner_stdout.log": "runner ok\n",
                    "openclaw_runner_stderr.log": "",
                    "openclaw_request.json": json.dumps(
                        {
                            "headers": {"x-openclaw-agent-id": "main"},
                            "body": {"model": "openclaw:main"},
                            "openclaw_require_patch": True,
                        }
                    ),
                    "openclaw_tool_verdict.json": json.dumps(
                        {
                            "classification": "toolful",
                            "reason": "patch.diff is non-empty",
                        }
                    ),
                    "openclaw_edit_convergence.json": json.dumps(
                        {
                            "classification": "patch_created",
                            "reason": "patch.diff is non-empty",
                            "prompt_contract_path": "openclaw_prompt_contract.txt",
                        }
                    ),
                    "openclaw_trajectory_summary.json": json.dumps(
                        {
                            "trajectory_event_count": 1,
                            "non_config_event_count": 1,
                        }
                    ),
                    "openclaw_session.jsonl": json.dumps(
                        {
                            "type": "message",
                            "message": {
                                "role": "assistant",
                                "content": [{"type": "toolCall", "name": "read"}],
                            },
                        }
                    )
                    + "\n",
                    "trajectory.jsonl": "{\"event\": \"step\"}\n",
                    "git_status_before.txt": "",
                    "git_status_after.txt": " M app.py\n",
                    "openclaw_candidate_models.txt": "demo-model\nfallback-model\n",
                    "openclaw_prompt_profile.txt": (
                        "prompt_profile: edit_first\n"
                        "problem_statement_max_chars: 12000\n"
                        "resolved_model_alias: demo-model\n"
                    ),
                    "openclaw_prompt_contract.txt": (
                        "OpenClaw execution contract:\n"
                        "- Leave a non-empty git diff in the repository before stopping.\n"
                    ),
                    "repo_root.txt": "/workspace/repo\n",
                },
            )

        monkeypatch.setattr(agent, "_stage_file_into_container", fake_stage)
        monkeypatch.setattr(agent, "_docker_exec", fake_exec)
        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        response = agent.solve(_task())

        assert response.answer.startswith("diff --git")
        assert response.metadata["agent_type"] == "openclaw"
        assert response.metadata["repo_root"] == "/workspace/repo"
        assert response.metadata["runtime_injected"] is True
        assert response.metadata["runtime_source_image"] == "tmlrgroup/alphadiana:v1"
        assert response.metadata["bootstrap_needed"] is True
        assert any(path.endswith("/prompt.txt") for path in staged_paths)
        assert any(path.endswith("/run_openclaw.sh") for path in staged_paths)
        assert any(path.endswith("/openclaw.json.template") for path in staged_paths)
        assert any("run_openclaw.sh" in " ".join(call) for call in docker_exec_calls)
        assert any(env.get("OPENCLAW_COMPLETION_MAX_TOKENS") for env in exec_envs)
        assert any(env.get("OPENCLAW_AGENT_ID") == "main" for env in exec_envs)
        assert any(env.get("OPENCLAW_TOOLS_PROFILE") == "coding" for env in exec_envs)
        assert any(env.get("OPENCLAW_REQUIRE_PATCH") == "1" for env in exec_envs)
        assert "/swebench_agent/openclaw/patch.diff" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/prompt_budget.json" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_request.json" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_tool_verdict.json" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_edit_convergence.json" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_attempt_matrix.json" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_selected_attempt.json" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_candidate_models.txt" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_prompt_profile.txt" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_session.jsonl" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_prompt_contract.txt" in response.workspace_file_contents
        assert "/swebench_agent/openclaw/openclaw_runner_stdout.log" in response.workspace_file_contents
        prompt_budget = json.loads(
            response.workspace_file_contents["/swebench_agent/openclaw/prompt_budget.json"]
        )
        assert prompt_budget["completion_cap"] >= 512
        assert prompt_budget["needs_larger_context_model"] is False
        assert response.metadata["openclaw_request_path"].endswith("openclaw_request.json")
        assert response.metadata["openclaw_tool_verdict_path"].endswith(
            "openclaw_tool_verdict.json"
        )
        assert response.metadata["openclaw_edit_convergence_path"].endswith(
            "openclaw_edit_convergence.json"
        )
        assert response.metadata["openclaw_attempt_matrix_path"].endswith(
            "openclaw_attempt_matrix.json"
        )
        assert response.metadata["openclaw_selected_attempt_path"].endswith(
            "openclaw_selected_attempt.json"
        )
        assert response.metadata["openclaw_candidate_models_path"].endswith(
            "openclaw_candidate_models.txt"
        )
        assert response.metadata["openclaw_session_path"].endswith("openclaw_session.jsonl")
        assert response.metadata["openclaw_prompt_contract_path"].endswith(
            "openclaw_prompt_contract.txt"
        )
        assert response.metadata["openclaw_prompt_profile_path"].endswith(
            "openclaw_prompt_profile.txt"
        )
        assert response.metadata["openclaw_selected_model_alias"] == "demo-model"
        assert response.response_json["events"][0]["choices"][0]["delta"]["content"] == "Applied fix"

    def test_openclaw_mode_fails_preflight_when_context_is_too_small(self, monkeypatch, tmp_path):
        staged_paths: list[str] = []
        docker_exec_calls: list[list[str]] = []

        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "openclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
                "OPENCLAW_CONTEXT_WINDOW": "500",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:openclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:v1",
                },
            ),
        )
        monkeypatch.setattr(
            agent,
            "_stage_file_into_container",
            lambda *args, **kwargs: staged_paths.append(kwargs.get("remote_path", "")),
        )

        def fake_exec(
            container_id,
            args,
            *,
            timeout=None,
            check=True,
            env=None,
            workdir=None,
        ):
            docker_exec_calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(agent, "_docker_exec", fake_exec)

        huge_problem = "Investigate and fix the following issue.\n" + ("very long context\n" * 500)
        task = _task(
            problem=huge_problem,
            metadata={
                "selected_test_files_to_run": [f"tests/test_{i}.py" for i in range(50)],
                "fail_to_pass": [f"case_{i}" for i in range(50)],
                "pass_to_pass": [f"baseline_{i}" for i in range(50)],
            },
        )

        with pytest.raises(RuntimeError, match="OPENCLAW_SMOKE_MODEL_NAME"):
            agent.solve(task)

        artifacts_dir = next(tmp_path.glob("instance_demo/sample_0/*"))
        prompt_budget = json.loads((artifacts_dir / "prompt_budget.json").read_text(encoding="utf-8"))
        assert prompt_budget["needs_larger_context_model"] is True
        assert "edit_first_removed_pass_to_pass" in prompt_budget["compactions_applied"]
        assert not staged_paths
        assert not docker_exec_calls

    def test_openclaw_mode_copies_gateway_artifacts_on_runner_failure(self, monkeypatch, tmp_path):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "openclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
                "OPENCLAW_CONTEXT_WINDOW": "4096",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:openclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:v1",
                },
            ),
        )
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)

        def fake_exec(
            container_id,
            args,
            *,
            timeout=None,
            check=True,
            env=None,
            workdir=None,
        ):
            command = " ".join(args)
            if "mkdir -p" in command:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="runner failed\n",
                stderr="curl: (7) Failed to connect\n",
            )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "gateway.log": "curl: (7) Failed to connect to 127.0.0.1\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_exec", fake_exec)
        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(RuntimeError, match="OpenClaw run did not produce openclaw_output.jsonl"):
            agent.solve(_task())

        artifacts_dir = next(tmp_path.glob("instance_demo/sample_0/*"))
        assert (artifacts_dir / "gateway.log").read_text(encoding="utf-8").startswith("curl: (7)")
        assert (artifacts_dir / "openclaw_runner_stdout.log").read_text(encoding="utf-8") == "runner failed\n"
        assert (artifacts_dir / "openclaw_runner_stderr.log").read_text(encoding="utf-8") == "curl: (7) Failed to connect\n"

    def test_openclaw_mode_classifies_text_only_verdict_before_missing_patch(self, monkeypatch, tmp_path):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "openclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
                "OPENCLAW_CONTEXT_WINDOW": "4096",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:openclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:v1",
                },
            ),
        )
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr="gmail watcher stopped\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "openclaw_output.jsonl": json.dumps(
                        {"choices": [{"delta": {"content": "Let me inspect the repository first"}}]}
                    )
                    + "\n",
                    "openclaw_response.txt": "Let me inspect the repository first",
                    "openclaw_tool_verdict.json": json.dumps(
                        {
                            "classification": "text_only",
                            "reason": "OpenClaw streamed text output but did not modify the repository",
                        }
                    ),
                    "gateway.log": "gateway ready\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(RuntimeError, match=r"text-only OpenClaw session"):
            agent.solve(_task())

    def test_openclaw_mode_classifies_toolful_no_edit_before_generic_missing_patch(
        self, monkeypatch, tmp_path
    ):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "openclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
                "OPENCLAW_CONTEXT_WINDOW": "4096",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:openclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:v1",
                },
            ),
        )
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr="timeout\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "openclaw_output.jsonl": json.dumps(
                        {"choices": [{"delta": {"content": "I inspected the repo and ran tools"}}]}
                    )
                    + "\n",
                    "openclaw_response.txt": "I inspected the repo and ran tools",
                    "openclaw_tool_verdict.json": json.dumps(
                        {
                            "classification": "toolful",
                            "reason": "OpenClaw session used tools but did not modify the repository",
                        }
                    ),
                    "openclaw_edit_convergence.json": json.dumps(
                        {
                            "classification": "toolful_no_edit",
                            "reason": "tool trace exceeded no-edit threshold",
                            "prompt_contract_path": "openclaw_prompt_contract.txt",
                        }
                    ),
                    "openclaw_prompt_contract.txt": "OpenClaw execution contract\n",
                    "gateway.log": "gateway ready\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(
            RuntimeError,
            match=r"OpenClaw active session used tools but produced no repository edits",
        ):
            agent.solve(_task())

    def test_opencode_mode_returns_patch_and_artifact_metadata(self, monkeypatch, tmp_path):
        staged_paths: list[str] = []
        docker_exec_calls: list[list[str]] = []

        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENCODE_SMOKE_MODEL_CANDIDATES": "demo-model,fallback-model",
                "OPENCODE_STRATEGY_SEQUENCE": "bash_edit_first,guided_edit_first,edit_first",
                "OPENCODE_PROMPT_PROFILE": "edit_first",
                "OPENCODE_REQUIRE_PATCH": "1",
                "OPENCODE_TARGET_FILE_HINTS": "README.md",
                "OPENCODE_PROBLEM_STATEMENT_MAX_CHARS": "12000",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )

        def fake_stage(container_id, *, local_path, remote_path):
            staged_paths.append(remote_path)

        def fake_exec(
            container_id,
            args,
            *,
            timeout=None,
            check=True,
            env=None,
            workdir=None,
        ):
            docker_exec_calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="runner ok\n", stderr="")

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "patch.diff": "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
                    "opencode_provider_preflight.txt": "OpenCode provider preflight\nstatus: passed\nmodel: demo-model\n",
                    "opencode_startup_diagnostics.txt": "OpenCode startup diagnostics\nstatus: activity_observed\n",
                    "opencode_activity_summary.json": json.dumps(
                        {
                            "classification": "patch_created",
                            "tracked_repo_change_count": 1,
                            "tool_use_count": 3,
                            "tracked_repo_changed_paths": ["README.md"],
                            "primary_target_file": "README.md",
                            "primary_target_file_changed": True,
                        }
                    ),
                    "opencode_no_edit_reason.txt": "",
                    "opencode_output.jsonl": json.dumps({"type": "message", "message": "done"}) + "\n",
                    "opencode_stderr.log": "",
                    "opencode_session.jsonl": "{\"session\": 1}\n",
                    "opencode_candidate_models.txt": "demo-model\nfallback-model\n",
                    "opencode_prompt_profile.txt": (
                        "prompt_profile: edit_first\n"
                        "strategy_name: bash_edit_first\n"
                        "problem_statement_max_chars: 12000\n"
                        "target_file_hints_source: env_override\n"
                        "primary_target_file: README.md\n"
                        "prompt_prefix_mode: prepend\n"
                        "resolved_model_alias: demo-model\n"
                    ),
                    "opencode_prompt_contract.txt": (
                        "OpenCode execution contract:\n"
                        "- Leave a non-empty git diff before stopping.\n"
                    ),
                    "opencode_target_file_hints.txt": (
                        "strategy_name: bash_edit_first\n"
                        "target_file_hints_source: env_override\n"
                        "primary_target_file: README.md\n"
                        "target_file_hints:\n"
                        "- README.md\n"
                    ),
                    "opencode_edit_bootstrap.txt": (
                        "OpenCode edit bootstrap\n"
                        "strategy_name: bash_edit_first\n"
                    ),
                    "opencode_edit_contract.txt": (
                        "prompt_profile: edit_first\n"
                        "strategy_name: bash_edit_first\n"
                        "primary_target_file: README.md\n"
                    ),
                    "git_status_before.txt": "",
                    "git_status_after.txt": " M README.md\n",
                    "repo_root.txt": "/workspace/repo\n",
                },
            )

        monkeypatch.setattr(agent, "_stage_file_into_container", fake_stage)
        monkeypatch.setattr(agent, "_docker_exec", fake_exec)
        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        response = agent.solve(_task())

        assert response.answer.startswith("diff --git")
        assert response.metadata["agent_type"] == "opencode"
        assert response.metadata["runtime_injected"] is True
        assert response.metadata["runtime_source_image"] == "tmlrgroup/alphadiana:opencode"
        assert response.metadata["bootstrap_needed"] is False
        assert response.metadata["repo_root"] == "/workspace/repo"
        assert response.metadata["opencode_output_path"].endswith("opencode_output.jsonl")
        assert response.metadata["opencode_stderr_path"].endswith("opencode_stderr.log")
        assert response.metadata["opencode_provider_preflight_path"].endswith(
            "opencode_provider_preflight.txt"
        )
        assert response.metadata["opencode_startup_diagnostics_path"].endswith(
            "opencode_startup_diagnostics.txt"
        )
        assert response.metadata["opencode_activity_summary_path"].endswith(
            "opencode_activity_summary.json"
        )
        assert response.metadata["opencode_no_edit_reason_path"].endswith(
            "opencode_no_edit_reason.txt"
        )
        assert response.metadata["opencode_prompt_contract_path"].endswith(
            "opencode_prompt_contract.txt"
        )
        assert response.metadata["opencode_prompt_profile_path"].endswith(
            "opencode_prompt_profile.txt"
        )
        assert response.metadata["opencode_candidate_models_path"].endswith(
            "opencode_candidate_models.txt"
        )
        assert response.metadata["opencode_target_file_hints_path"].endswith(
            "opencode_target_file_hints.txt"
        )
        assert response.metadata["opencode_edit_bootstrap_path"].endswith(
            "opencode_edit_bootstrap.txt"
        )
        assert response.metadata["opencode_edit_contract_path"].endswith(
            "opencode_edit_contract.txt"
        )
        assert response.metadata["opencode_attempt_matrix_path"].endswith(
            "opencode_attempt_matrix.json"
        )
        assert response.metadata["opencode_selected_attempt_path"].endswith(
            "opencode_selected_attempt.json"
        )
        assert response.metadata["opencode_stall_reason_path"].endswith("opencode_stall_reason.txt")
        assert response.metadata["opencode_progress_snapshot_path"].endswith(
            "opencode_progress_snapshot.txt"
        )
        assert response.metadata["git_status_before_path"].endswith("git_status_before.txt")
        assert response.metadata["git_status_after_path"].endswith("git_status_after.txt")
        assert response.metadata["opencode_selected_model_alias"] == "demo-model"
        assert response.metadata["opencode_selected_strategy_name"] == "bash_edit_first"
        assert response.metadata["opencode_selected_target_file_hints"] == ["README.md"]
        assert response.metadata["opencode_selected_primary_target_file"] == "README.md"
        assert response.metadata["opencode_selected_target_file_hints_source"] == "env_override"
        assert response.metadata["opencode_selected_tracked_repo_changed_paths"] == ["README.md"]
        assert response.metadata["opencode_selected_primary_target_file_changed"] is True
        assert response.metadata["opencode_strategy_sequence"] == [
            "bash_edit_first",
            "guided_edit_first",
            "edit_first",
        ]
        assert any(path.endswith("/run_opencode.sh") for path in staged_paths)
        assert any(path.endswith("/opencode.json.template") for path in staged_paths)
        assert any("run_opencode.sh" in " ".join(call) for call in docker_exec_calls)
        assert "/swebench_agent/opencode/opencode_provider_preflight.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_startup_diagnostics.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_activity_summary.json" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_no_edit_reason.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_prompt_contract.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_prompt_profile.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_candidate_models.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_target_file_hints.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_edit_bootstrap.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_edit_contract.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_attempt_matrix.json" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_selected_attempt.json" in response.workspace_file_contents
        assert "/swebench_agent/opencode/opencode_session.jsonl" in response.workspace_file_contents
        assert "/swebench_agent/opencode/git_status_before.txt" in response.workspace_file_contents
        assert "/swebench_agent/opencode/git_status_after.txt" in response.workspace_file_contents

    def test_zeroclaw_mode_returns_patch_and_artifact_metadata(self, monkeypatch, tmp_path):
        staged_paths: list[str] = []
        docker_exec_calls: list[list[str]] = []
        exec_envs: list[dict[str, str]] = []

        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "zeroclaw",
            "output_dir": str(tmp_path),
            "temperature": 0.6,
            "max_tokens": 32768,
            "reasoning_enabled": True,
            "reasoning_effort": "high",
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "ZEROCLAW_SMOKE_MODEL_CANDIDATES": "demo-model,fallback-model",
                "ZEROCLAW_REQUIRE_PATCH": "1",
                "ZEROCLAW_PROMPT_PROFILE": "edit_first",
                "ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS": "12000",
                "ZEROCLAW_WORKSPACE_ONLY": "0",
                "ZEROCLAW_MAX_TOOL_ITERATIONS": "100",
                "ZEROCLAW_MAX_ACTIONS_PER_HOUR": "200",
                "ZEROCLAW_RUNTIME_TRACE_MODE": "none",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:zeroclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "zeroclaw-reasoning:0.6.9",
                },
            ),
        )

        def fake_stage(container_id, *, local_path, remote_path):
            staged_paths.append(remote_path)

        def fake_exec(
            container_id,
            args,
            *,
            timeout=None,
            check=True,
            env=None,
            workdir=None,
        ):
            docker_exec_calls.append(args)
            if env is not None:
                exec_envs.append(dict(env))
            return subprocess.CompletedProcess(args, 0, stdout="runner ok\n", stderr="")

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "patch.diff": "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
                    "zeroclaw_output.txt": "Applied fix\n",
                    "zeroclaw_stderr.log": "",
                    "runtime_trace.jsonl": json.dumps({"type": "tool", "name": "bash"}) + "\n",
                    "zeroclaw_no_edit_reason.txt": "",
                    "zeroclaw_candidate_models.txt": "demo-model\nfallback-model\n",
                    "zeroclaw_prompt_profile.txt": (
                        "prompt_profile: edit_first\n"
                        "problem_statement_max_chars: 12000\n"
                        "require_patch: true\n"
                        "workspace_only: false\n"
                        "resolved_model_alias: demo-model\n"
                    ),
                    "zeroclaw_prompt_contract.txt": (
                        "ZeroClaw execution contract:\n"
                        "- Leave a non-empty git diff in the repository before stopping.\n"
                    ),
                    "zeroclaw_attempt_matrix.json": json.dumps(
                        {
                            "attempts": [
                                {
                                    "attempt_index": 1,
                                    "resolved_model_alias": "demo-model",
                                    "classification": "patch_created",
                                }
                            ],
                            "selected_attempt_index": 1,
                            "tried_aliases": ["demo-model", "fallback-model"],
                        }
                    ),
                    "zeroclaw_selected_attempt.json": json.dumps(
                        {
                            "attempt_index": 1,
                            "resolved_model_alias": "demo-model",
                            "prompt_profile": "edit_first",
                            "classification": "patch_created",
                            "patch_size_bytes": 72,
                            "tracked_repo_change_count": 1,
                            "untracked_repo_change_count": 0,
                            "reason": "patch.diff is non-empty",
                            "returncode": 0,
                        }
                    ),
                    "git_status_before.txt": "",
                    "git_status_after.txt": " M app.py\n",
                    "repo_root.txt": "/workspace/repo\n",
                },
            )

        monkeypatch.setattr(agent, "_stage_file_into_container", fake_stage)
        monkeypatch.setattr(agent, "_docker_exec", fake_exec)
        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        response = agent.solve(_task())

        assert response.answer.startswith("diff --git")
        assert response.metadata["agent_type"] == "zeroclaw"
        assert response.metadata["runtime_injected"] is True
        assert response.metadata["runtime_source_image"] == "zeroclaw-reasoning:0.6.9"
        assert response.metadata["bootstrap_needed"] is False
        assert response.metadata["repo_root"] == "/workspace/repo"
        assert response.metadata["zeroclaw_output_path"].endswith("zeroclaw_output.txt")
        assert response.metadata["zeroclaw_stderr_path"].endswith("zeroclaw_stderr.log")
        assert response.metadata["zeroclaw_runtime_trace_path"].endswith("runtime_trace.jsonl")
        assert response.metadata["zeroclaw_no_edit_reason_path"].endswith(
            "zeroclaw_no_edit_reason.txt"
        )
        assert response.metadata["zeroclaw_prompt_contract_path"].endswith(
            "zeroclaw_prompt_contract.txt"
        )
        assert response.metadata["zeroclaw_prompt_profile_path"].endswith(
            "zeroclaw_prompt_profile.txt"
        )
        assert response.metadata["zeroclaw_candidate_models_path"].endswith(
            "zeroclaw_candidate_models.txt"
        )
        assert response.metadata["zeroclaw_attempt_matrix_path"].endswith(
            "zeroclaw_attempt_matrix.json"
        )
        assert response.metadata["zeroclaw_selected_attempt_path"].endswith(
            "zeroclaw_selected_attempt.json"
        )
        assert response.metadata["zeroclaw_selected_model_alias"] == "demo-model"
        assert response.metadata["zeroclaw_selected_classification"] == "patch_created"
        assert response.metadata["zeroclaw_selected_reason"] == "patch.diff is non-empty"
        assert response.metadata["zeroclaw_require_patch"] is True
        assert response.response_json["runtime_trace_present"] is True
        assert any(path.endswith("/run_zeroclaw.sh") for path in staged_paths)
        assert any(path.endswith("/zeroclaw.toml.template") for path in staged_paths)
        assert any("run_zeroclaw.sh" in " ".join(call) for call in docker_exec_calls)
        assert any(env.get("ZEROCLAW_SMOKE_MODEL_NAME") == "demo-model" for env in exec_envs)
        assert any(env.get("ZEROCLAW_TIMEOUT_SEC") == "1800" for env in exec_envs)
        assert any(env.get("ZEROCLAW_TEMPERATURE") == "0.6" for env in exec_envs)
        assert any(env.get("ZEROCLAW_PROVIDER_TIMEOUT_SECS") == "1800" for env in exec_envs)
        assert any(env.get("ZEROCLAW_PROVIDER_MAX_TOKENS") == "32768" for env in exec_envs)
        assert any(env.get("ZEROCLAW_REASONING_ENABLED") == "true" for env in exec_envs)
        assert any(env.get("ZEROCLAW_REASONING_EFFORT") == "high" for env in exec_envs)
        assert "/swebench_agent/zeroclaw/patch.diff" in response.workspace_file_contents
        assert "/swebench_agent/zeroclaw/zeroclaw_output.txt" in response.workspace_file_contents
        assert "/swebench_agent/zeroclaw/zeroclaw_stderr.log" in response.workspace_file_contents
        assert "/swebench_agent/zeroclaw/runtime_trace.jsonl" in response.workspace_file_contents
        assert "/swebench_agent/zeroclaw/zeroclaw_prompt_contract.txt" in response.workspace_file_contents
        assert "/swebench_agent/zeroclaw/zeroclaw_prompt_profile.txt" in response.workspace_file_contents
        assert "/swebench_agent/zeroclaw/zeroclaw_candidate_models.txt" in response.workspace_file_contents
        assert "/swebench_agent/zeroclaw/zeroclaw_attempt_matrix.json" in response.workspace_file_contents
        assert "/swebench_agent/zeroclaw/zeroclaw_selected_attempt.json" in response.workspace_file_contents

    def test_zeroclaw_mode_returns_auditable_response_for_active_session_no_patch(
        self, monkeypatch, tmp_path
    ):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "zeroclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:zeroclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "zeroclaw-reasoning:0.6.9",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr="timeout\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "zeroclaw_output.txt": "I inspected the repository and ran commands.\n",
                    "zeroclaw_stderr.log": "timeout\n",
                    "zeroclaw_no_edit_reason.txt": (
                        "ZeroClaw finished without tracked repository edits\n"
                    ),
                    "zeroclaw_selected_attempt.json": json.dumps(
                        {
                            "attempt_index": 1,
                            "resolved_model_alias": "demo-model",
                            "prompt_profile": "edit_first",
                            "classification": "active_session_no_patch",
                            "patch_size_bytes": 0,
                            "tracked_repo_change_count": 0,
                            "untracked_repo_change_count": 0,
                            "reason": "ZeroClaw finished without tracked repository edits",
                            "returncode": 1,
                        }
                    ),
                    "zeroclaw_attempt_matrix.json": json.dumps({"attempts": []}),
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        response = agent.solve(_task())

        assert response.answer == ""
        assert len(response.trajectory) >= 2
        assert response.metadata["zeroclaw_selected_classification"] == "active_session_no_patch"
        assert response.metadata["zeroclaw_selected_returncode"] == 1
        assert response.metadata["zeroclaw_preserved_failure"] is True
        assert (
            response.metadata["zeroclaw_selected_reason"]
            == "ZeroClaw finished without tracked repository edits"
        )
        assert response.finish_reason == "preserved_failure"
        assert response.raw_output == "I inspected the repository and ran commands."
        assert (
            response.artifact_manifest["files"]["response_stream"]
            == "/swebench_agent/zeroclaw/zeroclaw_output.txt"
        )

    def test_zeroclaw_mode_returns_auditable_response_for_untracked_only_changes(
        self, monkeypatch, tmp_path
    ):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "zeroclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:zeroclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "zeroclaw-reasoning:0.6.9",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr=""
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "zeroclaw_output.txt": "Added scratch notes\n",
                    "zeroclaw_no_edit_reason.txt": (
                        "Only untracked files changed; git diff --binary remained empty\n"
                    ),
                    "zeroclaw_selected_attempt.json": json.dumps(
                        {
                            "attempt_index": 1,
                            "resolved_model_alias": "demo-model",
                            "prompt_profile": "edit_first",
                            "classification": "untracked_only_changes",
                            "patch_size_bytes": 0,
                            "tracked_repo_change_count": 0,
                            "untracked_repo_change_count": 1,
                            "reason": "Only untracked files changed; git diff --binary remained empty",
                            "returncode": 1,
                        }
                    ),
                    "zeroclaw_attempt_matrix.json": json.dumps({"attempts": []}),
                    "git_status_after.txt": "?? notes.txt\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        response = agent.solve(_task())

        assert response.answer == ""
        assert len(response.trajectory) >= 2
        assert response.metadata["zeroclaw_selected_classification"] == "untracked_only_changes"
        assert response.metadata["zeroclaw_selected_returncode"] == 1
        assert response.metadata["zeroclaw_preserved_failure"] is True
        assert (
            response.metadata["zeroclaw_selected_reason"]
            == "Only untracked files changed; git diff --binary remained empty"
        )
        assert response.finish_reason == "preserved_failure"
        assert response.raw_output == "Added scratch notes"

    def test_zeroclaw_mode_returns_runner_logs_when_output_file_is_missing(self, monkeypatch, tmp_path):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "zeroclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:zeroclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "zeroclaw-reasoning:0.6.9",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 0, stdout="runner ok\n", stderr=""
            ),
        )
        monkeypatch.setattr(
            agent,
            "_docker_cp_from",
            lambda container_id, src, dst: _write_artifacts(dst, {"zeroclaw_stderr.log": ""}),
        )

        response = agent.solve(_task())

        assert response.answer == ""
        assert response.raw_output == "runner ok"
        assert response.response_json["runner_stdout_text"] == "runner ok"
        assert response.metadata["zeroclaw_preserved_failure"] is True

    def test_zeroclaw_mode_returns_auditable_response_for_cli_error(self, monkeypatch, tmp_path):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "zeroclaw",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:zeroclaw-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "zeroclaw-reasoning:0.6.9",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 17, stdout="runner failed\n", stderr="TLS handshake failed\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "zeroclaw_output.txt": "partial output\n",
                    "zeroclaw_stderr.log": "TLS handshake failed\n",
                    "zeroclaw_no_edit_reason.txt": "TLS handshake failed\n",
                    "zeroclaw_selected_attempt.json": json.dumps(
                        {
                            "attempt_index": 1,
                            "resolved_model_alias": "demo-model",
                            "prompt_profile": "edit_first",
                            "classification": "cli_error",
                            "patch_size_bytes": 0,
                            "tracked_repo_change_count": 0,
                            "untracked_repo_change_count": 0,
                            "reason": "TLS handshake failed",
                            "returncode": 17,
                        }
                    ),
                    "zeroclaw_attempt_matrix.json": json.dumps({"attempts": []}),
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        response = agent.solve(_task())

        assert response.answer == ""
        assert len(response.trajectory) >= 2
        assert response.metadata["zeroclaw_selected_classification"] == "cli_error"
        assert response.metadata["zeroclaw_selected_returncode"] == 17
        assert response.metadata["zeroclaw_selected_reason"] == "TLS handshake failed"
        assert response.metadata["zeroclaw_preserved_failure"] is True
        assert response.response_json["runner_stderr_text"] == "TLS handshake failed"
        assert response.finish_reason == "preserved_failure"

    def test_opencode_mode_auto_derives_target_hints_for_guided_strategy(
        self, monkeypatch, tmp_path
    ):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENCODE_SMOKE_MODEL_CANDIDATES": "demo-model",
                "OPENCODE_STRATEGY_SEQUENCE": "bash_edit_first",
                "OPENCODE_REQUIRE_PATCH": "1",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/app")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)

        exec_envs: list[dict[str, str]] = []

        def fake_exec(
            container_id,
            args,
            *,
            timeout=None,
            check=True,
            env=None,
            workdir=None,
        ):
            command = " ".join(args)
            if "mkdir -p" in command:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if env is not None:
                exec_envs.append(dict(env))
            return subprocess.CompletedProcess(args, 0, stdout="runner ok\n", stderr="")

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "patch.diff": "diff --git a/src/user/email.js b/src/user/email.js\n",
                    "opencode_provider_preflight.txt": (
                        "OpenCode provider preflight\nstatus: passed\nmodel: demo-model\n"
                    ),
                    "opencode_startup_diagnostics.txt": (
                        "OpenCode startup diagnostics\nstatus: activity_observed\n"
                    ),
                    "opencode_activity_summary.json": json.dumps(
                        {
                            "classification": "patch_created",
                            "tracked_repo_change_count": 2,
                            "tool_use_count": 4,
                            "tracked_repo_changed_paths": ["src/database/redis/main.js"],
                            "primary_target_file": "src/database/redis/main.js",
                            "primary_target_file_changed": True,
                        }
                    ),
                    "opencode_output.jsonl": json.dumps({"type": "message", "message": "done"})
                    + "\n",
                    "opencode_candidate_models.txt": "demo-model\n",
                    "opencode_target_file_hints.txt": (
                        "strategy_name: bash_edit_first\n"
                        "target_file_hints_source: auto_derived\n"
                        "primary_target_file: src/database/redis/main.js\n"
                        "target_file_hints:\n"
                        "- src/database/redis/main.js\n"
                        "- src/database/mongo/main.js\n"
                        "- src/user/email.js\n"
                    ),
                    "opencode_edit_bootstrap.txt": (
                        "OpenCode edit bootstrap\n"
                        "strategy_name: bash_edit_first\n"
                    ),
                    "opencode_edit_contract.txt": (
                        "prompt_profile: edit_first\n"
                        "strategy_name: bash_edit_first\n"
                        "primary_target_file: src/database/redis/main.js\n"
                    ),
                    "opencode_prompt_profile.txt": (
                        "prompt_profile: edit_first\n"
                        "strategy_name: bash_edit_first\n"
                        "target_file_hints_source: auto_derived\n"
                        "primary_target_file: src/database/redis/main.js\n"
                        "prompt_prefix_mode: prepend\n"
                        "resolved_model_alias: demo-model\n"
                    ),
                    "opencode_prompt_contract.txt": "OpenCode execution contract\n",
                    "git_status_before.txt": "",
                    "git_status_after.txt": " M src/user/email.js\n",
                    "repo_root.txt": "/app\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_exec", fake_exec)
        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        response = agent.solve(
            _task(
                problem=(
                    "Email confirmation should remain valid until it explicitly expires. "
                    "canSendValidation should return true if it has been long enough to re-send confirmation."
                ),
                metadata={
                    "repo": "NodeBB/NodeBB",
                    "selected_test_files_to_run": [
                        "test/database.js",
                        "test/database/keys.js",
                        "test/user/emails.js",
                    ],
                    "fail_to_pass": [
                        "test/database.js | Key methods should return multiple keys and null if key doesn't exist",
                        "test/user/emails.js | canSendValidation should return true if it has been long enough to re-send confirmation",
                    ],
                },
            )
        )

        runner_env = exec_envs[-1]
        assert runner_env["OPENCODE_STRATEGY_NAME"] == "bash_edit_first"
        assert runner_env["OPENCODE_TARGET_FILE_HINTS_SOURCE"] == "auto_derived"
        assert runner_env["OPENCODE_PRIMARY_TARGET_FILE"] == "src/database/redis/main.js"
        assert runner_env["OPENCODE_TARGET_FILE_HINTS"] == (
            "src/database/redis/main.js,src/database/mongo/main.js,src/user/email.js"
        )
        assert response.metadata["opencode_selected_target_file_hints"] == [
            "src/database/redis/main.js",
            "src/database/mongo/main.js",
            "src/user/email.js",
        ]
        assert response.metadata["opencode_selected_primary_target_file"] == (
            "src/database/redis/main.js"
        )
        assert response.metadata["opencode_selected_target_file_hints_source"] == "auto_derived"

    def test_opencode_mode_fails_on_provider_preflight_before_missing_output(self, monkeypatch, tmp_path):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr=""
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "opencode_provider_preflight.txt": (
                        "OpenCode provider preflight\n"
                        "status: failed\n"
                        "model: demo-model\n"
                        "error: unsupported tool calling\n"
                    ),
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(RuntimeError, match=r"OpenCode provider preflight failed: unsupported tool calling"):
            agent.solve(_task())

    def test_opencode_mode_preserves_root_attempt_matrix_across_multiple_aliases(
        self, monkeypatch, tmp_path
    ):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "first-model",
                "OPENCODE_SMOKE_MODEL_CANDIDATES": "first-model,second-model",
                "OPENCODE_STRATEGY_SEQUENCE": "guided_edit_first,edit_first",
                "OPENCODE_TARGET_FILE_HINTS": "src/database/redis/main.js,src/database/mongo/main.js",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)

        def fake_exec(
            container_id,
            args,
            *,
            timeout=None,
            check=True,
            env=None,
            workdir=None,
        ):
            command = " ".join(args)
            if "mkdir -p" in command:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 1, stdout="runner failed\n", stderr="timeout\n")

        def fake_cp_from(container_id, src, dst):
            attempt_dir = Path(dst)
            if attempt_dir.name == "01_first-model__guided_edit_first":
                _write_artifacts(
                    attempt_dir,
                    {
                        "opencode_provider_preflight.txt": (
                            "OpenCode provider preflight\nstatus: passed\nmodel: first-model\n"
                        ),
                        "opencode_startup_diagnostics.txt": (
                            "OpenCode startup diagnostics\nstatus: activity_observed\nmodel: first-model\n"
                        ),
                        "opencode_output.jsonl": json.dumps(
                            {"type": "tool_use", "message": "still active"}
                        )
                        + "\n",
                        "opencode_activity_summary.json": json.dumps(
                            {
                                "classification": "active_session_no_patch",
                                "tracked_repo_change_count": 0,
                                "tool_use_count": 12,
                            }
                        ),
                        "opencode_no_edit_reason.txt": (
                            "tool threshold reached without tracked repository edits"
                        ),
                        "opencode_attempt_matrix.json": json.dumps(
                            {
                                "attempts": [
                                    {
                                        "attempt_index": 1,
                                        "resolved_model_alias": "first-model",
                                        "strategy_name": "guided_edit_first",
                                        "target_file_hints": [
                                            "src/database/redis/main.js",
                                            "src/database/mongo/main.js",
                                        ],
                                        "classification": "active_session_no_patch",
                                    }
                                ]
                            }
                        ),
                        "opencode_selected_attempt.json": json.dumps(
                            {
                                "attempt_index": 1,
                                "resolved_model_alias": "first-model",
                                "strategy_name": "guided_edit_first",
                            }
                        ),
                        "opencode_candidate_models.txt": "first-model\nsecond-model\n",
                        "opencode_prompt_profile.txt": (
                            "prompt_profile: edit_first\n"
                            "strategy_name: guided_edit_first\n"
                            "target_file_hints_source: env_override\n"
                        ),
                        "opencode_prompt_contract.txt": "OpenCode execution contract\n",
                        "opencode_target_file_hints.txt": (
                            "strategy_name: guided_edit_first\n"
                            "target_file_hints_source: env_override\n"
                            "target_file_hints:\n"
                            "- src/database/redis/main.js\n"
                            "- src/database/mongo/main.js\n"
                        ),
                        "opencode_edit_bootstrap.txt": "OpenCode edit bootstrap\n",
                        "git_status_before.txt": "",
                        "git_status_after.txt": "",
                    },
                )
                return

            if attempt_dir.name == "02_first-model__edit_first":
                _write_artifacts(
                    attempt_dir,
                    {
                        "opencode_provider_preflight.txt": (
                            "OpenCode provider preflight\nstatus: passed\nmodel: first-model\n"
                        ),
                        "opencode_startup_diagnostics.txt": (
                            "OpenCode startup diagnostics\nstatus: activity_observed\nmodel: first-model\n"
                        ),
                        "opencode_output.jsonl": json.dumps(
                            {"type": "tool_use", "message": "still active"}
                        )
                        + "\n",
                        "opencode_activity_summary.json": json.dumps(
                            {
                                "classification": "active_session_no_patch",
                                "tracked_repo_change_count": 0,
                                "tool_use_count": 9,
                            }
                        ),
                        "opencode_no_edit_reason.txt": (
                            "active session completed without repository edits"
                        ),
                        "opencode_attempt_matrix.json": json.dumps(
                            {
                                "attempts": [
                                    {
                                        "attempt_index": 2,
                                        "resolved_model_alias": "first-model",
                                        "strategy_name": "edit_first",
                                        "target_file_hints": [],
                                        "classification": "active_session_no_patch",
                                    }
                                ]
                            }
                        ),
                        "opencode_selected_attempt.json": json.dumps(
                            {
                                "attempt_index": 2,
                                "resolved_model_alias": "first-model",
                                "strategy_name": "edit_first",
                            }
                        ),
                        "opencode_candidate_models.txt": "first-model\nsecond-model\n",
                        "opencode_prompt_profile.txt": (
                            "prompt_profile: edit_first\n"
                            "strategy_name: edit_first\n"
                            "target_file_hints_source: strategy_disabled\n"
                        ),
                        "opencode_prompt_contract.txt": "OpenCode execution contract\n",
                        "opencode_target_file_hints.txt": (
                            "strategy_name: edit_first\n"
                            "target_file_hints_source: strategy_disabled\n"
                            "target_file_hints:\n"
                            "<none>\n"
                        ),
                        "opencode_edit_bootstrap.txt": "OpenCode edit bootstrap\n",
                    },
                )
                return

            if attempt_dir.name == "03_second-model__guided_edit_first":
                _write_artifacts(
                    attempt_dir,
                    {
                        "opencode_provider_preflight.txt": (
                            "OpenCode provider preflight\n"
                            "status: failed\n"
                            "model: second-model\n"
                            "error: unsupported tool calling\n"
                        ),
                        "opencode_attempt_matrix.json": json.dumps(
                            {
                                "attempts": [
                                    {
                                        "attempt_index": 3,
                                        "resolved_model_alias": "second-model",
                                        "strategy_name": "guided_edit_first",
                                        "target_file_hints": [
                                            "src/database/redis/main.js",
                                            "src/database/mongo/main.js",
                                        ],
                                        "classification": "provider_preflight_failed",
                                    }
                                ]
                            }
                        ),
                        "opencode_selected_attempt.json": json.dumps(
                            {
                                "attempt_index": 3,
                                "resolved_model_alias": "second-model",
                                "strategy_name": "guided_edit_first",
                            }
                        ),
                        "opencode_candidate_models.txt": "first-model\nsecond-model\n",
                        "opencode_prompt_profile.txt": (
                            "prompt_profile: edit_first\n"
                            "strategy_name: guided_edit_first\n"
                            "target_file_hints_source: env_override\n"
                        ),
                        "opencode_prompt_contract.txt": "OpenCode execution contract\n",
                        "opencode_target_file_hints.txt": (
                            "strategy_name: guided_edit_first\n"
                            "target_file_hints_source: env_override\n"
                            "target_file_hints:\n"
                            "- src/database/redis/main.js\n"
                            "- src/database/mongo/main.js\n"
                        ),
                        "opencode_edit_bootstrap.txt": "OpenCode edit bootstrap\n",
                    },
                )
                return

            if attempt_dir.name == "04_second-model__edit_first":
                _write_artifacts(
                    attempt_dir,
                    {
                        "opencode_provider_preflight.txt": (
                            "OpenCode provider preflight\n"
                            "status: failed\n"
                            "model: second-model\n"
                            "error: unsupported tool calling\n"
                        ),
                        "opencode_attempt_matrix.json": json.dumps(
                            {
                                "attempts": [
                                    {
                                        "attempt_index": 4,
                                        "resolved_model_alias": "second-model",
                                        "strategy_name": "edit_first",
                                        "target_file_hints": [],
                                        "classification": "provider_preflight_failed",
                                    }
                                ]
                            }
                        ),
                        "opencode_selected_attempt.json": json.dumps(
                            {
                                "attempt_index": 4,
                                "resolved_model_alias": "second-model",
                                "strategy_name": "edit_first",
                            }
                        ),
                        "opencode_candidate_models.txt": "first-model\nsecond-model\n",
                        "opencode_prompt_profile.txt": (
                            "prompt_profile: edit_first\n"
                            "strategy_name: edit_first\n"
                            "target_file_hints_source: strategy_disabled\n"
                        ),
                        "opencode_prompt_contract.txt": "OpenCode execution contract\n",
                        "opencode_target_file_hints.txt": (
                            "strategy_name: edit_first\n"
                            "target_file_hints_source: strategy_disabled\n"
                            "target_file_hints:\n"
                            "<none>\n"
                        ),
                        "opencode_edit_bootstrap.txt": "OpenCode edit bootstrap\n",
                    },
                )
                return

            raise AssertionError(f"unexpected attempt directory {attempt_dir.name}")

        monkeypatch.setattr(agent, "_docker_exec", fake_exec)
        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(
            RuntimeError,
            match=r"OpenCode provider preflight failed: unsupported tool calling \(model=second-model\)",
        ):
            agent.solve(_task())

        artifacts_dir = next(tmp_path.glob("instance_demo/sample_0/*"))
        root_attempt_matrix = json.loads(
            (artifacts_dir / "opencode_attempt_matrix.json").read_text(encoding="utf-8")
        )
        assert [
            (attempt["resolved_model_alias"], attempt["strategy_name"])
            for attempt in root_attempt_matrix["attempts"]
        ] == [
            ("first-model", "guided_edit_first"),
            ("first-model", "edit_first"),
            ("second-model", "guided_edit_first"),
            ("second-model", "edit_first"),
        ]
        assert root_attempt_matrix["tried_strategy_names"] == [
            "guided_edit_first",
            "edit_first",
        ]
        assert root_attempt_matrix["selected_attempt_index"] == 4
        root_selected_attempt = json.loads(
            (artifacts_dir / "opencode_selected_attempt.json").read_text(encoding="utf-8")
        )
        assert root_selected_attempt["resolved_model_alias"] == "second-model"
        assert root_selected_attempt["strategy_name"] == "edit_first"

    def test_opencode_mode_fails_on_startup_timeout_before_idle_stall(self, monkeypatch, tmp_path):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr="sqlite-migration:done\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "opencode_provider_preflight.txt": (
                        "OpenCode provider preflight\nstatus: passed\nmodel: demo-model\n"
                    ),
                    "opencode_startup_diagnostics.txt": (
                        "OpenCode startup diagnostics\n"
                        "status: no_activity_within_timeout\n"
                        "model: demo-model\n"
                    ),
                    "opencode_stderr.log": "sqlite-migration:done\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(
            RuntimeError,
            match=r"OpenCode startup produced no session/output activity",
        ):
            agent.solve(_task())

    def test_opencode_mode_fails_when_process_exits_before_startup_activity(
        self, monkeypatch, tmp_path
    ):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr=""
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "opencode_provider_preflight.txt": (
                        "OpenCode provider preflight\nstatus: passed\nmodel: demo-model\n"
                    ),
                    "opencode_startup_diagnostics.txt": (
                        "OpenCode startup diagnostics\n"
                        "status: process_exited_before_activity\n"
                        "model: demo-model\n"
                    ),
                    "opencode_output.jsonl": "",
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(
            RuntimeError,
            match=r"OpenCode startup exited before any session/output activity",
        ):
            agent.solve(_task())

    def test_opencode_mode_classifies_stall_artifacts_before_missing_patch(self, monkeypatch, tmp_path):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr="stall\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "opencode_output.jsonl": json.dumps({"type": "message", "message": "started"}) + "\n",
                    "opencode_stderr.log": "sqlite-migration:done\n",
                    "opencode_stall_reason.txt": "OpenCode stalled after 600s without output progress\n",
                    "opencode_progress_snapshot.txt": (
                        "OpenCode progress snapshot\n"
                        "opencode_output.jsonl | bytes=64 lines=1\n"
                    ),
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(
            RuntimeError,
            match=r"OpenCode run stalled after 600s without output progress",
        ):
            agent.solve(_task())

        artifacts_dir = next(tmp_path.glob("instance_demo/sample_0/*"))
        assert (artifacts_dir / "opencode_stall_reason.txt").read_text(encoding="utf-8").startswith(
            "OpenCode stalled after"
        )
        assert "bytes=64" in (
            artifacts_dir / "opencode_progress_snapshot.txt"
        ).read_text(encoding="utf-8")

    def test_opencode_mode_classifies_activity_no_patch_before_generic_missing_patch(
        self, monkeypatch, tmp_path
    ):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr="timeout\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "opencode_provider_preflight.txt": (
                        "OpenCode provider preflight\nstatus: passed\nmodel: demo-model\n"
                    ),
                    "opencode_startup_diagnostics.txt": (
                        "OpenCode startup diagnostics\nstatus: activity_observed\nmodel: demo-model\n"
                    ),
                    "opencode_output.jsonl": json.dumps({"type": "tool_use", "message": "still active"}) + "\n",
                    "opencode_activity_summary.json": json.dumps(
                        {
                            "classification": "active_session_no_patch",
                            "tracked_repo_change_count": 0,
                            "tool_use_count": 49,
                            "reason": "tool threshold reached without tracked repository edits",
                        }
                    ),
                    "opencode_no_edit_reason.txt": (
                        "tool threshold reached without tracked repository edits"
                    ),
                    "opencode_progress_snapshot.txt": "OpenCode progress snapshot\n",
                    "opencode_stderr.log": "timeout\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(
            RuntimeError,
            match=r"OpenCode active session timed out without repository edits",
        ):
            agent.solve(_task())

    def test_opencode_mode_allows_unresolved_no_patch_when_patch_not_required(
        self, monkeypatch, tmp_path
    ):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
                "OPENCODE_REQUIRE_PATCH": "0",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr="timeout\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "opencode_provider_preflight.txt": (
                        "OpenCode provider preflight\nstatus: passed\nmodel: demo-model\n"
                    ),
                    "opencode_startup_diagnostics.txt": (
                        "OpenCode startup diagnostics\nstatus: activity_observed\nmodel: demo-model\n"
                    ),
                    "opencode_output.jsonl": json.dumps(
                        {"type": "tool_use", "message": "still active"}
                    )
                    + "\n",
                    "opencode_activity_summary.json": json.dumps(
                        {
                            "classification": "active_session_no_patch",
                            "tracked_repo_change_count": 0,
                            "tool_use_count": 49,
                            "reason": "tool threshold reached without tracked repository edits",
                        }
                    ),
                    "opencode_no_edit_reason.txt": (
                        "tool threshold reached without tracked repository edits"
                    ),
                    "opencode_progress_snapshot.txt": "OpenCode progress snapshot\n",
                    "opencode_stderr.log": "timeout\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        response = agent.solve(_task())

        assert response.answer == ""
        assert response.raw_output == '{"type": "tool_use", "message": "still active"}'
        assert response.metadata["opencode_require_patch"] is False
        assert response.metadata["opencode_selected_classification"] == "active_session_no_patch"
        assert (
            response.metadata["opencode_selected_reason"]
            == "tool threshold reached without tracked repository edits"
        )

    def test_opencode_mode_fails_on_error_record(self, monkeypatch, tmp_path):
        agent = SWEBenchDockerAgent()
        agent.setup({
            "agent_type": "opencode",
            "output_dir": str(tmp_path),
            "env": {
                "OPENAI_MODEL_NAME": "demo-model",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
                "OPENAI_API_KEY": "demo-key",
            },
        })

        monkeypatch.setattr(agent, "_docker_pull", lambda image: None)
        monkeypatch.setattr(agent, "_docker_create", lambda image: "container123")
        monkeypatch.setattr(agent, "_docker_start", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_stop", lambda container_id: None)
        monkeypatch.setattr(agent, "_docker_rm", lambda container_id: None)
        monkeypatch.setattr(agent, "_detect_repo_root", lambda container_id: "/workspace/repo")
        monkeypatch.setattr(
            agent,
            "_prepare_runtime_image",
            lambda image: (
                "alphadiana-swebench-runtime:opencode-test",
                {
                    "runtime_injected": True,
                    "runtime_image_built": False,
                    "runtime_source_image": "tmlrgroup/alphadiana:opencode",
                },
            ),
        )
        monkeypatch.setattr(agent, "_stage_file_into_container", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            agent,
            "_docker_exec",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                kwargs.get("args", []), 1, stdout="runner failed\n", stderr="tool error\n"
            ),
        )

        def fake_cp_from(container_id, src, dst):
            _write_artifacts(
                dst,
                {
                    "opencode_output.jsonl": json.dumps({
                        "type": "error",
                        "error": {"data": {"message": "tool error"}},
                    }) + "\n",
                    "opencode_stderr.log": "tool error\n",
                },
            )

        monkeypatch.setattr(agent, "_docker_cp_from", fake_cp_from)

        with pytest.raises(RuntimeError, match="OpenCode run reported error record: tool error"):
            agent.solve(_task())
