from __future__ import annotations

import subprocess
from pathlib import Path


RUNNER = Path("scripts/run_podman_mmmu_pro_readiness.sh")
PREFLIGHT = Path("scripts/podman_vlm_image_preflight.py")

EXCLUDED_NAMES = {
    "swe_bench",
    "swebench",
    "terminal_bench2",
    "moltbench",
    "aime",
    "gpqa",
    "imo_answerbench",
}


def test_runner_shell_syntax_is_valid() -> None:
    result = subprocess.run(["bash", "-n", str(RUNNER)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_preflight_helper_compiles() -> None:
    result = subprocess.run(["python", "-m", "py_compile", str(PREFLIGHT)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_runner_has_required_modes_and_agent_matrix() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    for mode in ("validate", "preflight", "pilot", "audit", "full"):
        assert f"{mode})" in script
    assert "openclaw zeroclaw opencode" in script
    assert "_mmmu_pro_pilot.yaml" in script
    assert "${RUN_PREFIX}_${agent}_mmmu_pro" in script


def test_runner_blocks_pilot_on_podman_image_preflight() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    assert "podman run --rm --network" in script
    assert "scripts/podman_vlm_image_preflight.py" in script
    assert "preflight_provider_from_podman || exit" in script
    pilot_body = script.split("run_pilot() {", 1)[1].split("\n}", 1)[0]
    assert pilot_body.index("preflight_provider_from_podman || exit") < pilot_body.index("run_config")


def test_runner_writes_expected_logs_and_status_paths() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    assert "context/podman-mmmu-pro-readiness" in script
    assert "logs/${run_id}.log" in script
    assert "results/${run_id}" in script
    assert "run-status-${RUN_PREFIX}.tsv" in script
    assert "preflight-${RUN_PREFIX}.json" in script


def test_runner_avoids_excluded_benchmark_names() -> None:
    script = RUNNER.read_text(encoding="utf-8").lower()
    for excluded in EXCLUDED_NAMES:
        assert excluded not in script


def test_preflight_helper_uses_image_chat_without_secret_output() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert "/models" in source
    assert "/chat/completions" in source
    assert "image_url" in source
    assert "response_non_empty" in source
    assert "OPENAI_BASE_URL" in source
    assert "OPENAI_API_KEY" in source
    assert "OPENAI_MODEL_NAME" in source
    assert "Authorization.*print" not in source
    assert "OPENAI_API_KEY.*print" not in source
    assert "data:image/png;base64.*print" not in source
