from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

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
