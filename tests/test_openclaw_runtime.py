from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
import pytest

from alphadiana.agent.openclaw import OpenClawAgent, _detect_session_pollution
from alphadiana.agent.openclaw_container_runtime import OpenClawContainerRuntimeManager
from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager
from alphadiana.benchmark.base import BenchmarkTask


def _write_runtime_templates(tmp_path: Path) -> tuple[Path, Path]:
    rock_cfg = tmp_path / "rock_agent_config.yaml"
    rock_cfg.write_text(
        "\n".join([
            "name: test",
            "env:",
            '  OPENAI_BASE_URL: "${OPENAI_BASE_URL}"',
            '  OPENAI_API_KEY: "${OPENAI_API_KEY}"',
            '  OPENAI_MODEL_NAME: "${OPENAI_MODEL_NAME}"',
        ]),
        encoding="utf-8",
    )
    openclaw_cfg = tmp_path / "openclaw.json"
    openclaw_cfg.write_text(json.dumps({"models": {"providers": {}}}), encoding="utf-8")
    return rock_cfg, openclaw_cfg


def test_openclaw_session_pollution_heuristic_allows_formula_rewrites() -> None:
    problem = (
        "For a given positive integer N, Henry writes the quotient of ab divided "
        "by N+1 on the board for each pair. Find all N such that the sum is "
        "(N^3-N^2+2)/4."
    )
    raw_output = (
        "Let S = sum_{a=1}^N sum_{b=1}^N floor(ab/(N+1)). "
        "Checking and simplifying the condition gives the only solution "
        "$$\\boxed{3}$$."
    )

    assert _detect_session_pollution(problem, raw_output, "") is False


def test_openclaw_session_pollution_heuristic_flags_near_total_mismatch() -> None:
    problem = (
        "For a given positive integer N, Henry writes the quotient of ab divided "
        "by N+1 on the board for each pair. Find all N."
    )
    raw_output = (
        "The mitochondrion is the organelle responsible for cellular respiration. "
        "The historical event occurred in Paris in 1789."
    )

    assert _detect_session_pollution(problem, raw_output, "") is True


def test_openclaw_runtime_uses_agent_config_provider_env(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "OPENAI_BASE_URL": "https://models.sjtu.edu.cn/api/v1",
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_MODEL_NAME": "minimax-m2.5",
    })

    with patch.dict("os.environ", {}, clear=True):
        config_dir = mgr._prepare_runtime_files(MagicMock())

    generated = yaml.safe_load((config_dir / "rock_agent_config.yaml").read_text(encoding="utf-8"))
    assert generated["env"]["OPENAI_BASE_URL"] == "https://models.sjtu.edu.cn/api/v1"
    assert generated["env"]["OPENAI_API_KEY"] == "sk-test"
    assert generated["env"]["OPENAI_MODEL_NAME"] == "minimax-m2.5"


def test_openclaw_runtime_accepts_lowercase_provider_overrides(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "openai_base_url": "http://127.0.0.1:8011/v1",
        "openai_api_key": "LOCAL_DUMMY",
        "openai_model_name": "Qwen/Qwen3.5-27B",
    })

    with patch.dict("os.environ", {
        "OPENAI_BASE_URL": "https://models.sjtu.edu.cn/api/v1",
        "OPENAI_API_KEY": "sk-remote",
        "OPENAI_MODEL_NAME": "minimax-m2.5",
    }, clear=True):
        config_dir = mgr._prepare_runtime_files(MagicMock())

    generated = yaml.safe_load((config_dir / "rock_agent_config.yaml").read_text(encoding="utf-8"))
    assert generated["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:8011/v1"
    assert generated["env"]["OPENAI_API_KEY"] == "LOCAL_DUMMY"
    assert generated["env"]["OPENAI_MODEL_NAME"] == "Qwen/Qwen3.5-27B"


def test_openclaw_runtime_rejects_unresolved_placeholder_provider_env(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "OPENAI_BASE_URL": "${OPENAI_BASE_URL}",
        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
        "OPENAI_MODEL_NAME": "${OPENAI_MODEL_NAME}",
    })

    with patch.dict("os.environ", {}, clear=True):
        try:
            mgr._prepare_runtime_files(MagicMock())
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected unresolved provider env placeholders to fail")

    assert "OPENAI_BASE_URL" in message
    assert "agent.config or the local environment" in message


def test_openclaw_runtime_applies_planner_only_overrides(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "tools_profile": "minimal",
        "skip_bootstrap": True,
        "system_prompt_override": "Return shell commands only.",
    })

    cfg = mgr._build_openclaw_config()

    assert cfg["tools"]["profile"] == "minimal"
    assert cfg["agents"]["defaults"]["skipBootstrap"] is True
    assert cfg["agents"]["defaults"]["systemPromptOverride"] == "Return shell commands only."


def test_openclaw_runtime_marks_configured_model_as_image_capable(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    openclaw_cfg.write_text(json.dumps({
        "models": {
            "providers": {
                "local": {
                    "models": [
                        {
                            "id": "${OPENAI_MODEL_NAME}",
                            "name": "${OPENAI_MODEL_NAME}",
                        }
                    ]
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {"primary": "local/${OPENAI_MODEL_NAME}"},
                "models": {
                    "local/${OPENAI_MODEL_NAME}": {
                        "params": {"maxTokens": 32768},
                    }
                },
            }
        },
    }), encoding="utf-8")
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
    })

    cfg = mgr._build_openclaw_config(json.loads(openclaw_cfg.read_text(encoding="utf-8")))

    provider_model = cfg["models"]["providers"]["local"]["models"][0]
    assert provider_model["input"] == ["text", "image"]
    resolved_model = cfg["agents"]["defaults"]["models"]["local/${OPENAI_MODEL_NAME}"]
    assert resolved_model["params"]["maxTokens"] == 32768


def test_openclaw_runtime_preserves_existing_input_capabilities_without_duplicates(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
    })

    cfg = mgr._build_openclaw_config({
        "models": {
            "providers": {
                "local": {
                    "models": [
                        {
                            "id": "Qwen/Qwen3.5-27B",
                            "name": "Qwen/Qwen3.5-27B",
                            "input": ["text", "image", "image"],
                        }
                    ]
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {"primary": "local/Qwen/Qwen3.5-27B"},
                "models": {
                    "local/Qwen/Qwen3.5-27B": {
                        "params": {"maxTokens": 1024},
                    }
                },
            }
        },
    })

    provider_model = cfg["models"]["providers"]["local"]["models"][0]
    assert provider_model["input"] == ["text", "image"]
    resolved_model = cfg["agents"]["defaults"]["models"]["local/Qwen/Qwen3.5-27B"]
    assert resolved_model == {"params": {"maxTokens": 1024}}


def test_openclaw_runtime_applies_generation_param_overrides(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    openclaw_cfg.write_text(json.dumps({
        "models": {
            "providers": {
                "local": {
                    "models": [
                        {
                            "id": "${OPENAI_MODEL_NAME}",
                            "name": "${OPENAI_MODEL_NAME}",
                        }
                    ]
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {"primary": "local/${OPENAI_MODEL_NAME}"},
                "models": {
                    "local/${OPENAI_MODEL_NAME}": {
                        "params": {"maxTokens": 32768},
                    }
                },
            }
        },
    }), encoding="utf-8")
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": 65536,
    })

    cfg = mgr._build_openclaw_config(json.loads(openclaw_cfg.read_text(encoding="utf-8")))

    params = cfg["agents"]["defaults"]["models"]["local/${OPENAI_MODEL_NAME}"]["params"]
    assert params["temperature"] == 0.0
    assert params["topP"] == 0.95
    assert params["maxTokens"] == 65536


def test_openclaw_runtime_collect_artifacts_preserves_session_trace_and_response_stream_aliases() -> None:
    mgr = OpenClawRuntimeManager({})

    class _Result:
        def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
            self.stdout = stdout
            self.exit_code = exit_code
            self.stderr = ""

    class _Sandbox:
        sandbox_id = "sandbox-demo"

        def __init__(self) -> None:
            self.command_history = []

        def execute(self, command: str) -> _Result:
            self.command_history.append({
                "command": command,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "wall_time_sec": 0.01,
            })
            if "find " in command and "sort -u" in command:
                return _Result(
                    "/tmp/oc_home/.openclaw/workspace/openclaw_output.jsonl\n"
                    "/tmp/oc_home/.openclaw/workspace/AGENTS.md\n"
                )
            if "ls -t" in command and "sessions" in command:
                return _Result("/tmp/oc_home/.openclaw/agents/main/sessions/demo-session.jsonl\n")
            if command.startswith("cat ") and "demo-session.jsonl" in command:
                return _Result(
                    '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"demo prompt"}]}}\n'
                    '{"type":"message","message":{"role":"assistant","content":[{"type":"thinking","thinking":"step"},{"type":"text","text":"done"}]}}\n'
                )
            return _Result()

        def read_text(self, path: str) -> str:
            if path.endswith("openclaw_output.jsonl"):
                return '{"choices":[{"delta":{"content":"done"}}]}\n'
            if path.endswith("AGENTS.md"):
                return "You are a solver."
            if path.endswith("workspace-state.json"):
                return '{"active":"session-demo"}\n'
            if path.endswith("sessions.json"):
                return '{"latest":"session-demo"}\n'
            if path == "/tmp/openclaw.json":
                return '{"env":{"OPENAI_API_KEY":"secret"}}\n'
            if path.endswith("demo-session.jsonl"):
                raise RuntimeError("fallback to cat")
            raise FileNotFoundError(path)

        def metadata(self) -> dict:
            return {"sandbox_id": self.sandbox_id}

    artifacts = mgr.collect_artifacts(_Sandbox())

    files = artifacts["artifact_manifest"]["files"]
    assert files["response_stream"] == "openclaw_output.jsonl"
    assert files["session_trace"] == "openclaw_session.jsonl"
    assert files["workspace_listing"] == "openclaw_workspace_listing.txt"
    assert files["workspace_state"] == "openclaw_workspace_state.json"
    assert files["session_index"] == "openclaw_sessions_index.json"
    assert files["runtime_config"] == "openclaw_runtime_config.json"
    assert artifacts["workspace_file_contents"]["openclaw_output.jsonl"]
    assert artifacts["workspace_file_contents"]["openclaw_session.jsonl"]
    assert artifacts["workspace_file_contents"]["openclaw_workspace_state.json"] == '{"active":"session-demo"}\n'
    assert artifacts["workspace_file_contents"]["openclaw_sessions_index.json"] == '{"latest":"session-demo"}\n'
    assert "REDACTED" in artifacts["workspace_file_contents"]["openclaw_runtime_config.json"]
    assert "/tmp/oc_home/.openclaw/workspace/openclaw_output.jsonl" in artifacts["workspace_snapshot_paths"]


def test_recover_openclaw_session_artifacts_prefers_saved_session_trace() -> None:
    from alphadiana.agent.openclaw import _recover_openclaw_session_artifacts

    session_text = "\n".join([
        json.dumps({
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "solve demo"}],
            },
        }),
        json.dumps({
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "inspect first"},
                    {"type": "toolCall", "name": "ls", "arguments": {"path": "."}},
                    {"type": "text", "text": "done"},
                ],
            },
        }),
    ])

    trajectory, reasoning, recovered_text = _recover_openclaw_session_artifacts(
        {"openclaw_session.jsonl": session_text},
        expected_user_content="solve demo",
    )

    assert recovered_text == session_text
    assert len(trajectory) == 2
    assert trajectory[1]["thinking"] == "inspect first"
    assert trajectory[1]["tool_calls"][0]["tool"] == "ls"
    assert reasoning[0]["type"] == "reasoning"
    assert reasoning[1]["type"] == "tool_call"


def test_openclaw_container_runtime_collect_artifacts_exposes_stable_aliases() -> None:
    mgr = OpenClawContainerRuntimeManager({"openclaw_config_path": "openclaw_deploy/openclaw_swe_bench.runtime.json"})

    class _Result:
        def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
            self.stdout = stdout
            self.exit_code = exit_code
            self.stderr = ""

    class _Sandbox:
        sandbox_id = "container-sandbox"

        def execute(self, command: str) -> _Result:
            if "ls -t" in command and "sessions" in command:
                return _Result("/tmp/oc_home/.openclaw/agents/main/sessions/demo-session.jsonl\n")
            if command.startswith("cat ") and "demo-session.jsonl" in command:
                return _Result('{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}\n')
            return _Result()

        def read_text(self, path: str) -> str:
            if path.endswith("openclaw_output.jsonl"):
                return '{"choices":[{"delta":{"content":"done"}}]}\n'
            if path.endswith("AGENTS.md"):
                return "You are a solver."
            if path.endswith("workspace-state.json"):
                return '{"active":"session-demo"}\n'
            if path.endswith("sessions.json"):
                return '{"latest":"session-demo"}\n'
            if path == "/tmp/openclaw.json":
                return '{"env":{"OPENAI_API_KEY":"secret"}}\n'
            if path.endswith("demo-session.jsonl"):
                raise RuntimeError("fallback to cat")
            raise FileNotFoundError(path)

        def metadata(self) -> dict:
            return {"repo_workdir": "/testbed"}

    mgr._list_workspace_paths = lambda _sandbox, _root: [  # type: ignore[method-assign]
        "/testbed/openclaw_output.jsonl",
        "/testbed/AGENTS.md",
    ]

    artifacts = mgr.collect_artifacts(_Sandbox())

    files = artifacts["artifact_manifest"]["files"]
    assert files["workspace_root"] == "/testbed"
    assert files["response_stream"] == "openclaw_output.jsonl"
    assert files["session_trace"] == "openclaw_session.jsonl"
    assert files["openclaw_output"] == "openclaw_output.jsonl"
    assert files["openclaw_session"] == "openclaw_session.jsonl"
    assert files["system_prompt"] == "/testbed/AGENTS.md"
    assert files["workspace_listing"] == "openclaw_workspace_listing.txt"
    assert files["workspace_state"] == "openclaw_workspace_state.json"
    assert files["session_index"] == "openclaw_sessions_index.json"
    assert files["runtime_config"] == "openclaw_runtime_config.json"
    assert artifacts["workspace_file_contents"]["openclaw_output.jsonl"]
    assert artifacts["workspace_file_contents"]["openclaw_session.jsonl"]
    assert artifacts["workspace_file_contents"]["openclaw_workspace_state.json"] == '{"active":"session-demo"}\n'
    assert artifacts["workspace_file_contents"]["openclaw_sessions_index.json"] == '{"latest":"session-demo"}\n'
    assert "REDACTED" in artifacts["workspace_file_contents"]["openclaw_runtime_config.json"]


def test_openclaw_container_runtime_applies_max_tokens_override_to_rendered_config(tmp_path: Path) -> None:
    runtime_cfg = tmp_path / "openclaw.json"
    runtime_cfg.write_text(json.dumps({
        "models": {
            "providers": {
                "local": {
                    "models": [
                        {
                            "id": "${OPENAI_MODEL_NAME}",
                            "name": "${OPENAI_MODEL_NAME}",
                        }
                    ]
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {"primary": "local/${OPENAI_MODEL_NAME}"},
                "models": {
                    "local/${OPENAI_MODEL_NAME}": {
                        "params": {"maxTokens": 32768},
                    }
                },
            }
        },
    }), encoding="utf-8")

    mgr = OpenClawContainerRuntimeManager({
        "openclaw_config_path": str(runtime_cfg),
        "OPENAI_BASE_URL": "http://127.0.0.1:8011/v1",
        "OPENAI_API_KEY": "LOCAL_DUMMY",
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
        "max_tokens": 4096,
    })

    cfg = mgr._build_openclaw_config(json.loads(runtime_cfg.read_text(encoding="utf-8")))

    assert cfg["agents"]["defaults"]["models"]["local/${OPENAI_MODEL_NAME}"]["params"]["maxTokens"] == 4096


def test_openclaw_container_runtime_binds_gateway_for_published_host_access(tmp_path: Path) -> None:
    runtime_cfg = tmp_path / "openclaw.json"
    runtime_cfg.write_text(json.dumps({}), encoding="utf-8")

    mgr = OpenClawContainerRuntimeManager({
        "openclaw_config_path": str(runtime_cfg),
        "OPENAI_BASE_URL": "http://127.0.0.1:8011/v1",
        "OPENAI_API_KEY": "LOCAL_DUMMY",
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
    })

    cfg = mgr._build_openclaw_config(json.loads(runtime_cfg.read_text(encoding="utf-8")))

    assert cfg["gateway"]["bind"] == "lan"
    assert "customBindHost" not in cfg["gateway"]


def test_openclaw_container_runtime_stages_offline_payload_via_docker_cp(tmp_path: Path) -> None:
    runtime_cfg = tmp_path / "openclaw.json"
    runtime_cfg.write_text(json.dumps({}), encoding="utf-8")
    payload_dir = tmp_path / "offline-openclaw"
    (payload_dir / "dist").mkdir(parents=True)
    (payload_dir / "node_modules").mkdir()
    (payload_dir / "dist" / "index.js").write_text("console.log('index');", encoding="utf-8")

    mgr = OpenClawContainerRuntimeManager({
        "openclaw_config_path": str(runtime_cfg),
    })

    class _Result:
        def __init__(self, exit_code: int = 0) -> None:
            self.exit_code = exit_code
            self.stdout = ""
            self.stderr = ""

    class _Sandbox:
        sandbox_id = "sandbox-1"

        def __init__(self) -> None:
            self.commands: list[str] = []

        def execute(self, command: str) -> _Result:
            self.commands.append(command)
            return _Result()

        def metadata(self) -> dict:
            return {"container_name": "sandbox-container"}

    sandbox = _Sandbox()

    with patch("alphadiana.agent.openclaw_container_runtime._ensure_offline_openclaw_payload", return_value=payload_dir):
        with patch("alphadiana.agent.openclaw_container_runtime.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
            assert mgr._stage_offline_openclaw_payload(sandbox) is True

    docker_cp_calls = [
        call.args[0]
        for call in mock_run.call_args_list
        if call.args and isinstance(call.args[0], list) and call.args[0][:2] == ["docker", "cp"]
    ]
    assert docker_cp_calls == [["docker", "cp", f"{payload_dir}/.", "sandbox-container:/tmp/openclaw-offline"]]
    assert any("/tmp/openclaw-offline" in command for command in sandbox.commands)


def test_openclaw_solve_preserves_request_and_selected_response_aliases() -> None:
    agent = OpenClawAgent()
    agent.setup({
        "api_base": "http://127.0.0.1:9999/v1",
        "max_attempts": 1,
        "request_timeout": 5,
    })
    task = BenchmarkTask(task_id="openclaw-demo", problem="What is 1+1?", ground_truth="2")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/event-stream"}
    mock_response.iter_lines = MagicMock(return_value=[
        'data: {"choices":[{"delta":{"content":"2"}}]}',
        'data: [DONE]',
    ])
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_response)
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch.object(agent, "_health_check", return_value=None):
        with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
            with patch("httpx.Client", return_value=mock_client):
                response = agent.solve(task)

    files = response.artifact_manifest["files"]
    assert files["request_payload"] == "openclaw_request_payload.json"
    assert files["selected_response"] == "openclaw_selected_response.json"
    request_payload = json.loads(response.workspace_file_contents["openclaw_request_payload.json"])
    selected_response = json.loads(response.workspace_file_contents["openclaw_selected_response.json"])
    assert request_payload["messages"][0]["content"] == "What is 1+1?"
    assert selected_response["choices"][0]["message"]["content"] == "2"
