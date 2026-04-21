from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
import pytest

from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager


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


@pytest.mark.parametrize(
    ("gateway_warmup_enabled", "expected_warmup_calls"),
    [
        (False, 0),
        (True, 1),
    ],
)
def test_openclaw_runtime_warmup_is_opt_in(
    tmp_path: Path,
    gateway_warmup_enabled: bool,
    expected_warmup_calls: int,
) -> None:
    rock_cfg, openclaw_cfg = _write_runtime_templates(tmp_path)
    mgr = OpenClawRuntimeManager({
        "rock_agent_config_path": str(rock_cfg),
        "openclaw_config_path": str(openclaw_cfg),
        "gateway_warmup_enabled": gateway_warmup_enabled,
    })
    sandbox = MagicMock()
    sandbox.sandbox_id = "sandbox-1"

    with (
        patch.object(mgr, "_prepare_runtime_files", return_value=tmp_path),
        patch.object(mgr, "_wait_for_gateway"),
        patch.object(mgr, "_warmup_gateway") as warmup_mock,
        patch.object(mgr, "runtime_info", return_value={"sandbox_id": "sandbox-1", "api_base": "http://sandbox/v1", "gateway_url": "http://sandbox/v1/chat/completions", "gateway_token": "OPENCLAW"}),
    ):
        mgr.ensure_ready(sandbox)

    assert warmup_mock.call_count == expected_warmup_calls
