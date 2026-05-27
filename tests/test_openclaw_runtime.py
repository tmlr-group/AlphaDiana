from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
import pytest

from alphadiana.agent.openclaw import (
    OpenClawAgent,
    _detect_session_pollution,
    _extract_answer,
    _extract_raw_output_from_payload,
    _recover_openclaw_session_artifacts,
)
from alphadiana.agent.openclaw_container_runtime import OpenClawContainerRuntimeManager
from alphadiana.agent.openclaw_runtime import OpenClawPodmanRuntimeManager, OpenClawRuntimeManager
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.container_runtime import PodmanAgentRuntimeResult


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


def test_openclaw_session_pollution_heuristic_matches_reasoning_token_markup() -> None:
    problem = (
        "A standard fair six-sided die is rolled repeatedly. Each time the die "
        "reads 1 or 2, Alice gets a coin; each time it reads 3 or 4, Bob gets "
        "a coin; and each time it reads 5 or 6, Carol gets a coin. The "
        "probability that Alice and Bob each receive at least two coins before "
        "Carol receives any coins can be written as $\\tfrac{m}{n}.$ Find 100m+n."
    )
    raw_reasoning = (
        "Reasoning: _A_ standard fair _six_-sided die is _rolled_ repeatedly. "
        "_Alice_ and Bob need to _receive_ two _coins_ before _Carol_ receives "
        "any coins. The _probability_ can be written with 100m+n. The same "
        "token markup may split words like bin_omial or numbers like 5_0_3."
    )

    assert _detect_session_pollution(problem, "", raw_reasoning) is False


def test_openclaw_session_pollution_heuristic_matches_split_math_tokens() -> None:
    problem = (
        "Let S_r be the sum of binomial coefficients binom(10000, 502n+r). "
        "Find how many values are divisible by 503."
    )
    raw_reasoning = (
        "Reasoning: _This_ is_ about_ bin_omial_ coefficients_ modulo_ "
        "5_0_3_._ The_ index_ has_ 5_0_2_n+r_ and_ total_ 1_0_0_0_0_."
    )

    assert _detect_session_pollution(problem, "", raw_reasoning) is False


def test_openclaw_provider_error_payload_is_not_answer_text() -> None:
    payload = {"error": {"message": "400 Bad Request: cost=0", "type": "BadRequestError"}}

    assert _extract_raw_output_from_payload(payload) == ""
    assert _extract_answer(json.dumps(payload)) == ""


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


def test_openclaw_runtime_defaults_blank_gateway_token(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    agent = OpenClawAgent()

    agent.setup({
        "gateway_token": "",
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "OPENAI_BASE_URL": "http://127.0.0.1:8011/v1",
        "OPENAI_API_KEY": "EMPTY",
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
    })

    assert agent._gateway_token == "OPENCLAW"
    assert isinstance(agent._runtime_manager, OpenClawRuntimeManager)
    assert agent._runtime_manager._gateway_token == "OPENCLAW"

    with patch.dict("os.environ", {}, clear=True):
        config_dir = agent._runtime_manager._prepare_runtime_files(MagicMock())

    generated = yaml.safe_load((config_dir / "rock_agent_config.yaml").read_text(encoding="utf-8"))
    assert generated["env"]["OPENCLAW_GATEWAY_TOKEN"] == "OPENCLAW"


def test_openclaw_runtime_gateway_probe_uses_resolved_default_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "gateway_token": "${OPENCLAW_GATEWAY_TOKEN}",
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
    })
    calls: list[dict] = []

    class _Sandbox:
        sandbox_id = "sandbox-token-default"

        def published_base(self, port: int) -> str:
            assert port == 8080
            return "http://127.0.0.1:18080"

    class _Response:
        status_code = 200

    def fake_get(url: str, *, headers: dict, timeout: float, trust_env: bool) -> _Response:
        calls.append({"url": url, "headers": headers, "timeout": timeout, "trust_env": trust_env})
        return _Response()

    monkeypatch.setattr("httpx.get", fake_get)

    mgr._wait_for_gateway(_Sandbox())

    assert calls
    assert calls[0]["headers"]["Authorization"] == "bearer OPENCLAW"


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


def test_openclaw_runtime_propagates_request_timeout_to_agent_and_exec(tmp_path: Path) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "request_timeout": 9300,
    })

    cfg = mgr._build_openclaw_config({
        "agents": {"defaults": {"timeoutSeconds": 3600}},
        "tools": {"exec": {"timeoutSec": 1800}},
    })

    assert cfg["agents"]["defaults"]["timeoutSeconds"] == 9300
    assert cfg["tools"]["exec"]["timeoutSec"] == 9300


def test_openclaw_runtime_patches_embedded_provider_stream_timeout(
    tmp_path: Path,
) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    rock_cfg.write_text(
        "\n".join([
            "name: test",
            "run_cmd: >-",
            "  mkdir -p /tmp/empty-bundled /tmp/oc_home &&",
            "  OPENCLAW_CONFIG_PATH=${working_dir}/openclaw.json nohup openclaw gateway &",
            "env:",
            '  OPENAI_BASE_URL: "${OPENAI_BASE_URL}"',
            '  OPENAI_API_KEY: "${OPENAI_API_KEY}"',
            '  OPENAI_MODEL_NAME: "${OPENAI_MODEL_NAME}"',
        ]),
        encoding="utf-8",
    )
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "OPENAI_BASE_URL": "http://127.0.0.1:8011/v1",
        "OPENAI_API_KEY": "EMPTY",
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
        "request_timeout": 9300,
    })

    with patch.dict("os.environ", {}, clear=True):
        config_dir = mgr._prepare_runtime_files(MagicMock())

    generated = yaml.safe_load((config_dir / "rock_agent_config.yaml").read_text(encoding="utf-8"))

    assert generated["agent_run_timeout"] == 9300
    assert generated["env"]["OPENCLAW_UNDICI_STREAM_TIMEOUT_MS"] == "9300000"
    assert "opts?.timeoutMs ?? 18e5" in generated["run_cmd"]
    assert (
        "opts?.timeoutMs ?? (Number(process.env.OPENCLAW_UNDICI_STREAM_TIMEOUT_MS || 9300000) || 18e5)"
        in generated["run_cmd"]
    )
    assert "9300000" in generated["run_cmd"]


def test_openclaw_runtime_does_not_duplicate_existing_undici_timeout_patch(
    tmp_path: Path,
) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    replacement = (
        "opts?.timeoutMs ?? "
        "(Number(process.env.OPENCLAW_UNDICI_STREAM_TIMEOUT_MS || 9300000) || 18e5)"
    )
    rock_cfg.write_text(
        "\n".join([
            "name: test",
            "run_cmd: >-",
            "  export OPENCLAW_UNDICI_STREAM_TIMEOUT_MS=${OPENCLAW_UNDICI_STREAM_TIMEOUT_MS:-9300000};",
            "  patch_file=\"$(grep -Rsl 'opts?.timeoutMs ?? 18e5' /app/dist 2>/dev/null | head -1 || true)\";",
            "  if [ -n \"$patch_file\" ]; then",
            "  sed -i \"s/const timeoutMsRaw = opts?.timeoutMs ?? 18e5;/const timeoutMsRaw = opts?.timeoutMs ?? (Number(process.env.OPENCLAW_UNDICI_STREAM_TIMEOUT_MS || 9300000) || 18e5);/\" \"$patch_file\";",
            "  fi;",
            "  OPENCLAW_CONFIG_PATH=${working_dir}/openclaw.json nohup openclaw gateway &",
            "env:",
            '  OPENAI_BASE_URL: "${OPENAI_BASE_URL}"',
            '  OPENAI_API_KEY: "${OPENAI_API_KEY}"',
            '  OPENAI_MODEL_NAME: "${OPENAI_MODEL_NAME}"',
        ]),
        encoding="utf-8",
    )
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "OPENAI_BASE_URL": "http://127.0.0.1:8011/v1",
        "OPENAI_API_KEY": "EMPTY",
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
        "request_timeout": 9300,
    })

    with patch.dict("os.environ", {}, clear=True):
        config_dir = mgr._prepare_runtime_files(MagicMock())

    generated = yaml.safe_load((config_dir / "rock_agent_config.yaml").read_text(encoding="utf-8"))

    assert generated["agent_run_timeout"] == 9300
    assert generated["run_cmd"].count(replacement) == 1


def test_openclaw_runtime_allows_explicit_agent_and_exec_timeout_overrides(
    tmp_path: Path,
) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "request_timeout": 9300,
        "openclaw_agent_timeout": 7200,
        "openclaw_exec_timeout": 8100,
    })

    cfg = mgr._build_openclaw_config({})

    assert cfg["agents"]["defaults"]["timeoutSeconds"] == 7200
    assert cfg["tools"]["exec"]["timeoutSec"] == 8100


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


def test_recover_openclaw_session_artifacts_truncates_post_answer_heartbeat() -> None:
    request = "solve demo"
    session_text = "\n".join([
        json.dumps({
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": request}],
            },
        }),
        json.dumps({
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "solve"},
                    {"type": "text", "text": "$$\\boxed{62}$$"},
                ],
            },
        }),
        json.dumps({
            "type": "message",
            "message": {
                "role": "user",
                "content": (
                    "System: Exec completed.\n\n"
                    "Read HEARTBEAT.md if it exists. If nothing needs attention, "
                    "reply HEARTBEAT_OK."
                ),
            },
        }),
        json.dumps({
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "HEARTBEAT_OK"}],
            },
        }),
    ])

    trajectory, reasoning, recovered_text = _recover_openclaw_session_artifacts(
        {"openclaw_session.jsonl": session_text},
        expected_user_content=request,
    )

    assert recovered_text == session_text
    assert [entry["role"] for entry in trajectory] == ["user", "assistant"]
    assert trajectory[1]["content"] == "$$\\boxed{62}$$"
    assert all("HEARTBEAT" not in json.dumps(entry) for entry in trajectory)
    assert all("HEARTBEAT" not in json.dumps(entry) for entry in reasoning)


def test_openclaw_sandbox_session_retrieval_discards_unmatched_fallback() -> None:
    agent = OpenClawAgent()
    session_text = "\n".join([
        json.dumps({
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "stale problem"}],
            },
        }),
        json.dumps({
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "stale answer"}],
            },
        }),
    ])

    class _Result:
        def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
            self.stdout = stdout
            self.exit_code = exit_code
            self.stderr = ""

    class _Sandbox:
        def execute(self, command: str) -> _Result:
            if "ls -t" in command and "sessions" in command:
                return _Result("/tmp/oc_home/.openclaw/agents/main/sessions/stale.jsonl\n")
            if command.startswith("cat ") and "stale.jsonl" in command:
                return _Result(session_text)
            raise AssertionError(command)

        def read_text(self, _path: str) -> str:
            raise RuntimeError("force cat fallback")

    trajectory = agent._retrieve_trajectory_from_sandbox_session(
        sandbox=_Sandbox(),
        expected_user_content="fresh problem",
    )

    assert trajectory == []


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


def test_openclaw_podman_runtime_collect_artifacts_mirrors_rock_aliases(tmp_path: Path) -> None:
    class _ExecResult:
        def __init__(self, stdout: str = "", returncode: int = 0) -> None:
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = ""

    session_path = "/tmp/oc_home/agents/main/sessions/abc.jsonl"
    workspace_output_path = "/tmp/oc_home/.openclaw/workspace/openclaw_output.jsonl"
    workspace_note_path = "/tmp/oc_home/.openclaw/workspace/note.txt"
    agents_path = "/tmp/oc_home/.openclaw/workspace/AGENTS.md"
    file_text = {
        session_path: (
            '{"role":"assistant","tool_calls":[{"name":"python","arguments":"print(1)"}]}\n'
            '{"role":"tool","tool_call_id":"x","content":"1\\n"}\n'
        ),
        workspace_output_path: '{"choices":[{"delta":{"content":"hi"}}]}\n',
        workspace_note_path: "a note\n",
        agents_path: "You are a solver.\n",
    }

    class _FakeRuntime:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def start(self, _spec) -> PodmanAgentRuntimeResult:
            return PodmanAgentRuntimeResult(
                container_id="cid",
                api_base="http://127.0.0.1:8080/v1",
                metadata={"image": "alphadiana-openclaw:latest"},
                logs="gateway boot log\n",
                config_snapshots={"/tmp/openclaw.json": '{"env":{"OPENAI_API_KEY":"REDACTED"}}\n'},
                artifacts={
                    "/tmp/gateway.log": "[gateway] started\n",
                    "/tmp/oc_home/agents/main/sessions.json": '{"latest":"abc"}\n',
                    "/tmp/oc_home/workspace-state.json": '{"active":"abc"}\n',
                },
            )

        def collect_artifacts(self) -> PodmanAgentRuntimeResult:
            return self.start(None)

        def cleanup(self, *, check: bool = False) -> None:
            pass

        def exec(
            self,
            command,
            *,
            env=None,
            workdir=None,
            timeout=None,
            check: bool = True,
        ) -> _ExecResult:
            shell = command[-1] if isinstance(command, list) and command else str(command)
            self.calls.append(shell)
            if "find " in shell and "-maxdepth" in shell:
                return _ExecResult(
                    f"{workspace_output_path}\n"
                    f"{workspace_note_path}\n"
                    f"{agents_path}\n"
                )
            if "/agents/main/sessions/*.jsonl" in shell:
                return _ExecResult(f"{session_path}\n")
            if shell.startswith("stat -c %s "):
                path = shell.split()[3]
                if path in file_text:
                    return _ExecResult(f"{len(file_text[path].encode('utf-8'))}\n")
                return _ExecResult("-1\n")
            if shell.startswith("cat "):
                path = shell.split()[1]
                return _ExecResult(file_text.get(path, ""))
            return _ExecResult("")

    fake_rt = _FakeRuntime()
    openclaw_cfg = tmp_path / "openclaw.json"
    gateway_cfg = tmp_path / "podman_gateway.yaml"
    openclaw_cfg.write_text('{"meta":{}}', encoding="utf-8")
    gateway_cfg.write_text(
        "image: alphadiana-openclaw:latest\n"
        "run_command: openclaw gateway\n"
        "exposed_port: 8080\n",
        encoding="utf-8",
    )
    mgr = OpenClawPodmanRuntimeManager({
        "openclaw_config_path": str(openclaw_cfg),
        "podman_gateway_config_path": str(gateway_cfg),
        "_podman_agent_runtime": fake_rt,
        "OPENAI_BASE_URL": "http://localhost:8011/v1",
        "OPENAI_API_KEY": "test",
        "OPENAI_MODEL_NAME": "test-model",
    })
    mgr._podman_result = fake_rt.start(None)

    artifacts = mgr.collect_artifacts(None)

    files = artifacts["artifact_manifest"]["files"]
    contents = artifacts["workspace_file_contents"]

    assert files["gateway_log"] == "openclaw_gateway.log"
    assert files["workspace_listing"] == "openclaw_workspace_listing.txt"
    assert files["openclaw_session"] == "openclaw_session.jsonl"
    assert files["session_trace"] == "openclaw_session.jsonl"
    assert files["openclaw_output"] == "openclaw_output.jsonl"
    assert files["response_stream"] == "openclaw_output.jsonl"
    assert files["runtime_config"] == "openclaw_runtime_config.json"
    assert files["workspace_state"] == "openclaw_workspace_state.json"
    assert files["session_index"] == "openclaw_sessions_index.json"
    assert files["system_prompt"] == agents_path
    assert "tool_calls" in contents["openclaw_session.jsonl"]
    assert "tool_call_id" in contents["openclaw_session.jsonl"]
    assert "openclaw_output.jsonl" in contents["openclaw_workspace_listing.txt"]
    assert "note.txt" in contents["openclaw_workspace_listing.txt"]
    assert contents["openclaw_workspace_state.json"] == '{"active":"abc"}\n'
    assert contents["openclaw_sessions_index.json"] == '{"latest":"abc"}\n'


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
