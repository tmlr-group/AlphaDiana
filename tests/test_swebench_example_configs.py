from __future__ import annotations

from pathlib import Path

import pytest

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator

EXAMPLE_CONFIGS = [
    Path("configs/examples/swebench_pro_direct_llm_smoke.local.yaml"),
    Path("configs/examples/swebench_pro_openclaw_smoke.local.yaml"),
    Path("configs/examples/swebench_pro_opencode_smoke.local.yaml"),
]
FULL_RUN_CONFIGS = [
    Path("configs/full_runs/p29_full_openclaw_swebench_pro.yaml"),
    Path("configs/full_runs/p29_full_opencode_swebench_pro.yaml"),
]


def _set_swebench_pro_env(monkeypatch, eval_script: Path, scripts_dir: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "demo-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "demo-model")
    monkeypatch.setenv("OPENCLAW_SMOKE_MODEL_NAME", "demo-openclaw-model")
    monkeypatch.setenv("OPENCLAW_AGENT_ID", "main")
    monkeypatch.setenv("OPENCLAW_TOOLS_PROFILE", "coding")
    monkeypatch.setenv("OPENCLAW_REQUIRE_PATCH", "1")
    monkeypatch.setenv("OPENCLAW_SMOKE_MODEL_CANDIDATES", "demo-openclaw-model,demo-openclaw-fallback")
    monkeypatch.setenv("OPENCLAW_PROMPT_PROFILE", "edit_first")
    monkeypatch.setenv("OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS", "12000")
    monkeypatch.setenv("OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT", "20")
    monkeypatch.setenv("OPENCLAW_MAX_NO_EDIT_SECONDS", "300")
    monkeypatch.setenv("OPENCLAW_CONTEXT_WINDOW", "200000")
    monkeypatch.setenv("OPENCLAW_COMPLETION_MAX_TOKENS", "4096")
    monkeypatch.setenv("OPENCLAW_FULL_MODEL_NAME", "demo-openclaw-model")
    monkeypatch.setenv("OPENCLAW_FULL_MODEL_CANDIDATES", "demo-openclaw-model,demo-openclaw-fallback")
    monkeypatch.setenv("OPENCODE_SMOKE_MODEL_NAME", "demo-opencode-model")
    monkeypatch.setenv("OPENCODE_SMOKE_MODEL_CANDIDATES", "demo-opencode-model,demo-opencode-fallback")
    monkeypatch.setenv("OPENCODE_STRATEGY_SEQUENCE", "bash_edit_first,guided_edit_first,edit_first")
    monkeypatch.setenv("OPENCODE_REQUIRE_PATCH", "1")
    monkeypatch.setenv("OPENCODE_PROMPT_PROFILE", "edit_first")
    monkeypatch.setenv("OPENCODE_AUTO_TARGET_HINTS", "1")
    monkeypatch.setenv("OPENCODE_TARGET_FILE_HINTS", "src/app.py,src/db.py")
    monkeypatch.setenv("OPENCODE_PRIMARY_TARGET_FILE", "src/app.py")
    monkeypatch.setenv("OPENCODE_PROBLEM_STATEMENT_MAX_CHARS", "12000")
    monkeypatch.setenv("OPENCODE_PREFLIGHT_TIMEOUT_SEC", "45")
    monkeypatch.setenv("OPENCODE_STARTUP_TIMEOUT_SEC", "90")
    monkeypatch.setenv("OPENCODE_IDLE_TIMEOUT_SEC", "900")
    monkeypatch.setenv("OPENCODE_IDLE_POLL_SEC", "15")
    monkeypatch.setenv("OPENCODE_MAX_ACTIVE_NO_EDIT_SEC", "600")
    monkeypatch.setenv("OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT", "40")
    monkeypatch.setenv("OPENCODE_ACTIVITY_HEARTBEAT_SEC", "30")
    monkeypatch.setenv("OPENCODE_FULL_MODEL_NAME", "demo-opencode-model")
    monkeypatch.setenv("OPENCODE_FULL_MODEL_CANDIDATES", "demo-opencode-model,demo-opencode-fallback")
    monkeypatch.setenv("SWE_BENCH_PRO_EVAL_SCRIPT", str(eval_script))
    monkeypatch.setenv("SWE_BENCH_PRO_SCRIPTS_DIR", str(scripts_dir))


@pytest.mark.parametrize("config_path", EXAMPLE_CONFIGS, ids=[path.stem for path in EXAMPLE_CONFIGS])
def test_example_configs_load_and_validate_with_env(monkeypatch, tmp_path, config_path):
    eval_script = tmp_path / "swe_bench_pro_eval.py"
    eval_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    scripts_dir = tmp_path / "run_scripts"
    scripts_dir.mkdir()

    _set_swebench_pro_env(monkeypatch, eval_script, scripts_dir)

    config = ExperimentConfig.from_yaml(str(config_path))
    validated = ConfigValidator().load_and_validate(str(config_path))

    assert config.benchmark_name == "swebench_pro_os"
    assert config.scorer_name == "swebench_pro"
    assert validated.benchmark_name == "swebench_pro_os"
    assert validated.scorer_name == "swebench_pro"


@pytest.mark.parametrize("config_path", FULL_RUN_CONFIGS, ids=[path.stem for path in FULL_RUN_CONFIGS])
def test_full_run_configs_load_and_validate_with_env(monkeypatch, tmp_path, config_path):
    eval_script = tmp_path / "swe_bench_pro_eval.py"
    eval_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    scripts_dir = tmp_path / "run_scripts"
    scripts_dir.mkdir()

    _set_swebench_pro_env(monkeypatch, eval_script, scripts_dir)

    config = ExperimentConfig.from_yaml(str(config_path))
    validated = ConfigValidator().load_and_validate(str(config_path))

    assert config.benchmark_name == "swebench_pro_os"
    assert config.scorer_name == "swebench_pro"
    assert validated.benchmark_name == "swebench_pro_os"
    assert validated.scorer_name == "swebench_pro"
    assert validated.benchmark_config["subset"] == "all"


@pytest.mark.parametrize("config_path", EXAMPLE_CONFIGS, ids=[path.stem for path in EXAMPLE_CONFIGS])
def test_example_configs_keep_env_expansion_and_do_not_embed_secrets(config_path):
    text = config_path.read_text(encoding="utf-8")

    assert "${OPENAI_API_KEY}" in text
    assert "${OPENAI_BASE_URL}" in text
    if "openclaw" in config_path.name:
        assert "${OPENCLAW_SMOKE_MODEL_NAME}" in text
        assert "${OPENCLAW_AGENT_ID}" in text
        assert "${OPENCLAW_TOOLS_PROFILE}" in text
        assert "${OPENCLAW_REQUIRE_PATCH}" in text
        assert "${OPENCLAW_SMOKE_MODEL_CANDIDATES}" in text
        assert "${OPENCLAW_PROMPT_PROFILE}" in text
        assert "${OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS}" in text
        assert "${OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT}" in text
        assert "${OPENCLAW_MAX_NO_EDIT_SECONDS}" in text
        assert "${OPENCLAW_CONTEXT_WINDOW}" in text
        assert "${OPENCLAW_COMPLETION_MAX_TOKENS}" in text
    elif "opencode" in config_path.name:
        assert "${OPENCODE_SMOKE_MODEL_NAME}" in text
        assert "${OPENCODE_SMOKE_MODEL_CANDIDATES}" in text
        assert "${OPENCODE_STRATEGY_SEQUENCE}" in text
        assert "${OPENCODE_REQUIRE_PATCH}" in text
        assert "${OPENCODE_PROMPT_PROFILE}" in text
        assert "${OPENCODE_AUTO_TARGET_HINTS}" in text
        assert "${OPENCODE_TARGET_FILE_HINTS}" in text
        assert "${OPENCODE_PRIMARY_TARGET_FILE}" in text
        assert "${OPENCODE_PROBLEM_STATEMENT_MAX_CHARS}" in text
        assert "${OPENCODE_PREFLIGHT_TIMEOUT_SEC}" in text
        assert "${OPENCODE_STARTUP_TIMEOUT_SEC}" in text
        assert "${OPENCODE_IDLE_TIMEOUT_SEC}" in text
        assert "${OPENCODE_IDLE_POLL_SEC}" in text
        assert "${OPENCODE_MAX_ACTIVE_NO_EDIT_SEC}" in text
        assert "${OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT}" in text
        assert "${OPENCODE_ACTIVITY_HEARTBEAT_SEC}" in text
    else:
        assert "${OPENAI_MODEL_NAME}" in text
    assert "demo-key" not in text
    assert "sk-" not in text


@pytest.mark.parametrize("config_path", FULL_RUN_CONFIGS, ids=[path.stem for path in FULL_RUN_CONFIGS])
def test_full_run_configs_keep_env_expansion_and_do_not_embed_secrets(config_path):
    text = config_path.read_text(encoding="utf-8")

    assert "${OPENAI_API_KEY}" in text
    assert "${OPENAI_BASE_URL}" in text
    assert "${SWE_BENCH_PRO_EVAL_SCRIPT}" in text
    assert "${SWE_BENCH_PRO_SCRIPTS_DIR}" in text
    if "openclaw" in config_path.name:
        assert "${OPENCLAW_FULL_MODEL_NAME}" in text
        assert "${OPENCLAW_FULL_MODEL_CANDIDATES}" in text
        assert "${OPENCLAW_AGENT_ID}" in text
        assert "${OPENCLAW_TOOLS_PROFILE}" in text
        assert "${OPENCLAW_REQUIRE_PATCH}" in text
        assert "${OPENCLAW_CONTEXT_WINDOW}" in text
        assert "${OPENCLAW_COMPLETION_MAX_TOKENS}" in text
    else:
        assert "${OPENCODE_FULL_MODEL_NAME}" in text
        assert "${OPENCODE_FULL_MODEL_CANDIDATES}" in text
        assert "${OPENCODE_STRATEGY_SEQUENCE}" in text
        assert "${OPENCODE_REQUIRE_PATCH}" in text
        assert "${OPENCODE_TARGET_FILE_HINTS}" in text
        assert "${OPENCODE_PRIMARY_TARGET_FILE}" in text
    assert "demo-key" not in text
    assert "sk-" not in text
