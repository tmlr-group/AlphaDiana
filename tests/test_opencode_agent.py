import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alphadiana.agent.opencode import OpenCodeAgent, _build_prompt, _extract_event_texts, _extract_strict_answer
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.attachments import write_attachments


def test_extract_event_texts_reads_nested_part_text():
    obj = {
        "type": "text",
        "part": {
            "type": "text",
            "text": "Nested assistant text",
        },
    }
    assert _extract_event_texts(obj) == ["Nested assistant text"]


def test_extract_strict_answer_ignores_partial_reasoning_numbers():
    text = "Trying N=3, then N=6, but still checking cases."
    assert _extract_strict_answer(text) == ""


def test_extract_strict_answer_accepts_boxed_answer():
    text = "Done.\n\\boxed{3}"
    assert _extract_strict_answer(text) == "3"


def test_build_prompt_mentions_attached_files():
    prompt = _build_prompt(
        "What letter is shown?",
        "Use tools when needed.",
        [Path("attachments/image_1.png")],
    )

    assert "--- Attachments ---" in prompt
    assert "image_1.png" in prompt


def test_write_attachments_uses_image_suffixes(tmp_path):
    paths = write_attachments(tmp_path, {
        "image_1": b"\x89PNG\r\n",
        "image_1_mime": b"image/png",
    })

    assert len(paths) == 1
    assert paths[0].name == "image_1.png"
    assert paths[0].read_bytes() == b"\x89PNG\r\n"


def test_solve_uses_cli_with_image_attachments():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    process = MagicMock()
    process.communicate.return_value = (
        '{"type":"assistant","message":{"content":"Done.\\\\boxed{A}"}}\n',
        "",
    )
    process.returncode = 0
    seen: dict[str, object] = {}

    task = BenchmarkTask(
        task_id="hle_0",
        problem="Which option is correct?",
        ground_truth="A",
        attachments={
            "image_1": b"\x89PNG\r\n",
            "image_1_mime": b"image/png",
        },
    )

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs["cwd"]
        seen["env"] = kwargs["env"]
        config_path = Path(kwargs["env"]["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
        seen["provider_config"] = json.loads(config_path.read_text(encoding="utf-8"))
        return process

    with patch("subprocess.Popen", side_effect=fake_popen) as popen:
        result = agent.solve(task)

    popen.assert_called_once()
    cmd = seen["cmd"]
    assert isinstance(cmd, list)
    assert "--file" in cmd
    attachment_path = Path(cmd[cmd.index("--file") + 1])
    assert attachment_path.name == "image_1.png"
    provider_config = seen["provider_config"]
    assert provider_config["model"] == "custom/test-model"
    model_spec = provider_config["provider"]["custom"]["models"]["test-model"]
    assert model_spec["attachment"] is True
    assert model_spec["modalities"]["input"] == ["text", "image"]
    assert result.answer == "A"
    assert result.metadata["transport"] == "opencode_cli"
    assert result.metadata["num_attachments"] == 1
    assert result.workspace_file_contents["attachments/image_1.png.base64"] == "iVBORw0K"
    manifest = json.loads(result.workspace_file_contents["attachment_manifest.json"])
    assert manifest["attachments"][0]["base64_artifact"] == "attachments/image_1.png.base64"


def test_solve_text_only_uses_opencode_cli():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    process = MagicMock()
    process.communicate.return_value = (
        '{"type":"assistant","message":{"content":"Done.\\\\boxed{A}"}}\n',
        "",
    )
    process.returncode = 0

    task = BenchmarkTask(
        task_id="text_0",
        problem="Which option is correct?",
        ground_truth="A",
    )

    with patch("subprocess.Popen", return_value=process) as popen:
        result = agent.solve(task)

    cmd = popen.call_args.args[0]
    assert "--file" not in cmd
    assert cmd[-2] == "--"
    assert "Which option is correct?" in cmd[-1]
    assert result.answer == "A"
    assert result.metadata["transport"] == "opencode_cli"
    assert result.request_messages[0]["role"] == "system"
    assert result.response_json["records"][0]["type"] == "assistant"
    assert result.artifact_manifest["files"]["response_stream"] == "opencode_output.jsonl"
    assert result.artifact_manifest["files"]["opencode_output"] == "opencode_output.jsonl"
    assert result.workspace_file_contents["prompt.txt"].startswith("You are an expert problem solver.")


def test_solve_preserves_namespaced_model_alias_in_provider_config():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "Qwen/Qwen3.5-27B",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    process = MagicMock()
    process.communicate.return_value = (
        '{"type":"assistant","message":{"content":"Done.\\\\boxed{A}"}}\n',
        "",
    )
    process.returncode = 0
    seen: dict[str, object] = {}

    task = BenchmarkTask(
        task_id="text_qwen",
        problem="Which option is correct?",
        ground_truth="A",
    )

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        config_path = Path(kwargs["env"]["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
        seen["provider_config"] = json.loads(config_path.read_text(encoding="utf-8"))
        return process

    with patch("subprocess.Popen", side_effect=fake_popen):
        result = agent.solve(task)

    provider_config = seen["provider_config"]
    assert provider_config["model"] == "custom/Qwen/Qwen3.5-27B"
    assert "Qwen/Qwen3.5-27B" in provider_config["provider"]["custom"]["models"]
    assert "custom/Qwen/Qwen3.5-27B" in seen["cmd"]
    assert result.answer == "A"


def test_solve_preserves_opencode_event_stream_as_trajectory():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
        "system_prompt": "Use tools when needed.",
    })

    process = MagicMock()
    process.communicate.return_value = (
        "\n".join([
            json.dumps({"type": "tool_use", "message": "Inspecting workspace"}),
            json.dumps({"type": "assistant", "message": {"content": "Done.\\n\\boxed{A}"}}),
        ]),
        "stderr text\n",
    )
    process.returncode = 0

    task = BenchmarkTask(
        task_id="text_1",
        problem="Which option is correct?",
        ground_truth="A",
    )

    with patch("subprocess.Popen", return_value=process):
        result = agent.solve(task)

    assert len(result.trajectory) >= 3
    assert result.trajectory[0]["role"] == "system"
    assert any(step.get("type") == "tool_use" for step in result.trajectory)
    assert result.reasoning_trajectory[0]["type"] == "tool_use"
    assert result.response_json["stderr_text"] == "stderr text"
    assert result.workspace_file_contents["opencode_output.jsonl"]


def test_solve_raises_provider_error_when_opencode_emits_error_event():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "qwen3vl",
        "api_base": "https://example.test/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    process = MagicMock()
    process.communicate.return_value = (
        json.dumps({
            "type": "error",
            "sessionID": "session-error",
            "error": {
                "name": "APIError",
                "data": {
                    "message": (
                        'litellm.BadRequestError: OpenAIException - "auto" tool choice requires '
                        "--enable-auto-tool-choice"
                    )
                },
            },
        }) + "\n",
        "",
    )
    process.returncode = 0

    task = BenchmarkTask(
        task_id="hle_error",
        problem="Which option is correct?",
        ground_truth="A",
    )

    with patch("subprocess.Popen", return_value=process):
        with pytest.raises(RuntimeError) as excinfo:
            agent.solve(task)

    assert getattr(excinfo.value, "error_type", "") == "provider_error"
    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert partial.answer is None
    assert partial.metadata["opencode_error_name"] == "APIError"
    assert "tool choice requires" in partial.metadata["opencode_error_message"]
    assert "tool choice requires" in partial.response_json["opencode_error_message"]


def test_solve_in_container_raises_provider_error_when_opencode_emits_error_event():
    agent = OpenCodeAgent()
    agent.setup({
        "runtime": "swebench_container",
        "model_name": "Qwen/Qwen3.5-27B",
        "api_base": "http://172.17.0.1:8011/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    runtime_manager = MagicMock()
    runtime_manager.run_task.return_value = {
        "stdout": json.dumps({
            "type": "error",
            "sessionID": "session-error",
            "error": {
                "name": "ContextOverflowError",
                "data": {
                    "message": (
                        "This model's maximum context length is 32768 tokens. "
                        "However, you requested 32000 output tokens and your prompt "
                        "contains at least 769 input tokens."
                    )
                },
            },
        }) + "\n",
        "stderr": "",
        "exit_code": 0,
        "patch": "",
    }
    runtime_manager.collect_artifacts.return_value = {
        "artifact_manifest": {},
        "session_files": ["session-error.jsonl"],
    }
    agent._runtime_manager = runtime_manager

    sandbox = MagicMock()
    task = BenchmarkTask(
        task_id="swe_django__django-11790",
        problem="Fix the regression.",
        ground_truth="diff --git a/foo b/foo",
        metadata={
            "instance_id": "django__django-11790",
            "repo": "django/django",
            "base_commit": "deadbeef",
        },
    )

    with pytest.raises(RuntimeError) as excinfo:
        agent.solve(task, sandbox=sandbox)

    assert getattr(excinfo.value, "error_type", "") == "provider_error"
    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert partial.answer is None
    assert partial.metadata["transport"] == "opencode_cli_container"
    assert partial.metadata["opencode_error_name"] == "ContextOverflowError"
    assert "maximum context length" in partial.metadata["opencode_error_message"]
    assert "maximum context length" in partial.response_json["opencode_error_message"]


def test_solve_preserves_opencode_session_trace_when_present():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    process = MagicMock()
    process.communicate.return_value = (
        json.dumps({
            "type": "assistant",
            "sessionID": "session-demo",
            "message": {"content": "Done.\\n\\boxed{A}"},
        }) + "\n",
        "",
    )
    process.returncode = 0

    task = BenchmarkTask(
        task_id="text_session",
        problem="Which option is correct?",
        ground_truth="A",
    )

    def fake_popen(cmd, **kwargs):
        session_dir = Path(kwargs["env"]["XDG_CONFIG_HOME"]) / "opencode" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session-demo.jsonl").write_text(
            json.dumps({"type": "message", "role": "assistant", "content": "Done"}) + "\n",
            encoding="utf-8",
        )
        return process

    with patch("subprocess.Popen", side_effect=fake_popen):
        result = agent.solve(task)

    assert result.artifact_manifest["files"]["session_trace"] == "opencode_session.jsonl"
    assert result.artifact_manifest["files"]["opencode_session"] == "opencode_session.jsonl"
    assert "opencode_session.jsonl" in result.workspace_file_contents


def test_solve_preserves_opencode_state_and_memory_artifacts():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
        "agent_md_content": "# custom agent\n",
    })

    process = MagicMock()
    process.communicate.return_value = (
        json.dumps({
            "type": "assistant",
            "sessionID": "session-demo",
            "message": {"content": "Done.\\n\\boxed{A}"},
        }) + "\n",
        "",
    )
    process.returncode = 0

    task = BenchmarkTask(
        task_id="text_session_state",
        problem="Which option is correct?",
        ground_truth="A",
    )

    def fake_popen(cmd, **kwargs):
        opencode_root = Path(kwargs["env"]["XDG_CONFIG_HOME"]) / "opencode"
        session_dir = opencode_root / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session-demo.jsonl").write_text(
            json.dumps({"type": "message", "role": "assistant", "content": "Done"}) + "\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(opencode_root / "opencode.db")
        conn.execute("CREATE TABLE session(id TEXT)")
        conn.execute("INSERT INTO session(id) VALUES ('session-demo')")
        conn.commit()
        conn.close()
        return process

    with patch("subprocess.Popen", side_effect=fake_popen):
        result = agent.solve(task)

    files = result.artifact_manifest["files"]
    assert files["runtime_config"] == "opencode_config.json"
    assert files["workspace_listing"] == "opencode_workspace_listing.txt"
    assert files["memory_summary"] == "memory/opencode.db.summary.json"
    assert "opencode.db" in result.workspace_file_contents["opencode_workspace_listing.txt"]
    assert "state/sessions/session-demo.jsonl" in result.workspace_file_contents
    assert "REDACTED" in result.workspace_file_contents["opencode_config.json"]
    assert "test-key" not in result.workspace_file_contents["opencode_config.json"]


def test_solve_in_container_preserves_runtime_workspace_artifacts():
    agent = OpenCodeAgent()
    agent.setup({
        "runtime": "swebench_container",
        "model_name": "Qwen/Qwen3.5-27B",
        "api_base": "http://172.17.0.1:8011/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    runtime_manager = MagicMock()
    runtime_manager.run_task.return_value = {
        "stdout": json.dumps({
            "type": "assistant",
            "sessionID": "session-demo",
            "message": {"content": "diff --git a/foo b/foo"},
        }) + "\n",
        "stderr": "stderr text\n",
        "exit_code": 0,
        "patch": "diff --git a/foo b/foo",
    }
    runtime_manager.collect_artifacts.return_value = {
        "artifact_manifest": {
            "files": {
                "workspace_listing": "opencode_workspace_listing.txt",
                "memory_summary": "memory/opencode.db.summary.json",
            }
        },
        "workspace_file_contents": {
            "opencode_workspace_listing.txt": "opencode.json\nsessions/session-demo.jsonl\n",
            "opencode_session.jsonl": '{"type":"message"}\n',
            "memory/opencode.db.summary.json": '{"tables":[{"name":"session","row_count":1}]}\n',
        },
        "workspace_snapshot_paths": ["/tmp/opencode-xdg/opencode/opencode.db"],
        "sandbox_metadata": {"sandbox_id": "sandbox-demo"},
    }
    agent._runtime_manager = runtime_manager

    sandbox = MagicMock()
    task = BenchmarkTask(
        task_id="swe_django__django-11790",
        problem="Fix the regression.",
        ground_truth="diff --git a/foo b/foo",
        metadata={
            "instance_id": "django__django-11790",
            "repo": "django/django",
            "base_commit": "deadbeef",
        },
    )

    response = agent.solve(task, sandbox=sandbox)

    assert response.workspace_file_contents["opencode_session.jsonl"]
    assert response.workspace_file_contents["memory/opencode.db.summary.json"]
    assert response.workspace_file_contents["opencode_output.jsonl"]
    assert response.workspace_file_contents["prompt.txt"].startswith("You are an expert software engineer.")
    assert response.artifact_manifest["files"]["memory_summary"] == "memory/opencode.db.summary.json"
    assert response.artifact_manifest["files"]["response_stream"] == "opencode_output.jsonl"
    assert response.workspace_snapshot_paths == ["/tmp/opencode-xdg/opencode/opencode.db"]


def test_setup_rejects_unknown_controller_mode():
    agent = OpenCodeAgent()

    with pytest.raises(ValueError, match="Unsupported opencode controller_mode"):
        agent.setup({
            "model_name": "test-model",
            "api_base": "http://localhost:9001/v1",
            "api_key": "test-key",
            "controller_mode": "doccker",
        })


def test_solve_text_only_uses_docker_runner_when_configured():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
        "controller_mode": "docker",
    })

    task = BenchmarkTask(
        task_id="text_0",
        problem="Which option is correct?",
        ground_truth="A",
    )

    with patch.object(
        agent,
        "_run_in_docker",
        return_value=('{"type":"assistant","message":{"content":"Done.\\\\boxed{A}"}}\n', "", 0),
    ) as run_in_docker:
        with patch("subprocess.Popen") as popen:
            result = agent.solve(task)

    run_in_docker.assert_called_once()
    popen.assert_not_called()
    assert result.answer == "A"
    assert result.metadata["controller_mode"] == "docker"
