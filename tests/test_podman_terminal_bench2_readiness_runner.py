from __future__ import annotations

import subprocess
from pathlib import Path


RUNNER = Path("scripts/run_podman_terminal_bench2_readiness.sh")
PREFLIGHT = Path("scripts/preflight_podman_terminal_bench2_readiness.py")
AUDIT = Path("scripts/audit_podman_terminal_bench2_readiness.py")


def test_runner_shell_syntax_is_valid() -> None:
    result = subprocess.run(["bash", "-n", str(RUNNER)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_python_helpers_compile() -> None:
    result = subprocess.run(
        ["python", "-m", "py_compile", str(PREFLIGHT), str(AUDIT)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_runner_has_required_modes_and_single_config() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    for mode in ("validate", "preflight", "pilot", "audit"):
        assert f"{mode})" in script
    assert "terminal_bench2_opencode_pilot.yaml" in script
    assert "openclaw" not in script.lower()
    assert "zeroclaw" not in script.lower()
    assert "direct_llm" not in script.lower()


def test_runner_blocks_pilot_on_preflight() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    pilot_body = script.split("run_pilot() {", 1)[1].split("\n}", 1)[0]
    assert "validate_config || exit" in pilot_body
    assert "run_preflight || exit" in pilot_body
    assert pilot_body.index("run_preflight || exit") < pilot_body.index("run_config")


def test_runner_writes_expected_logs_status_and_audit_paths() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    assert "context/podman-terminal-bench2-readiness" in script
    assert "logs/${run_id}.log" in script
    assert "results/${run_id}" in script
    assert "run-status-${RUN_PREFIX}.tsv" in script
    assert "preflight-${RUN_PREFIX}.json" in script
    assert "audit_podman_terminal_bench2_readiness.py" in script
    assert '*) export ALPHADIANA_TB2_LOGS_DIR="$ROOT_DIR/$ALPHADIANA_TB2_LOGS_DIR" ;;' in script


def test_preflight_probes_provider_from_podman_without_secret_output() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert '"podman"' in source
    assert '"run"' in source
    assert "/models" in source
    assert "OPENAI_BASE_URL" in source
    assert "OPENAI_API_KEY" in source
    assert "OPENAI_MODEL_NAME" in source
    assert '"base_url": _sanitize_url' in source
    assert '"OPENAI_API_KEY"' not in source.split('"provider":', 1)[-1]
