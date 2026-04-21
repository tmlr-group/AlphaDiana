from __future__ import annotations

from pathlib import Path

import pytest

from alphadiana.benchmark_rollout_cli import main as rollout_cli_main
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator
from alphadiana.utils import rollout_campaign as rc

MANIFEST_PATH = Path("configs/full_runs/rollout_local_vllm_campaign_20260419.yaml")
NEW_FULL_CONFIGS = [
    Path("configs/full_runs/rollout_full_directllm_gpqa_diamond.yaml"),
    Path("configs/full_runs/rollout_full_openclaw_gpqa_diamond.yaml"),
    Path("configs/full_runs/rollout_full_opencode_gpqa_diamond.yaml"),
    Path("configs/full_runs/rollout_full_zeroclaw_gpqa_diamond.yaml"),
    Path("configs/full_runs/rollout_full_directllm_mmmu_pro_vision.yaml"),
    Path("configs/full_runs/rollout_full_openclaw_mmmu_pro_vision.yaml"),
    Path("configs/full_runs/rollout_full_opencode_mmmu_pro_vision.yaml"),
    Path("configs/full_runs/rollout_full_zeroclaw_mmmu_pro_vision.yaml"),
]


def _pick_run(*, benchmark: str, harness: str, model: str):
    runs = rc.filter_runs(
        rc.expand_campaign(MANIFEST_PATH),
        benchmark=benchmark,
        harness=harness,
        model=model,
    )
    assert len(runs) == 1
    return runs[0]


def _set_rollout_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "demo-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:18080/v1")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "demo-model")
    monkeypatch.setenv("ROCK_BASE_URL", "http://127.0.0.1:9000")
    monkeypatch.setenv("ROCK_PROXY_URL", "http://127.0.0.1:9005")


def _mock_successful_docker_image_inspect(monkeypatch) -> None:
    class _CompletedProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    monkeypatch.setattr(
        "alphadiana.config.validator.subprocess.run",
        lambda *args, **kwargs: _CompletedProcess(),
    )


def test_expand_campaign_has_expected_size_and_sample_classification() -> None:
    runs = rc.expand_campaign(MANIFEST_PATH)

    assert len(runs) == 60

    mainline = _pick_run(benchmark="imo_answerbench", harness="openclaw", model="qwen35_27b")
    assert mainline.wave == "wave_a_mainline"
    assert mainline.risk == "mainline"

    official = _pick_run(benchmark="terminal_bench2", harness="direct_llm", model="qwen35_27b")
    assert official.wave == "wave_b_official"
    assert official.risk == "official_checkout"

    blocked = _pick_run(benchmark="terminal_bench2", harness="openclaw", model="qwen35_27b")
    assert blocked.wave == "wave_d_blocked"
    assert blocked.risk == "blocked_but_requested"

    high_risk = _pick_run(benchmark="hle", harness="direct_llm", model="nemotron_30b_a3b")
    assert high_risk.wave == "wave_c_high_risk"
    assert high_risk.risk == "high_risk"

    summary = rc.summarize_runs(runs)
    assert summary["waves"] == {
        "wave_a_mainline": 46,
        "wave_b_official": 3,
        "wave_c_high_risk": 8,
        "wave_d_blocked": 3,
    }


def test_render_alphadiana_run_command_keeps_env_refs_expandable() -> None:
    run = _pick_run(benchmark="gpqa_diamond", harness="direct_llm", model="qwen35_27b")
    command = rc.render_run_command(run)

    assert 'agent.config.api_base=$QWEN_VLLM_API_BASE' in command
    assert 'agent.config.api_key=$QWEN_VLLM_API_KEY' in command
    assert "'$QWEN_VLLM_API_BASE'" not in command
    assert "'$QWEN_VLLM_API_KEY'" not in command
    assert " \\\n+  " not in command
    assert "tee logs/full_20260419_gpqa_diamond_direct_llm_qwen35_27b.log" in command


def test_render_official_tb2_command_keeps_repo_logs_and_env_refs_expandable() -> None:
    tb2_run = _pick_run(benchmark="terminal_bench2", harness="direct_llm", model="qwen35_27b")
    tb2_command = rc.render_run_command(tb2_run)

    assert 'repo_root="$PWD"' in tb2_command
    assert '--ak api_base=$QWEN_VLLM_API_BASE' in tb2_command
    assert '"$repo_root/logs/full_20260419_terminal_bench2_direct_llm_qwen35_27b.log"' in tb2_command
    assert "'$QWEN_VLLM_API_BASE'" not in tb2_command


def test_preflight_uses_explicit_manifest_path(monkeypatch) -> None:
    monkeypatch.setattr(rc, "DEFAULT_MANIFEST_PATH", Path("/tmp/manifest-should-not-be-used.yaml"))

    results = rc.preflight_campaign(MANIFEST_PATH, validate_configs=False)

    assert results["manifest"][0].ok is True
    assert results["manifest"][0].detail == str(MANIFEST_PATH)


@pytest.mark.parametrize("config_path", NEW_FULL_CONFIGS, ids=[path.stem for path in NEW_FULL_CONFIGS])
def test_new_full_configs_load_and_validate_with_env(monkeypatch, config_path) -> None:
    _set_rollout_env(monkeypatch)
    _mock_successful_docker_image_inspect(monkeypatch)

    config = ExperimentConfig.from_yaml(str(config_path))
    validated = ConfigValidator().load_and_validate(str(config_path))

    assert config.run_id.startswith("rollout_full_")
    assert validated.run_id.startswith("rollout_full_")


def test_rollout_summary_cli_emits_matrix(capsys) -> None:
    exit_code = rollout_cli_main(["summary", "--manifest", str(MANIFEST_PATH)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Selected runs: 60" in captured.out
    assert "wave_a_mainline: 46" in captured.out
    assert "full_20260419_gpqa_diamond_direct_llm_qwen35_27b" in captured.out


def test_rollout_materialize_cli_writes_scripts(tmp_path) -> None:
    exit_code = rollout_cli_main(
        [
            "materialize",
            "--manifest",
            str(MANIFEST_PATH),
            "--benchmark",
            "gpqa_diamond",
            "--model",
            "qwen35_27b",
            "--kind",
            "both",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "validate__full_20260419_gpqa_diamond_direct_llm_qwen35_27b.sh").exists()
    assert (tmp_path / "run__full_20260419_gpqa_diamond_direct_llm_qwen35_27b.sh").exists()
